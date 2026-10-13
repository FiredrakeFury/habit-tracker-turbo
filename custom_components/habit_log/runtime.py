"""Runtime state for a loaded Habit Log config entry."""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from datetime import date, datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from homeassistant.auth.models import User
from homeassistant.core import CALLBACK_TYPE, Context, HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError, Unauthorized
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ALLOWED_USERS,
    CONF_CALENDAR_DETAILS,
    CONF_WEEK_START,
    DEFAULT_OPTIONS,
    DOMAIN,
    FIELD_BOOLEAN,
    FIELD_NUMBER,
    FIELD_RATING,
    FIELD_SELECT,
    FIELD_TEXT,
    MAX_TEXT_VALUE_LENGTH,
    PERIOD_DAY,
    SIGNAL_CHANGED,
    SUBENTRY_HABIT,
    SUBENTRY_JOURNAL_FIELD,
    VERSION,
)
from .models import Habit, JournalField, JournalValue
from .stats import HabitIndex, build_index, since_reset, summarize
from .store import HabitLogStore

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)


def validation_error(key: str, **placeholders: Any) -> ServiceValidationError:
    """Build a translated validation error."""
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders={k: str(v) for k, v in placeholders.items()},
    )


class HabitLogRuntime:
    """Definitions, data and derived statistics for one config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, store: HabitLogStore) -> None:
        """Initialize from the config entry's subentries and options."""
        self.hass = hass
        self.entry = entry
        self.store = store
        self.options: dict[str, Any] = {**DEFAULT_OPTIONS, **entry.options}
        self.habits: dict[str, Habit] = {}
        self.fields: dict[str, JournalField] = {}
        for subentry in entry.subentries.values():
            if subentry.subentry_type == SUBENTRY_HABIT:
                self.habits[subentry.subentry_id] = Habit.from_subentry(subentry)
            elif subentry.subentry_type == SUBENTRY_JOURNAL_FIELD:
                self.fields[subentry.subentry_id] = JournalField.from_subentry(subentry)
        self._index_cache: dict[str, tuple[int, str, HabitIndex]] = {}
        self._reset_cache: dict[str, tuple[tuple[int, str], dict[str, Any]]] = {}
        self._listeners: list[Callable[[], None]] = []
        self._unsubs: list[CALLBACK_TYPE] = []

    # ------------------------------------------------------------ lifecycle
    @callback
    def async_start(self) -> None:
        """Start listening for data changes and day rollover."""
        self._unsubs.append(self.store.async_add_listener(self._notify))
        self._unsubs.append(
            async_track_time_change(self.hass, self._handle_midnight, hour=0, minute=0, second=1)
        )

    @callback
    def async_stop(self) -> None:
        """Stop listeners."""
        while self._unsubs:
            self._unsubs.pop()()
        self._listeners.clear()

    @callback
    def _handle_midnight(self, _now: datetime) -> None:
        self._index_cache.clear()
        self._reset_cache.clear()
        self._notify()

    @callback
    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()
        async_dispatcher_send(self.hass, SIGNAL_CHANGED)

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        """Register for 'something changed' callbacks."""
        self._listeners.append(listener)

        @callback
        def _remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _remove

    # ----------------------------------------------------------------- time
    @property
    def tz(self) -> ZoneInfo:
        """Return the Home Assistant time zone."""
        return dt_util.get_default_time_zone()

    def today(self) -> date:
        """Return today's local date."""
        return dt_util.now(self.tz).date()

    def to_utc(self, value: datetime) -> datetime:
        """Interpret naive datetimes as local time and convert to UTC."""
        if value.tzinfo is None:
            value = value.replace(tzinfo=self.tz)
        return dt_util.as_utc(value)

    @property
    def week_start(self) -> str:
        """Return configured first day of week."""
        return self.options[CONF_WEEK_START]

    @property
    def calendar_details(self) -> bool:
        """Return whether calendar events include notes/values."""
        return bool(self.options[CONF_CALENDAR_DETAILS])

    # ----------------------------------------------------------------- auth
    @property
    def allowed_users(self) -> list[str]:
        """Return the allowed user IDs (empty = everyone)."""
        return list(self.options.get(CONF_ALLOWED_USERS) or [])

    def user_allowed(self, user_id: str | None) -> bool:
        """Return True if the user may read/write. None = internal (automation)."""
        allowed = self.allowed_users
        return not allowed or user_id is None or user_id in allowed

    def check_context(self, context: Context) -> None:
        """Raise Unauthorized if the calling user is not allowed."""
        if not self.user_allowed(context.user_id):
            _LOGGER.warning("Denied %s action for user %s", DOMAIN, context.user_id)
            raise Unauthorized(context=context)

    def check_user(self, user: User) -> None:
        """Raise Unauthorized for a websocket user that is not allowed."""
        if not self.user_allowed(user.id):
            raise Unauthorized(user_id=user.id)

    # --------------------------------------------------------------- habits
    def active_habits(self) -> list[Habit]:
        """Return non-archived habits in display order."""
        return sorted(
            (h for h in self.habits.values() if not h.archived),
            key=lambda h: (h.order, h.name.lower()),
        )

    def resolve_habit(self, value: str) -> Habit:
        """Resolve a habit from a habit (subentry) ID or a device ID."""
        if value in self.habits:
            return self.habits[value]
        device = dr.async_get(self.hass).async_get(value)
        if device is not None:
            for domain, identifier in device.identifiers:
                if domain == DOMAIN and identifier in self.habits:
                    return self.habits[identifier]
        raise validation_error("habit_not_found", habit=value)

    def index(self, habit_id: str) -> HabitIndex:
        """Return cached aggregates for a habit."""
        tz_key = str(self.tz)
        cached = self._index_cache.get(habit_id)
        if cached and cached[0] == self.store.data_version and cached[1] == tz_key:
            return cached[2]
        index = build_index(self.store.entries_for_habit(habit_id), self.tz)
        self._index_cache[habit_id] = (self.store.data_version, tz_key, index)
        return index

    def since_reset(self, habit_id: str) -> dict[str, Any]:
        """Totals since the habit's latest reset (all time if never reset)."""
        key = (self.store.data_version, str(self.tz))
        cached = self._reset_cache.get(habit_id)
        if cached and cached[0] == key:
            return cached[1]
        result = since_reset(self.store.entries_for_habit(habit_id), self.store.last_reset(habit_id), self.tz)
        result["resets"] = len(self.store.resets.get(habit_id, []))
        index = self.index(habit_id)
        result["all_time"] = {
            "count": index.entry_count,
            "seconds": round(sum(index.seconds_by_day.values()), 3),
            "active_days": len(index.active_days),
        }
        self._reset_cache[habit_id] = (key, result)
        return result

    def summary(self, habit_id: str, period: str, anchor: date | None = None) -> dict[str, Any]:
        """Summarize a habit for a period containing `anchor` (default today)."""
        today = self.today()
        return summarize(self.index(habit_id), period, anchor or today, self.week_start, today)

    # -------------------------------------------------------------- journal
    def active_fields(self) -> list[JournalField]:
        """Return active journal fields in display order."""
        return sorted(
            (f for f in self.fields.values() if f.active),
            key=lambda f: (f.order, f.name.lower()),
        )

    def resolve_field(self, key: str) -> JournalField:
        """Resolve a journal field by ID or (case-insensitive) name."""
        if key in self.fields:
            return self.fields[key]
        lowered = key.strip().lower()
        for journal_field in self.fields.values():
            if journal_field.name.lower() == lowered:
                return journal_field
        raise validation_error("journal_unknown_field", field=key)

    def build_journal_value(self, journal_field: JournalField, raw: Any) -> JournalValue | None:
        """Validate a raw value against its field. Returns None for empty input."""
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return None
        value: Any
        try:
            if journal_field.field_type == FIELD_NUMBER:
                value = float(raw)
                if not math.isfinite(value):
                    raise ValueError
                if journal_field.min is not None and value < journal_field.min:
                    raise ValueError
                if journal_field.max is not None and value > journal_field.max:
                    raise ValueError
                if value.is_integer():
                    value = int(value)
            elif journal_field.field_type == FIELD_RATING:
                if isinstance(raw, bool):
                    raise ValueError
                as_float = float(raw)
                if not as_float.is_integer():
                    raise ValueError
                value = int(as_float)
                if not 1 <= value <= int(journal_field.max or 5):
                    raise ValueError
            elif journal_field.field_type == FIELD_SELECT:
                value = str(raw)
                if value not in journal_field.options:
                    raise ValueError
            elif journal_field.field_type == FIELD_BOOLEAN:
                if isinstance(raw, bool):
                    value = raw
                elif str(raw).strip().lower() in ("true", "on", "yes", "1"):
                    value = True
                elif str(raw).strip().lower() in ("false", "off", "no", "0"):
                    value = False
                else:
                    raise ValueError
            elif journal_field.field_type == FIELD_TEXT:
                value = str(raw).strip()
                if len(value) > MAX_TEXT_VALUE_LENGTH:
                    raise ValueError
            else:  # pragma: no cover - guarded by config flow
                raise ValueError
        except (TypeError, ValueError) as err:
            raise validation_error("journal_invalid_value", field=journal_field.name, value=raw) from err
        return JournalValue(
            field_id=journal_field.id,
            label=journal_field.name,
            field_type=journal_field.field_type,
            unit=journal_field.unit,
            value=value,
        )

    def latest_value(self, field_id: str) -> tuple[Any, date] | None:
        """Return the most recent recorded value for a field and its day."""
        best: tuple[date, datetime, Any] | None = None
        for journal_entry in self.store.journal.values():
            for item in journal_entry.values:
                if item.field_id != field_id:
                    continue
                key = (journal_entry.day, journal_entry.created)
                if best is None or key > (best[0], best[1]):
                    best = (journal_entry.day, journal_entry.created, item.value)
        if best is None:
            return None
        return best[2], best[0]

    # ------------------------------------------------------------ websocket
    def config_payload(self) -> dict[str, Any]:
        """Return definitions for the frontend."""
        return {
            "habits": [
                {**h.as_dict(), "last_reset": (r.isoformat() if (r := self.store.last_reset(h.id)) else None)}
                for h in sorted(self.habits.values(), key=lambda h: (h.order, h.name.lower()))
            ],
            "journal_fields": [
                f.as_dict() for f in sorted(self.fields.values(), key=lambda f: (f.order, f.name.lower()))
            ],
            "week_start": self.week_start,
            "time_zone": str(self.tz),
            "today": self.today().isoformat(),
            "now_local": dt_util.now(self.tz).replace(second=0, microsecond=0, tzinfo=None).isoformat(),
            "today_summary": {
                h.id: {
                    "count": (day := self.summary(h.id, PERIOD_DAY))["count"],
                    "seconds": day["seconds"],
                    "active": day["active_days"] > 0,
                }
                for h in self.habits.values()
                if not h.archived
            },
            "version": VERSION,
        }
