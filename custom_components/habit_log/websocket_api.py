"""Websocket commands used by the Habit Log card (read-only; writes use actions)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import DOMAIN, PERIODS, SIGNAL_CHANGED, WS_PREFIX
from .runtime import HabitLogRuntime
from .stats import buckets, entry_days, shift_anchor, summarize

MAX_RANGE_DAYS = 400


def _get_runtime(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg_id: int
) -> HabitLogRuntime | None:
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            runtime: HabitLogRuntime = entry.runtime_data
            try:
                runtime.check_user(connection.user)
            except Unauthorized:
                connection.send_error(msg_id, websocket_api.ERR_UNAUTHORIZED, "Not allowed")
                return None
            return runtime
    connection.send_error(msg_id, websocket_api.ERR_NOT_FOUND, "Habit Log is not loaded")
    return None


@callback
def async_setup_websocket(hass: HomeAssistant) -> None:
    """Register websocket commands."""
    websocket_api.async_register_command(hass, ws_config)
    websocket_api.async_register_command(hass, ws_entries)
    websocket_api.async_register_command(hass, ws_stats)
    websocket_api.async_register_command(hass, ws_subscribe)


@websocket_api.websocket_command({vol.Required("type"): f"{WS_PREFIX}/config"})
@callback
def ws_config(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Return habit and journal field definitions."""
    if runtime := _get_runtime(hass, connection, msg["id"]):
        connection.send_result(msg["id"], runtime.config_payload())


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{WS_PREFIX}/entries",
        vol.Required("start_date"): vol.Coerce(date.fromisoformat),
        vol.Required("end_date"): vol.Coerce(date.fromisoformat),
        vol.Optional("habit_ids"): [str],
        vol.Optional("include_journal", default=True): bool,
    }
)
@callback
def ws_entries(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Return entries touching [start_date, end_date] (inclusive), grouped by day."""
    runtime = _get_runtime(hass, connection, msg["id"])
    if runtime is None:
        return
    start: date = msg["start_date"]
    end: date = msg["end_date"]
    if end < start or (end - start) > timedelta(days=MAX_RANGE_DAYS):
        connection.send_error(msg["id"], websocket_api.ERR_INVALID_FORMAT, "Invalid date range")
        return
    habit_ids = set(msg.get("habit_ids") or [])
    tz = runtime.tz
    entries: list[dict[str, Any]] = []
    for entry in runtime.store.entries.values():
        if habit_ids and entry.habit_id not in habit_ids:
            continue
        days = [d for d in entry_days(entry, tz) if start <= d <= end]
        if not days:
            continue
        payload = entry.as_dict()
        payload["count_day"] = entry.count_day(tz).isoformat()
        payload["days"] = [d.isoformat() for d in entry_days(entry, tz)]
        if entry.start is not None and entry.end is not None:
            payload["start_local"] = entry.start.astimezone(tz).replace(tzinfo=None).isoformat()
            payload["end_local"] = entry.end.astimezone(tz).replace(tzinfo=None).isoformat()
            payload["seconds"] = (entry.end - entry.start).total_seconds()
        entries.append(payload)
    entries.sort(key=lambda e: (e["count_day"], e["start"] or "", e["created"]))
    journal: list[dict[str, Any]] = []
    if msg["include_journal"]:
        journal = [
            j.as_dict()
            for j in sorted(runtime.store.journal.values(), key=lambda j: (j.day, j.created))
            if start <= j.day <= end
        ]
    connection.send_result(msg["id"], {"entries": entries, "journal": journal})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{WS_PREFIX}/stats",
        vol.Required("habit_id"): str,
        vol.Required("period"): vol.In(PERIODS),
        vol.Optional("anchor"): vol.Coerce(date.fromisoformat),
        vol.Optional("offset", default=0): vol.All(int, vol.Range(min=-1200, max=1200)),
    }
)
@callback
def ws_stats(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Return a habit's summary and chart buckets for a period."""
    runtime = _get_runtime(hass, connection, msg["id"])
    if runtime is None:
        return
    habit = runtime.habits.get(msg["habit_id"])
    if habit is None:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "Unknown habit")
        return
    today = runtime.today()
    anchor = shift_anchor(msg["period"], msg.get("anchor") or today, msg["offset"])
    index = runtime.index(habit.id)
    connection.send_result(
        msg["id"],
        {
            "habit_id": habit.id,
            "summary": summarize(index, msg["period"], anchor, runtime.week_start, today),
            "buckets": buckets(index, msg["period"], anchor, runtime.week_start),
            "since_reset": None if habit.is_streak else runtime.since_reset(habit.id),
        },
    )


@websocket_api.websocket_command({vol.Required("type"): f"{WS_PREFIX}/subscribe"})
@callback
def ws_subscribe(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Push a message whenever data, definitions or the date change."""
    if _get_runtime(hass, connection, msg["id"]) is None:
        return

    @callback
    def _forward() -> None:
        connection.send_message(websocket_api.event_message(msg["id"], {"changed": True}))

    # Dispatcher (not the runtime) so the subscription survives entry reloads,
    # which happen whenever a habit or journal field is added or edited.
    connection.subscriptions[msg["id"]] = async_dispatcher_connect(hass, SIGNAL_CHANGED, _forward)
    connection.send_result(msg["id"])
