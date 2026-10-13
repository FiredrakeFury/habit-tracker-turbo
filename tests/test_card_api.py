"""Tests for what the card relies on; also records real payloads for card rendering tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.habit_log.const import CARD_FILENAME, CARD_URL_BASE, DOMAIN, VERSION

from .conftest import DEFAULT_SUBENTRIES, habit

FIXTURE_OUT = os.environ.get("HABIT_LOG_FIXTURE_OUT")


@pytest.fixture(autouse=True)
def _freeze(freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to("2026-10-07 14:05:00-04:00")


async def _ws(client: Any, payload: dict[str, Any]) -> dict[str, Any]:
    await client.send_json_auto_id(payload)
    return await client.receive_json()


async def test_card_is_served(hass: HomeAssistant, entry: MockConfigEntry, hass_client: Any) -> None:
    client = await hass_client()
    resp = await client.get(f"{CARD_URL_BASE}/{CARD_FILENAME}")
    assert resp.status == 200
    body = await resp.text()
    assert f'const VERSION = "{VERSION}"' in body
    manifest = json.loads(
        (Path(__file__).parents[1] / "custom_components/habit_log/manifest.json").read_text()
    )
    assert manifest["version"] == VERSION


async def test_config_payload_has_today_summary(
    hass: HomeAssistant, entry: MockConfigEntry, hass_ws_client: WebSocketGenerator
) -> None:
    await hass.services.async_call(DOMAIN, "log", {"habit": "h_run"}, blocking=True)
    await hass.services.async_call(DOMAIN, "log", {"habit": "h_run"}, blocking=True)
    await hass.services.async_call(
        DOMAIN,
        "log",
        {"habit": "h_read", "start": "2026-10-07 08:00:00", "duration": {"minutes": 30}},
        blocking=True,
    )
    client = await hass_ws_client(hass)
    cfg = (await _ws(client, {"type": "habit_log/config"}))["result"]
    assert cfg["today_summary"]["h_run"] == {"count": 2, "seconds": 0, "active": True}
    assert cfg["today_summary"]["h_read"]["seconds"] == 1800
    assert cfg["today_summary"]["h_diet"]["active"] is False
    assert cfg["now_local"] == "2026-10-07T14:05:00"
    assert cfg["version"] == VERSION


async def test_service_errors_reach_card_as_readable_text(
    hass: HomeAssistant, entry: MockConfigEntry, hass_ws_client: WebSocketGenerator
) -> None:
    """The card shows err.message; it must be the translated sentence, not a key."""
    client = await hass_ws_client(hass)
    res = await _ws(
        client,
        {
            "type": "call_service",
            "domain": DOMAIN,
            "service": "log",
            "service_data": {"habit": "h_run", "date": "2026-10-09"},
            "return_response": True,
        },
    )
    assert res["success"] is False
    assert res["error"]["code"] == "service_validation_error"
    # HA prefixes "Validation error: "; the card strips it.
    assert res["error"]["message"].endswith("2026-10-09 is in the future")


async def test_long_period_allowed_up_to_limit(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    res = await hass.services.async_call(
        DOMAIN,
        "log",
        {"habit": "h_read", "start": "2025-06-01 08:00:00", "end": "2026-09-30 20:00:00"},
        blocking=True,
        return_response=True,
    )
    assert res["created"] is True
    state = hass.states.get("sensor.reading_time_all_time")
    assert float(state.state) > 11000


@pytest.mark.parametrize(
    "subentries",
    [[*DEFAULT_SUBENTRIES, habit("h_sugar", "No sugar", "streak", emoji="🍬", color="#7b4fd6", order=1)]],
)
async def test_record_card_fixtures(
    hass: HomeAssistant, entry: MockConfigEntry, hass_ws_client: WebSocketGenerator
) -> None:
    """Build a realistic month of data and record the exact payloads the card receives."""
    call = hass.services.async_call
    # Diet: streak with a gap; Meditate-like second streak via Run? Add a second streak habit.
    await call(
        DOMAIN,
        "backfill",
        {"habit": "h_diet", "start_date": "2026-09-22", "end_date": "2026-10-02"},
        blocking=True,
    )
    await call(
        DOMAIN,
        "backfill",
        {"habit": "h_diet", "start_date": "2026-10-04", "end_date": "2026-10-07"},
        blocking=True,
    )
    for day in ("2026-09-29", "2026-10-01", "2026-10-01", "2026-10-03", "2026-10-06"):
        await call(
            DOMAIN,
            "log",
            {"habit": "h_run", "date": day, "notes": "Park loop, easy pace" if day == "2026-10-06" else ""},
            blocking=True,
        )
    await call(
        DOMAIN,
        "log",
        {
            "habit": "h_read",
            "start": "2026-10-05 22:30:00",
            "end": "2026-10-06 00:45:00",
            "notes": "Finished part two",
        },
        blocking=True,
    )
    await call(
        DOMAIN,
        "log",
        {"habit": "h_read", "start": "2026-10-02 19:00:00", "duration": {"minutes": 50}},
        blocking=True,
    )
    await call(
        DOMAIN,
        "log",
        {"habit": "h_read", "start": "2026-10-07 07:15:00", "duration": {"minutes": 25}},
        blocking=True,
    )
    await call(
        DOMAIN,
        "journal",
        {
            "date": "2026-10-06",
            "values": {
                "Energy": 4,
                "Weight": 81.4,
                "Workout": "Good",
                "Notes": "Slept well.\nLegs a bit sore.",
                "Alcohol": False,
            },
        },
        blocking=True,
    )
    await call(
        DOMAIN,
        "backfill",
        {"habit": "h_sugar", "dates": "2026-09-30 2026-10-01 2026-10-03 2026-10-04 2026-10-05 2026-10-06"},
        blocking=True,
    )
    await call(
        DOMAIN, "journal", {"date": "2026-10-03", "values": {"Energy": 2, "Workout": "Poor"}}, blocking=True
    )
    await call(DOMAIN, "reset_counter", {"habit": "h_read", "at": "2026-10-04 12:00:00"}, blocking=True)

    client = await hass_ws_client(hass)
    cfg = (await _ws(client, {"type": "habit_log/config"}))["result"]
    entries = (
        await _ws(client, {"type": "habit_log/entries", "start_date": "2026-09-28", "end_date": "2026-11-01"})
    )["result"]
    stats = {}
    for habit_id in ("h_diet", "h_run", "h_read", "h_sugar"):
        for period in ("week", "month", "year", "all"):
            stats[f"{habit_id}:{period}:0"] = (
                await _ws(client, {"type": "habit_log/stats", "habit_id": habit_id, "period": period})
            )["result"]
        stats[f"{habit_id}:month:-1"] = (
            await _ws(
                client, {"type": "habit_log/stats", "habit_id": habit_id, "period": "month", "offset": -1}
            )
        )["result"]
    lists = {}
    for habit_id in ("h_diet", "h_run", "h_read"):
        lists[habit_id] = (
            await _ws(
                client,
                {
                    "type": "call_service",
                    "domain": DOMAIN,
                    "service": "list_entries",
                    "service_data": {"habit": habit_id, "start_date": "2026-10-01", "end_date": "2026-10-31"},
                    "return_response": True,
                },
            )
        )["result"]["response"]
    assert cfg["habits"] and entries["entries"]
    assert any(len(e["days"]) == 2 for e in entries["entries"])  # crosses midnight
    if FIXTURE_OUT:
        Path(FIXTURE_OUT).write_text(
            json.dumps(
                {"config": cfg, "entries": entries, "stats": stats, "lists": lists},
                indent=1,
                ensure_ascii=False,
            )
        )


async def test_exact_card_payloads_are_accepted(
    hass: HomeAssistant, entry: MockConfigEntry, hass_ws_client: WebSocketGenerator
) -> None:
    """Payloads copied from the card's interaction test, sent through call_service like the card does."""
    client = await hass_ws_client(hass)

    async def svc(service: str, data: dict[str, Any], response: bool = True) -> dict[str, Any]:
        res = await _ws(
            client,
            {
                "type": "call_service",
                "domain": DOMAIN,
                "service": service,
                "service_data": data,
                "return_response": response,
            },
        )
        assert res["success"], res
        return res["result"].get("response") or {}

    timed = await svc(
        "log",
        {
            "habit": "h_read",
            "start": "2026-10-07T13:05:00",
            "notes": "Ch. 5",
            "duration": {"hours": 0, "minutes": 45},
        },
    )
    long = await svc(
        "log", {"habit": "h_read", "start": "2026-06-01T09:00:00", "notes": "", "end": "2026-10-07T14:05:00"}
    )
    dated = await svc("log", {"habit": "h_run", "date": "2026-10-06", "notes": ""})
    await svc(
        "update_entry", {"entry_id": dated["entry_id"], "date": "2026-10-05", "notes": "Park loop, easy pace"}
    )
    await svc(
        "update_entry",
        {
            "entry_id": timed["entry_id"],
            "start": "2026-10-07T12:00:00",
            "end": "2026-10-07T12:30:00",
            "notes": "",
        },
    )
    journal = await svc(
        "journal",
        {
            "date": "2026-10-06",
            "values": {
                "f_energy": 4,
                "f_weight": 81.4,
                "f_notes": "Slept well.\nLegs sore.",
                "f_alcohol": False,
            },
        },
    )
    await svc(
        "update_journal_entry", {"entry_id": journal["entry_id"], "values": {"f_energy": 5, "f_weight": None}}
    )
    await svc("delete_entry", {"entry_id": long["entry_id"]}, response=False)

    listed = await svc("list_entries", {"habit": "h_read"})
    assert [e["seconds"] for e in listed["entries"]] == [1800]
    final = await svc("list_entries", {"start_date": "2026-10-06", "end_date": "2026-10-06"})
    values = {v["label"]: v["value"] for v in final["journal"][0]["values"]}
    assert values == {"Energy": 5, "Notes": "Slept well.\nLegs sore.", "Alcohol": False}


async def test_card_backfill_payloads(
    hass: HomeAssistant, entry: MockConfigEntry, hass_ws_client: WebSocketGenerator
) -> None:
    """The three shapes the card's back-fill form sends."""
    client = await hass_ws_client(hass)

    async def backfill(data: dict[str, Any]) -> dict[str, Any]:
        res = await _ws(
            client,
            {
                "type": "call_service",
                "domain": DOMAIN,
                "service": "backfill",
                "service_data": data,
                "return_response": True,
            },
        )
        assert res["success"], res
        return res["result"]["response"]

    assert await backfill(
        {
            "habit": "h_diet",
            "start_date": "2026-09-01",
            "end_date": "2026-09-09",
            "dates": "2026-08-20, 2026-09-03",
        }
    ) == {"added": 10, "skipped": 0}
    assert await backfill({"habit": "h_diet", "dates": "2026-09-02\n2026-09-15"}) == {
        "added": 1,
        "skipped": 1,
    }
    assert await backfill({"habit": "h_diet", "start_date": "2026-09-10", "end_date": "2026-09-10"}) == {
        "added": 1,
        "skipped": 0,
    }
