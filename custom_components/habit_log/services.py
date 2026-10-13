"""Actions (services) for Habit Log."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util
from homeassistant.util.ulid import ulid_now

from .const import (
    ATTR_AT,
    ATTR_DATE,
    ATTR_DATES,
    ATTR_DURATION,
    ATTR_END,
    ATTR_END_DATE,
    ATTR_ENTRY_ID,
    ATTR_HABIT,
    ATTR_NOTES,
    ATTR_SOURCE,
    ATTR_START,
    ATTR_START_DATE,
    ATTR_TARGET,
    ATTR_VALUES,
    CONF_ARCHIVED,
    DOMAIN,
    FUTURE_TOLERANCE,
    MAX_BACKFILL_DAYS,
    MAX_NOTES_LENGTH,
    MAX_TIMED_DURATION,
    SERVICE_BACKFILL,
    SERVICE_DELETE_ENTRY,
    SERVICE_JOURNAL,
    SERVICE_LIST_ENTRIES,
    SERVICE_LOG,
    SERVICE_MERGE_HABITS,
    SERVICE_RESET_COUNTER,
    SERVICE_UNDO_RESET,
    SERVICE_UPDATE_ENTRY,
    SERVICE_UPDATE_JOURNAL,
)
from .models import Entry, Habit, JournalEntry, JournalValue
from .runtime import HabitLogRuntime, validation_error
from .stats import entry_days

if TYPE_CHECKING:
    from . import HabitLogConfigEntry

_LOGGER = logging.getLogger(__name__)
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

NOTES = vol.All(cv.string, vol.Length(max=MAX_NOTES_LENGTH))

LOG_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_HABIT): cv.string,
        vol.Optional(ATTR_DATE): cv.date,
        vol.Optional(ATTR_START): cv.datetime,
        vol.Optional(ATTR_END): cv.datetime,
        vol.Optional(ATTR_DURATION): cv.positive_time_period,
        vol.Optional(ATTR_NOTES): vol.Any(None, NOTES),
    }
)
BACKFILL_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_HABIT): cv.string,
        vol.Optional(ATTR_START_DATE): cv.date,
        vol.Optional(ATTR_END_DATE): cv.date,
        vol.Optional(ATTR_DATES): vol.Any(cv.string, [cv.string]),
    }
)
UPDATE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTRY_ID): cv.string,
        vol.Optional(ATTR_HABIT): cv.string,
        vol.Optional(ATTR_DATE): cv.date,
        vol.Optional(ATTR_START): cv.datetime,
        vol.Optional(ATTR_END): cv.datetime,
        vol.Optional(ATTR_DURATION): cv.positive_time_period,
        vol.Optional(ATTR_NOTES): vol.Any(None, NOTES),
    }
)
DELETE_SCHEMA = vol.Schema({vol.Required(ATTR_ENTRY_ID): cv.string})
MERGE_SCHEMA = vol.Schema({vol.Required(ATTR_SOURCE): cv.string, vol.Required(ATTR_TARGET): cv.string})
LIST_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_HABIT): cv.string,
        vol.Optional(ATTR_START_DATE): cv.date,
        vol.Optional(ATTR_END_DATE): cv.date,
    }
)
JOURNAL_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DATE): cv.date,
        vol.Required(ATTR_VALUES): vol.All(dict, vol.Length(min=1, max=100)),
    }
)
RESET_SCHEMA = vol.Schema({vol.Required(ATTR_HABIT): cv.string, vol.Optional(ATTR_AT): cv.datetime})
UNDO_RESET_SCHEMA = vol.Schema({vol.Required(ATTR_HABIT): cv.string})
UPDATE_JOURNAL_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTRY_ID): cv.string,
        vol.Optional(ATTR_DATE): cv.date,
        vol.Optional(ATTR_VALUES): vol.All(dict, vol.Length(max=100)),
    }
)


def _runtime(hass: HomeAssistant) -> HabitLogRuntime:
    entries: list[HabitLogConfigEntry] = hass.config_entries.async_entries(DOMAIN)
    for entry in entries:
        if entry.state is ConfigEntryState.LOADED:
            return entry.runtime_data
    raise validation_error("not_loaded")


def _check_day(runtime: HabitLogRuntime, day: date) -> None:
    if day > runtime.today():
        raise validation_error("future_date", date=day.isoformat())


def _resolve_period(
    runtime: HabitLogRuntime,
    start: datetime | None,
    end: datetime | None,
    duration: timedelta | None,
) -> tuple[datetime, datetime]:
    """Validate and normalize a time period to aware UTC datetimes."""
    if start is None or (end is None and duration is None):
        raise validation_error("time_required")
    if end is not None and duration is not None:
        raise validation_error("end_and_duration")
    start_utc = runtime.to_utc(start)
    end_utc = runtime.to_utc(end) if end is not None else start_utc + duration  # type: ignore[operator]
    if end_utc <= start_utc:
        raise validation_error("end_before_start")
    if end_utc - start_utc > MAX_TIMED_DURATION:
        raise validation_error("duration_too_long", days=MAX_TIMED_DURATION.days)
    if end_utc > dt_util.utcnow() + FUTURE_TOLERANCE:
        raise validation_error("future_date", date=dt_util.as_local(end_utc).isoformat())
    return start_utc, end_utc


def _active_habit(runtime: HabitLogRuntime, key: str) -> Habit:
    habit = runtime.resolve_habit(key)
    if habit.archived:
        raise validation_error("habit_archived", habit=habit.name)
    return habit


def _entry_payload(runtime: HabitLogRuntime, entry: Entry) -> dict[str, Any]:
    habit = runtime.habits.get(entry.habit_id)
    payload = entry.as_dict()
    payload["habit_name"] = habit.name if habit else None
    payload["count_day"] = entry.count_day(runtime.tz).isoformat()
    payload["days"] = [d.isoformat() for d in entry_days(entry, runtime.tz)]
    if entry.is_timed:
        assert entry.start is not None and entry.end is not None
        payload["start_local"] = entry.start.astimezone(runtime.tz).replace(tzinfo=None).isoformat()
        payload["end_local"] = entry.end.astimezone(runtime.tz).replace(tzinfo=None).isoformat()
        payload["seconds"] = (entry.end - entry.start).total_seconds()
    return payload


def _parse_dates(raw: Any) -> list[date]:
    text = " ".join(raw) if isinstance(raw, list) else str(raw or "")
    out: list[date] = []
    for token in DATE_RE.findall(text):
        try:
            out.append(date.fromisoformat(token))
        except ValueError as err:
            raise validation_error("invalid_date", date=token) from err
    return out


def _build_values(runtime: HabitLogRuntime, raw_values: dict[str, Any]) -> dict[str, JournalValue | None]:
    """Validate journal input keyed by field ID or name. None = remove."""
    result: dict[str, JournalValue | None] = {}
    for key, raw in raw_values.items():
        journal_field = runtime.resolve_field(str(key))
        if not journal_field.active:
            raise validation_error("journal_inactive_field", field=journal_field.name)
        result[journal_field.id] = runtime.build_journal_value(journal_field, raw)
    return result


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register Habit Log actions."""

    async def handle_log(call: ServiceCall) -> ServiceResponse:
        runtime = _runtime(hass)
        runtime.check_context(call.context)
        habit = _active_habit(runtime, call.data[ATTR_HABIT])
        notes = (call.data.get(ATTR_NOTES) or "").strip()
        now = dt_util.utcnow()

        if habit.track_time:
            if ATTR_DATE in call.data:
                raise validation_error("time_required")
            start, end = _resolve_period(
                runtime,
                call.data.get(ATTR_START),
                call.data.get(ATTR_END),
                call.data.get(ATTR_DURATION),
            )
            entry = Entry(ulid_now(), habit.id, None, start, end, notes, now, now, call.context.user_id)
            runtime.store.add_entry(entry)
            return {"entry_id": entry.id, "created": True}

        if any(k in call.data for k in (ATTR_START, ATTR_END, ATTR_DURATION)):
            raise validation_error("time_not_supported", habit=habit.name)
        day = call.data.get(ATTR_DATE) or runtime.today()
        _check_day(runtime, day)
        if habit.is_streak:
            for existing in runtime.store.entries_for_habit(habit.id):
                if existing.count_day(runtime.tz) == day:
                    return {"entry_id": existing.id, "created": False}
        entry = Entry(ulid_now(), habit.id, day, None, None, notes, now, now, call.context.user_id)
        runtime.store.add_entry(entry)
        return {"entry_id": entry.id, "created": True}

    async def handle_backfill(call: ServiceCall) -> ServiceResponse:
        runtime = _runtime(hass)
        runtime.check_context(call.context)
        habit = _active_habit(runtime, call.data[ATTR_HABIT])
        if habit.track_time:
            raise validation_error("backfill_timed", habit=habit.name)

        days: set[date] = set(_parse_dates(call.data.get(ATTR_DATES)))
        start = call.data.get(ATTR_START_DATE)
        end = call.data.get(ATTR_END_DATE) or start
        if start is not None:
            if end < start:
                raise validation_error("end_before_start")
            if (end - start).days >= MAX_BACKFILL_DAYS:
                raise validation_error("backfill_too_many", max=MAX_BACKFILL_DAYS)
            days.update(start + timedelta(days=i) for i in range((end - start).days + 1))
        if not days:
            raise validation_error("backfill_empty")
        if len(days) > MAX_BACKFILL_DAYS:
            raise validation_error("backfill_too_many", max=MAX_BACKFILL_DAYS)
        for day in days:
            _check_day(runtime, day)

        existing = {e.count_day(runtime.tz) for e in runtime.store.entries_for_habit(habit.id)}
        now = dt_util.utcnow()
        new_entries = [
            Entry(ulid_now(), habit.id, day, None, None, "", now, now, call.context.user_id)
            for day in sorted(days - existing)
        ]
        runtime.store.add_entries(new_entries)
        return {"added": len(new_entries), "skipped": len(days) - len(new_entries)}

    async def handle_update(call: ServiceCall) -> ServiceResponse:
        runtime = _runtime(hass)
        runtime.check_context(call.context)
        entry_id = call.data[ATTR_ENTRY_ID]
        current = runtime.store.entries.get(entry_id)
        if current is None:
            raise validation_error("entry_not_found", entry_id=entry_id)
        habit = (
            _active_habit(runtime, call.data[ATTR_HABIT])
            if ATTR_HABIT in call.data
            else runtime.habits.get(current.habit_id)
        )
        if habit is None:
            raise validation_error("habit_not_found", habit=current.habit_id)

        updated = Entry.from_dict(current.as_dict())
        updated.habit_id = habit.id
        if ATTR_NOTES in call.data:
            updated.notes = (call.data[ATTR_NOTES] or "").strip()

        time_keys = [k for k in (ATTR_START, ATTR_END, ATTR_DURATION) if k in call.data]
        if habit.track_time:
            if ATTR_DATE in call.data:
                raise validation_error("time_required")
            if time_keys or not updated.is_timed:
                start = call.data.get(ATTR_START) or (
                    dt_util.as_local(updated.start) if updated.start else None
                )
                end = call.data.get(ATTR_END)
                duration = call.data.get(ATTR_DURATION)
                if end is None and duration is None and updated.end is not None:
                    if ATTR_START in call.data and updated.start is not None:
                        duration = updated.end - updated.start  # keep length when moving start
                    else:
                        end = dt_util.as_local(updated.end)
                updated.start, updated.end = _resolve_period(runtime, start, end, duration)
                updated.day = None
        else:
            if time_keys:
                raise validation_error("time_not_supported", habit=habit.name)
            if updated.is_timed and ATTR_DATE not in call.data:
                raise validation_error("date_required")
            if ATTR_DATE in call.data:
                updated.day = call.data[ATTR_DATE]
                updated.start = updated.end = None
            assert updated.day is not None
            _check_day(runtime, updated.day)
            if habit.is_streak and runtime.store.habit_has_day(
                habit.id, updated.day, runtime.tz, exclude=updated.id
            ):
                raise validation_error("streak_day_exists", habit=habit.name, date=updated.day)

        updated.updated = dt_util.utcnow()
        runtime.store.replace_entry(updated)
        return {"entry": _entry_payload(runtime, updated)}

    async def handle_delete(call: ServiceCall) -> None:
        runtime = _runtime(hass)
        runtime.check_context(call.context)
        try:
            runtime.store.remove_any(call.data[ATTR_ENTRY_ID])
        except KeyError as err:
            raise validation_error("entry_not_found", entry_id=call.data[ATTR_ENTRY_ID]) from err

    async def handle_merge(call: ServiceCall) -> ServiceResponse:
        runtime = _runtime(hass)
        runtime.check_context(call.context)
        source = runtime.resolve_habit(call.data[ATTR_SOURCE])
        target = _active_habit(runtime, call.data[ATTR_TARGET])
        if source.id == target.id:
            raise validation_error("merge_same")
        if runtime.store.habit_has_timed_entries(source.id) and not target.track_time:
            raise validation_error("merge_incompatible", source=source.name, target=target.name)
        moved, dropped = runtime.store.reassign(source.id, target.id, target.is_streak, runtime.tz)
        await runtime.store.async_flush()
        entry = runtime.entry
        subentry = entry.subentries[source.id]
        if not source.archived:
            hass.config_entries.async_update_subentry(
                entry, subentry, data={**subentry.data, CONF_ARCHIVED: True}
            )
        _LOGGER.info("Merged %s into %s: %d moved, %d dropped", source.name, target.name, moved, dropped)
        return {"moved": moved, "dropped_duplicates": dropped, "source_archived": True}

    async def handle_list(call: ServiceCall) -> ServiceResponse:
        runtime = _runtime(hass)
        runtime.check_context(call.context)
        habit_id = runtime.resolve_habit(call.data[ATTR_HABIT]).id if ATTR_HABIT in call.data else None
        start = call.data.get(ATTR_START_DATE)
        end = call.data.get(ATTR_END_DATE)
        if start and end and end < start:
            raise validation_error("end_before_start")

        def in_range(days: list[date]) -> bool:
            return any((start is None or d >= start) and (end is None or d <= end) for d in days)

        entries = [
            _entry_payload(runtime, e)
            for e in sorted(
                runtime.store.entries.values(), key=lambda e: (e.count_day(runtime.tz), e.created)
            )
            if (habit_id is None or e.habit_id == habit_id) and in_range(entry_days(e, runtime.tz))
        ]
        journal = []
        if habit_id is None:
            journal = [
                j.as_dict()
                for j in sorted(runtime.store.journal.values(), key=lambda j: (j.day, j.created))
                if in_range([j.day])
            ]
        return {"entries": entries, "journal": journal}

    async def handle_journal(call: ServiceCall) -> ServiceResponse:
        runtime = _runtime(hass)
        runtime.check_context(call.context)
        day = call.data.get(ATTR_DATE) or runtime.today()
        _check_day(runtime, day)
        values = [v for v in _build_values(runtime, call.data[ATTR_VALUES]).values() if v is not None]
        if not values:
            raise validation_error("journal_empty")
        order = {f.id: (f.order, f.name.lower()) for f in runtime.fields.values()}
        values.sort(key=lambda v: order.get(v.field_id, (0, v.label.lower())))
        now = dt_util.utcnow()
        journal_entry = JournalEntry(ulid_now(), day, values, now, now, call.context.user_id)
        runtime.store.add_journal(journal_entry)
        return {"entry_id": journal_entry.id}

    async def handle_update_journal(call: ServiceCall) -> ServiceResponse:
        runtime = _runtime(hass)
        runtime.check_context(call.context)
        entry_id = call.data[ATTR_ENTRY_ID]
        current = runtime.store.journal.get(entry_id)
        if current is None:
            raise validation_error("entry_not_found", entry_id=entry_id)
        updated = JournalEntry.from_dict(current.as_dict())
        if ATTR_DATE in call.data:
            _check_day(runtime, call.data[ATTR_DATE])
            updated.day = call.data[ATTR_DATE]
        if ATTR_VALUES in call.data:
            by_id = {v.field_id: v for v in updated.values}
            for field_id, value in _build_values(runtime, call.data[ATTR_VALUES]).items():
                if value is None:
                    by_id.pop(field_id, None)
                else:
                    by_id[field_id] = value
            updated.values = [v for v in by_id.values()]
            if not updated.values:
                raise validation_error("journal_empty")
        updated.updated = dt_util.utcnow()
        runtime.store.replace_journal(updated)
        return {"entry": updated.as_dict()}

    async def handle_reset(call: ServiceCall) -> ServiceResponse:
        runtime = _runtime(hass)
        runtime.check_context(call.context)
        habit = _active_habit(runtime, call.data[ATTR_HABIT])
        if habit.is_streak:
            raise validation_error("reset_streak", habit=habit.name)
        at = runtime.to_utc(call.data[ATTR_AT]) if ATTR_AT in call.data else dt_util.utcnow()
        if at > dt_util.utcnow() + FUTURE_TOLERANCE:
            raise validation_error("future_date", date=dt_util.as_local(at).isoformat())
        runtime.store.add_reset(habit.id, at)
        _LOGGER.info("Reset %s at %s", habit.name, at.isoformat())
        return {"reset_at": at.isoformat(), "since_reset": runtime.since_reset(habit.id)}

    async def handle_undo_reset(call: ServiceCall) -> ServiceResponse:
        runtime = _runtime(hass)
        runtime.check_context(call.context)
        habit = runtime.resolve_habit(call.data[ATTR_HABIT])
        removed = runtime.store.remove_last_reset(habit.id)
        if removed is None:
            raise validation_error("no_reset", habit=habit.name)
        last = runtime.store.last_reset(habit.id)
        return {"removed": removed.isoformat(), "last_reset": last.isoformat() if last else None}

    hass.services.async_register(
        DOMAIN,
        SERVICE_RESET_COUNTER,
        handle_reset,
        schema=RESET_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UNDO_RESET,
        handle_undo_reset,
        schema=UNDO_RESET_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_LOG, handle_log, schema=LOG_SCHEMA, supports_response=SupportsResponse.OPTIONAL
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_BACKFILL,
        handle_backfill,
        schema=BACKFILL_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_ENTRY,
        handle_update,
        schema=UPDATE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(DOMAIN, SERVICE_DELETE_ENTRY, handle_delete, schema=DELETE_SCHEMA)
    hass.services.async_register(
        DOMAIN,
        SERVICE_MERGE_HABITS,
        handle_merge,
        schema=MERGE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_LIST_ENTRIES,
        handle_list,
        schema=LIST_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_JOURNAL,
        handle_journal,
        schema=JOURNAL_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_JOURNAL,
        handle_update_journal,
        schema=UPDATE_JOURNAL_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
