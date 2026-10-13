"""Persistent storage for Habit Log entries and journal entries."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import SAVE_DELAY, STORAGE_KEY, STORAGE_MINOR_VERSION, STORAGE_VERSION
from .models import Entry, JournalEntry

_LOGGER = logging.getLogger(__name__)


class _HabitLogStore(Store[dict[str, Any]]):
    """Store with a migration hook for future schema changes."""

    async def _async_migrate_func(
        self,
        old_major_version: int,
        old_minor_version: int,
        old_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Migrate stored data. No migrations exist yet."""
        if old_major_version > STORAGE_VERSION:
            raise NotImplementedError("Data was written by a newer version of Habit Log")
        return old_data


class HabitLogStore:
    """In-memory model backed by a Home Assistant Store (/config/.storage)."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize."""
        self._store = _HabitLogStore(
            hass,
            STORAGE_VERSION,
            STORAGE_KEY,
            private=True,
            atomic_writes=True,
            minor_version=STORAGE_MINOR_VERSION,
        )
        self.entries: dict[str, Entry] = {}
        self.journal: dict[str, JournalEntry] = {}
        # Last-known habit definitions, used to label snapshots of deleted habits.
        self.habit_meta: dict[str, dict[str, Any]] = {}
        # Counter resets per habit (aware UTC datetimes, ascending). Entries are
        # never touched by a reset; "since reset" totals start at the latest one.
        self.resets: dict[str, list[datetime]] = {}
        self.data_version = 0
        self._listeners: list[Callable[[], None]] = []

    async def async_load(self) -> None:
        """Load data from disk."""
        data = await self._store.async_load() or {}
        self.entries = {}
        for raw in data.get("entries", []):
            try:
                entry = Entry.from_dict(raw)
            except (KeyError, TypeError, ValueError) as err:
                _LOGGER.error("Skipping unreadable entry %s: %s", raw.get("id"), err)
                continue
            self.entries[entry.id] = entry
        self.journal = {}
        for raw in data.get("journal", []):
            try:
                journal_entry = JournalEntry.from_dict(raw)
            except (KeyError, TypeError, ValueError) as err:
                _LOGGER.error("Skipping unreadable journal entry %s: %s", raw.get("id"), err)
                continue
            self.journal[journal_entry.id] = journal_entry
        self.habit_meta = dict(data.get("habit_meta", {}))
        self.resets = {}
        for habit_id, stamps in (data.get("resets") or {}).items():
            parsed = []
            for stamp in stamps or []:
                value = dt_util.parse_datetime(str(stamp))
                if value is None:
                    _LOGGER.error("Skipping unreadable reset %s for %s", stamp, habit_id)
                    continue
                parsed.append(dt_util.as_utc(value if value.tzinfo else value.replace(tzinfo=dt_util.UTC)))
            if parsed:
                self.resets[habit_id] = sorted(parsed)
        self.data_version += 1

    @callback
    def _serialize(self) -> dict[str, Any]:
        return {
            "entries": [e.as_dict() for e in self.entries.values()],
            "journal": [j.as_dict() for j in self.journal.values()],
            "habit_meta": self.habit_meta,
            "resets": {h: [r.isoformat() for r in stamps] for h, stamps in self.resets.items() if stamps},
        }

    @callback
    def _changed(self) -> None:
        """Persist and notify listeners."""
        self.data_version += 1
        self._store.async_delay_save(self._serialize, SAVE_DELAY)
        for listener in list(self._listeners):
            listener()

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        """Register a change listener."""
        self._listeners.append(listener)

        @callback
        def _remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _remove

    async def async_remove(self) -> None:
        """Delete the storage file."""
        await self._store.async_remove()

    async def async_flush(self) -> None:
        """Write pending changes immediately."""
        await self._store.async_save(self._serialize())

    # ------------------------------------------------------------------ meta
    @callback
    def set_habit_meta(self, meta: dict[str, dict[str, Any]]) -> None:
        """Remember current habit definitions (persisted only on change)."""
        if meta != self.habit_meta:
            self.habit_meta = meta
            self._store.async_delay_save(self._serialize, SAVE_DELAY)

    # --------------------------------------------------------------- entries
    def entries_for_habit(self, habit_id: str) -> list[Entry]:
        """Return a habit's entries."""
        return [e for e in self.entries.values() if e.habit_id == habit_id]

    def habit_has_day(self, habit_id: str, day: date, tz: ZoneInfo, exclude: str | None = None) -> bool:
        """Return True if the habit already has an entry counting on `day`."""
        return any(
            e.habit_id == habit_id and e.id != exclude and e.count_day(tz) == day
            for e in self.entries.values()
        )

    def habit_has_timed_entries(self, habit_id: str) -> bool:
        """Return True if the habit has any time-tracked entries."""
        return any(e.habit_id == habit_id and e.is_timed for e in self.entries.values())

    @callback
    def add_entry(self, entry: Entry) -> None:
        """Add an entry."""
        self.entries[entry.id] = entry
        self._changed()

    @callback
    def add_entries(self, entries: list[Entry]) -> None:
        """Add many entries with one save/notify."""
        if not entries:
            return
        for entry in entries:
            self.entries[entry.id] = entry
        self._changed()

    @callback
    def replace_entry(self, entry: Entry) -> None:
        """Replace an existing entry."""
        if entry.id not in self.entries:
            raise KeyError(entry.id)
        self.entries[entry.id] = entry
        self._changed()

    @callback
    def remove_any(self, item_id: str) -> Entry | JournalEntry:
        """Remove an entry or journal entry by ID."""
        if item_id in self.entries:
            removed: Entry | JournalEntry = self.entries.pop(item_id)
        elif item_id in self.journal:
            removed = self.journal.pop(item_id)
        else:
            raise KeyError(item_id)
        self._changed()
        return removed

    @callback
    def remove_habit_entries(self, habit_id: str) -> list[Entry]:
        """Remove and return all entries of a habit."""
        removed = [e for e in self.entries.values() if e.habit_id == habit_id]
        if removed:
            for entry in removed:
                del self.entries[entry.id]
            self._changed()
        return removed

    @callback
    def reassign(self, source_id: str, target_id: str, dedupe_days: bool, tz: ZoneInfo) -> tuple[int, int]:
        """Move entries from one habit to another.

        With dedupe_days (streak targets), a source entry on a day the target
        already has is dropped. Returns (moved, dropped).
        """
        target_days = {e.count_day(tz) for e in self.entries.values() if e.habit_id == target_id}
        moved = dropped = 0
        for entry in [e for e in self.entries.values() if e.habit_id == source_id]:
            day = entry.count_day(tz)
            if dedupe_days and day in target_days:
                del self.entries[entry.id]
                dropped += 1
                continue
            entry.habit_id = target_id
            target_days.add(day)
            moved += 1
        if moved or dropped:
            self._changed()
        return moved, dropped

    # ---------------------------------------------------------------- resets
    def last_reset(self, habit_id: str) -> datetime | None:
        """Return the latest reset of a habit, if any."""
        stamps = self.resets.get(habit_id)
        return stamps[-1] if stamps else None

    @callback
    def add_reset(self, habit_id: str, at: datetime) -> None:
        """Record a reset (kept in ascending order)."""
        stamps = self.resets.setdefault(habit_id, [])
        stamps.append(at)
        stamps.sort()
        self._changed()

    @callback
    def remove_last_reset(self, habit_id: str) -> datetime | None:
        """Undo the latest reset; return it (None if there was none)."""
        stamps = self.resets.get(habit_id)
        if not stamps:
            return None
        removed = stamps.pop()
        if not stamps:
            del self.resets[habit_id]
        self._changed()
        return removed

    @callback
    def drop_resets(self, habit_id: str) -> list[datetime]:
        """Forget all resets of a habit (deleted or merged away)."""
        removed = self.resets.pop(habit_id, [])
        if removed:
            self._changed()
        return removed

    # --------------------------------------------------------------- journal
    @callback
    def add_journal(self, journal_entry: JournalEntry) -> None:
        """Add a journal entry."""
        self.journal[journal_entry.id] = journal_entry
        self._changed()

    @callback
    def replace_journal(self, journal_entry: JournalEntry) -> None:
        """Replace a journal entry."""
        if journal_entry.id not in self.journal:
            raise KeyError(journal_entry.id)
        self.journal[journal_entry.id] = journal_entry
        self._changed()
