"""Diagnostics for Habit Log (counts only; no notes or journal values)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant

if TYPE_CHECKING:
    from . import HabitLogConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: HabitLogConfigEntry
) -> dict[str, Any]:
    """Return non-sensitive diagnostics."""
    runtime = entry.runtime_data
    store = runtime.store
    return {
        "options": {
            "allowed_users_count": len(runtime.allowed_users),
            "week_start": runtime.week_start,
            "calendar_details": runtime.calendar_details,
        },
        "habits": [
            {
                "kind": h.kind,
                "track_time": h.track_time,
                "archived": h.archived,
                "entries": len(store.entries_for_habit(h.id)),
            }
            for h in runtime.habits.values()
        ],
        "journal_fields": [
            {"field_type": f.field_type, "active": f.active, "create_sensor": f.create_sensor}
            for f in runtime.fields.values()
        ],
        "totals": {
            "entries": len(store.entries),
            "journal_entries": len(store.journal),
            "orphaned_entries": sum(1 for e in store.entries.values() if e.habit_id not in runtime.habits),
        },
        "time_zone": str(runtime.tz),
    }
