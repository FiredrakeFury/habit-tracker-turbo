"""Data model for the Habit Log integration.

Habit and journal-field *definitions* live in config subentries (managed via
the UI). Log *data* (entries and journal entries) lives in the integration's
Store and references definitions by subentry ID, which never changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigSubentry
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ACTIVE,
    CONF_ARCHIVED,
    CONF_COLOR,
    CONF_CREATE_SENSOR,
    CONF_EMOJI,
    CONF_FIELD_TYPE,
    CONF_KIND,
    CONF_MAX,
    CONF_MIN,
    CONF_MULTILINE,
    CONF_OPTIONS,
    CONF_ORDER,
    CONF_STEP,
    CONF_TRACK_TIME,
    CONF_UNIT,
    FIELD_RATING,
    FIELD_TEXT,
    KIND_COUNTER,
    KIND_STREAK,
    NUMERIC_FIELD_TYPES,
)


@dataclass(frozen=True, slots=True)
class Habit:
    """A habit definition (from a config subentry)."""

    id: str
    name: str
    emoji: str
    color: str | None
    kind: str
    track_time: bool
    order: int
    archived: bool

    @property
    def is_streak(self) -> bool:
        """Return True for streak habits."""
        return self.kind == KIND_STREAK

    @property
    def label(self) -> str:
        """Return display label with emoji."""
        return f"{self.emoji} {self.name}".strip()

    @classmethod
    def from_subentry(cls, subentry: ConfigSubentry) -> Habit:
        """Build from a config subentry."""
        data = subentry.data
        kind = data.get(CONF_KIND, KIND_COUNTER)
        return cls(
            id=subentry.subentry_id,
            name=subentry.title,
            emoji=data.get(CONF_EMOJI, "") or "",
            color=data.get(CONF_COLOR),
            kind=kind,
            track_time=bool(data.get(CONF_TRACK_TIME, False)) and kind == KIND_COUNTER,
            order=int(data.get(CONF_ORDER, 0)),
            archived=bool(data.get(CONF_ARCHIVED, False)),
        )

    def as_dict(self) -> dict[str, Any]:
        """Serialize for websocket/snapshots."""
        return {
            "id": self.id,
            "name": self.name,
            "emoji": self.emoji,
            "color": self.color,
            "kind": self.kind,
            "track_time": self.track_time,
            "order": self.order,
            "archived": self.archived,
        }


@dataclass(frozen=True, slots=True)
class JournalField:
    """A journal field definition (from a config subentry)."""

    id: str
    name: str
    field_type: str
    unit: str | None
    min: float | None
    max: float | None
    step: float | None
    options: tuple[str, ...]
    multiline: bool
    create_sensor: bool
    order: int
    active: bool

    @property
    def is_numeric(self) -> bool:
        """Return True if values are numbers."""
        return self.field_type in NUMERIC_FIELD_TYPES

    @classmethod
    def from_subentry(cls, subentry: ConfigSubentry) -> JournalField:
        """Build from a config subentry."""
        data = subentry.data
        field_type = data[CONF_FIELD_TYPE]
        is_numeric = field_type in NUMERIC_FIELD_TYPES
        minimum = data.get(CONF_MIN)
        if field_type == FIELD_RATING:
            minimum = 1
        return cls(
            id=subentry.subentry_id,
            name=subentry.title,
            field_type=field_type,
            unit=(data.get(CONF_UNIT) or None) if is_numeric else None,
            min=float(minimum) if is_numeric and minimum is not None else None,
            max=float(data[CONF_MAX]) if is_numeric and data.get(CONF_MAX) is not None else None,
            step=float(data[CONF_STEP]) if is_numeric and data.get(CONF_STEP) is not None else None,
            options=tuple(data.get(CONF_OPTIONS, ())),
            multiline=bool(data.get(CONF_MULTILINE, False)) and field_type == FIELD_TEXT,
            create_sensor=bool(data.get(CONF_CREATE_SENSOR, False)) and is_numeric,
            order=int(data.get(CONF_ORDER, 0)),
            active=bool(data.get(CONF_ACTIVE, True)),
        )

    def as_dict(self) -> dict[str, Any]:
        """Serialize for websocket."""
        return {
            "id": self.id,
            "name": self.name,
            "field_type": self.field_type,
            "unit": self.unit,
            "min": self.min,
            "max": self.max,
            "step": self.step,
            "options": list(self.options),
            "multiline": self.multiline,
            "create_sensor": self.create_sensor,
            "order": self.order,
            "active": self.active,
        }


@dataclass(slots=True)
class Entry:
    """A logged habit occurrence.

    Date-based entries set `day`. Time-tracked entries set `start`/`end`
    (timezone-aware, stored as UTC); their count day is the local date of
    `start`.
    """

    id: str
    habit_id: str
    day: date | None
    start: datetime | None
    end: datetime | None
    notes: str
    created: datetime
    updated: datetime
    user_id: str | None

    @property
    def is_timed(self) -> bool:
        """Return True if the entry has a start/end."""
        return self.start is not None and self.end is not None

    def count_day(self, tz: ZoneInfo) -> date:
        """Return the local date the entry counts on."""
        if self.start is not None:
            return self.start.astimezone(tz).date()
        assert self.day is not None
        return self.day

    def as_dict(self) -> dict[str, Any]:
        """Serialize for storage and websocket."""
        return {
            "id": self.id,
            "habit_id": self.habit_id,
            "day": self.day.isoformat() if self.day else None,
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat() if self.end else None,
            "notes": self.notes,
            "created": self.created.isoformat(),
            "updated": self.updated.isoformat(),
            "user_id": self.user_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Entry:
        """Deserialize from storage."""
        return cls(
            id=data["id"],
            habit_id=data["habit_id"],
            day=date.fromisoformat(data["day"]) if data.get("day") else None,
            start=_parse_dt(data.get("start")),
            end=_parse_dt(data.get("end")),
            notes=data.get("notes", ""),
            created=_parse_dt(data["created"]) or dt_util.utcnow(),
            updated=_parse_dt(data["updated"]) or dt_util.utcnow(),
            user_id=data.get("user_id"),
        )


@dataclass(slots=True)
class JournalValue:
    """A recorded journal value with a snapshot of its field definition."""

    field_id: str
    label: str
    field_type: str
    unit: str | None
    value: Any

    def as_dict(self) -> dict[str, Any]:
        """Serialize."""
        return {
            "field_id": self.field_id,
            "label": self.label,
            "field_type": self.field_type,
            "unit": self.unit,
            "value": self.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JournalValue:
        """Deserialize."""
        return cls(
            field_id=data["field_id"],
            label=data["label"],
            field_type=data["field_type"],
            unit=data.get("unit"),
            value=data.get("value"),
        )


@dataclass(slots=True)
class JournalEntry:
    """A journal submission for a day."""

    id: str
    day: date
    values: list[JournalValue] = field(default_factory=list)
    created: datetime = field(default_factory=dt_util.utcnow)
    updated: datetime = field(default_factory=dt_util.utcnow)
    user_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Serialize."""
        return {
            "id": self.id,
            "day": self.day.isoformat(),
            "values": [v.as_dict() for v in self.values],
            "created": self.created.isoformat(),
            "updated": self.updated.isoformat(),
            "user_id": self.user_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JournalEntry:
        """Deserialize."""
        return cls(
            id=data["id"],
            day=date.fromisoformat(data["day"]),
            values=[JournalValue.from_dict(v) for v in data.get("values", [])],
            created=_parse_dt(data["created"]) or dt_util.utcnow(),
            updated=_parse_dt(data["updated"]) or dt_util.utcnow(),
            user_id=data.get("user_id"),
        )


def _parse_dt(value: str | None) -> datetime | None:
    """Parse an ISO datetime, returning an aware UTC datetime."""
    if not value:
        return None
    parsed = dt_util.parse_datetime(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.UTC)
    return dt_util.as_utc(parsed)
