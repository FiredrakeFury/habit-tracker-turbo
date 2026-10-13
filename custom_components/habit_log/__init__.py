"""Habit Log: local habit, streak, time and journal tracking for Home Assistant."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL, add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from .const import CARD_FILENAME, CARD_URL_BASE, DELETED_SNAPSHOT_DIR, DOMAIN, SIGNAL_CHANGED, VERSION
from .runtime import HabitLogRuntime
from .services import async_setup_services
from .store import HabitLogStore
from .websocket_api import async_setup_websocket

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.CALENDAR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type HabitLogConfigEntry = ConfigEntry[HabitLogRuntime]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register actions, websocket commands and the card once."""
    async_setup_services(hass)
    async_setup_websocket(hass)
    await _async_register_card(hass)
    return True


async def _async_register_card(hass: HomeAssistant) -> None:
    """Serve the card JS and load it on every dashboard."""
    if getattr(hass, "http", None) is None:  # pragma: no cover - http is a dependency
        return
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL_BASE, str(Path(__file__).parent / "www"), cache_headers=False)]
    )
    url = f"{CARD_URL_BASE}/{CARD_FILENAME}?v={VERSION}"
    if DATA_EXTRA_MODULE_URL in hass.data:
        add_extra_js_url(hass, url)
    else:
        _LOGGER.debug("Frontend not loaded; card available at %s", url)


async def async_setup_entry(hass: HomeAssistant, entry: HabitLogConfigEntry) -> bool:
    """Set up Habit Log from a config entry."""
    store = HabitLogStore(hass)
    await store.async_load()
    runtime = HabitLogRuntime(hass, entry, store)

    await _async_purge_deleted_habits(hass, runtime)
    _async_remove_archived_devices(hass, runtime)
    store.set_habit_meta({h.id: h.as_dict() for h in runtime.habits.values()})

    entry.runtime_data = runtime
    runtime.async_start()
    entry.async_on_unload(runtime.async_stop)
    entry.async_on_unload(entry.add_update_listener(_async_reload_on_update))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Definitions may have changed (reload after a subentry edit): tell the card.
    async_dispatcher_send(hass, SIGNAL_CHANGED)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HabitLogConfigEntry) -> bool:
    """Unload a config entry, flushing pending writes first."""
    await entry.runtime_data.store.async_flush()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: HabitLogConfigEntry) -> None:
    """Export everything before the integration is deleted, then remove storage."""
    store = HabitLogStore(hass)
    await store.async_load()
    if store.entries or store.journal:
        payload = {
            "exported": dt_util.utcnow().isoformat(),
            "reason": "integration_removed",
            "habits": store.habit_meta,
            "entries": [e.as_dict() for e in store.entries.values()],
            "journal": [j.as_dict() for j in store.journal.values()],
            "resets": {h: [r.isoformat() for r in stamps] for h, stamps in store.resets.items()},
        }
        path = await hass.async_add_executor_job(_write_snapshot, hass, "full_export", payload)
        _LOGGER.warning("Habit Log removed; all data was exported to %s", path)
    await store.async_remove()


async def _async_reload_on_update(hass: HomeAssistant, entry: HabitLogConfigEntry) -> None:
    """Reload when options or subentries change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def _async_purge_deleted_habits(hass: HomeAssistant, runtime: HabitLogRuntime) -> None:
    """Snapshot and purge entries whose habit subentry was deleted."""
    store = runtime.store
    orphan_ids = ({e.habit_id for e in store.entries.values()} | set(store.resets)) - set(runtime.habits)
    for habit_id in orphan_ids:
        entries = store.entries_for_habit(habit_id)
        meta = store.habit_meta.get(habit_id, {"id": habit_id, "name": habit_id})
        payload = {
            "exported": dt_util.utcnow().isoformat(),
            "reason": "habit_deleted",
            "habit": meta,
            "entries": [e.as_dict() for e in entries],
            "resets": [r.isoformat() for r in store.resets.get(habit_id, [])],
        }
        path = await hass.async_add_executor_job(
            _write_snapshot, hass, slugify(str(meta.get("name", habit_id))) or habit_id, payload
        )
        store.remove_habit_entries(habit_id)
        store.drop_resets(habit_id)
        _LOGGER.warning(
            "Habit '%s' was deleted; %d entries were saved to %s and removed",
            meta.get("name", habit_id),
            len(entries),
            path,
        )
    if orphan_ids:
        await store.async_flush()


def _async_remove_archived_devices(hass: HomeAssistant, runtime: HabitLogRuntime) -> None:
    """Archived habits keep their data but lose their device and entities."""
    dev_reg = dr.async_get(hass)
    for habit in runtime.habits.values():
        if not habit.archived:
            continue
        if device := dev_reg.async_get_device_by_identifier((DOMAIN, habit.id), runtime.entry.entry_id):
            dev_reg.async_remove_device(device.id)


def _write_snapshot(hass: HomeAssistant, name: str, payload: dict[str, Any]) -> str:
    """Write a JSON snapshot under /config/habit_log/deleted (executor)."""
    folder = Path(hass.config.path(DELETED_SNAPSHOT_DIR))
    folder.mkdir(parents=True, exist_ok=True)
    stamp = dt_util.utcnow().strftime("%Y%m%dT%H%M%SZ")
    path = folder / f"{name}_{stamp}.json"
    counter = 1
    while path.exists():
        path = folder / f"{name}_{stamp}_{counter}.json"
        counter += 1
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(path)
    return str(path)
