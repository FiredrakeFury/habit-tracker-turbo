"""Read-only calendar view of Habit Log entries (for the native Calendar panel)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import TITLE
from .models import Entry, JournalEntry
from .runtime import HabitLogRuntime
from .stats import local_midnight

if TYPE_CHECKING:
    from . import HabitLogConfigEntry

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HabitLogConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the calendar entity."""
    async_add_entities([HabitLogCalendar(entry.runtime_data)])


def _format_journal(journal_entry: JournalEntry) -> str:
    lines = []
    for item in journal_entry.values:
        value = item.value
        if isinstance(value, bool):
            value = "Yes" if value else "No"
        unit = f" {item.unit}" if item.unit else ""
        lines.append(f"{item.label}: {value}{unit}")
    return "\n".join(lines)


class HabitLogCalendar(CalendarEntity):
    """All habit and journal entries as calendar events."""

    _attr_should_poll = False
    _attr_has_entity_name = False
    _attr_name = TITLE
    _attr_icon = "mdi:calendar-check"

    def __init__(self, runtime: HabitLogRuntime) -> None:
        """Initialize."""
        self._runtime = runtime
        self._attr_unique_id = f"{runtime.entry.entry_id}_calendar"

    async def async_added_to_hass(self) -> None:
        """Refresh state when data changes."""
        self.async_on_remove(self._runtime.async_add_listener(self._handle_change))

    @callback
    def _handle_change(self) -> None:
        self.async_write_ha_state()

    @property
    def event(self) -> CalendarEvent | None:
        """Return the most recent event of today, if any."""
        today = self._runtime.today()
        start = local_midnight(today, self._runtime.tz)
        events = self._events(start, start + timedelta(days=1))
        return events[-1] if events else None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Return events overlapping [start_date, end_date)."""
        return self._events(start_date, end_date)

    def _events(self, range_start: datetime, range_end: datetime) -> list[CalendarEvent]:
        runtime = self._runtime
        tz = runtime.tz
        details = runtime.calendar_details
        events: list[CalendarEvent] = []

        def all_day_overlaps(day: date) -> bool:
            day_start = local_midnight(day, tz)
            return day_start < range_end and day_start + timedelta(days=1) > range_start

        for entry in runtime.store.entries.values():
            habit = runtime.habits.get(entry.habit_id)
            summary = habit.label if habit else "Habit"
            description = entry.notes if details and entry.notes else None
            if entry.is_timed:
                assert entry.start is not None and entry.end is not None
                if entry.start < range_end and entry.end > range_start:
                    events.append(
                        CalendarEvent(
                            start=dt_util.as_local(entry.start),
                            end=dt_util.as_local(entry.end),
                            summary=summary,
                            description=description,
                            uid=entry.id,
                        )
                    )
            else:
                assert entry.day is not None
                if all_day_overlaps(entry.day):
                    events.append(_all_day(entry, summary, description))

        for journal_entry in runtime.store.journal.values():
            if all_day_overlaps(journal_entry.day):
                events.append(
                    CalendarEvent(
                        start=journal_entry.day,
                        end=journal_entry.day + timedelta(days=1),
                        summary="📝 Journal",
                        description=_format_journal(journal_entry) if details else None,
                        uid=journal_entry.id,
                    )
                )

        events.sort(key=lambda e: (e.start_datetime_local, e.summary))
        return events


def _all_day(entry: Entry, summary: str, description: str | None) -> CalendarEvent:
    assert entry.day is not None
    return CalendarEvent(
        start=entry.day,
        end=entry.day + timedelta(days=1),
        summary=summary,
        description=description,
        uid=entry.id,
    )
