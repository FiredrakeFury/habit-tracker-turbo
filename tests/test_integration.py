"""Integration tests: setup, entities, actions, auth, calendar, websocket."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import ServiceValidationError, Unauthorized
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.habit_log.const import CONF_ALLOWED_USERS, DOMAIN

from .conftest import habit

NOW = "2026-10-07 14:00:00-04:00"


async def _call(hass: HomeAssistant, service: str, data: dict[str, Any], **kwargs: Any) -> Any:
    return await hass.services.async_call(
        DOMAIN, service, data, blocking=True, return_response=True, **kwargs
    )


def _state(hass: HomeAssistant, entity_id: str) -> str:
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return state.state


@pytest.fixture(autouse=True)
def _freeze(freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(NOW)


@pytest.fixture(autouse=True)
def _clean_snapshots(hass: HomeAssistant) -> None:
    """The test config dir is shared between runs; start each test clean."""
    import shutil

    shutil.rmtree(hass.config.path("habit_log"), ignore_errors=True)


async def test_setup_creates_entities(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Each habit gets a device with type-appropriate sensors; journal opt-ins get sensors."""
    assert entry.state is ConfigEntryState.LOADED
    ent_reg = er.async_get(hass)
    ids = {e.unique_id: e.entity_id for e in er.async_entries_for_config_entry(ent_reg, entry.entry_id)}
    # Streak habit: days + streak sensors, no count/time
    assert "h_diet_days_week" in ids and "h_diet_current_streak" in ids
    assert "h_diet_count_week" not in ids and "h_diet_time_week" not in ids
    # Counter: counts but no time
    assert "h_run_count_total" in ids and "h_run_time_total" not in ids
    # Time-tracked counter: counts and time
    assert "h_read_count_month" in ids and "h_read_time_year" in ids
    # Journal: only opted-in numeric fields
    assert "f_energy_latest" in ids and "f_weight_latest" in ids
    assert "f_workout_latest" not in ids
    assert ids["h_diet_current_streak"] == "sensor.diet_current_streak"
    assert ent_reg.async_get("sensor.diet_current_streak").config_subentry_id == "h_diet"
    assert hass.states.get("calendar.habit_log") is not None


async def test_streak_log_is_idempotent(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    first = await _call(hass, "log", {"habit": "h_diet"})
    second = await _call(hass, "log", {"habit": "h_diet"})
    assert first["created"] is True and second["created"] is False
    assert second["entry_id"] == first["entry_id"]
    await _call(hass, "backfill", {"habit": "h_diet", "start_date": "2026-10-03", "end_date": "2026-10-06"})
    await hass.async_block_till_done()
    assert _state(hass, "sensor.diet_current_streak") == "5"
    assert _state(hass, "sensor.diet_best_streak") == "5"
    assert _state(hass, "sensor.diet_days_this_week") == "3"  # Mon 5th - Wed 7th
    assert _state(hass, "sensor.diet_last_logged") == "2026-10-07"


async def test_counter_counts_multiple_per_day(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    for _ in range(3):
        await _call(hass, "log", {"habit": "h_run"})
    await _call(hass, "log", {"habit": "h_run", "date": "2026-09-30", "notes": "hills"})
    await hass.async_block_till_done()
    assert _state(hass, "sensor.run_count_this_week") == "3"
    assert _state(hass, "sensor.run_count_this_month") == "3"
    assert _state(hass, "sensor.run_count_all_time") == "4"


async def test_log_by_device_id(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, "h_run"), entry.entry_id)
    assert device is not None
    result = await _call(hass, "log", {"habit": device.id})
    assert result["created"] is True


async def test_time_tracking_splits_across_midnight(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await _call(
        hass, "log", {"habit": "h_read", "start": "2026-10-04 23:00:00", "end": "2026-10-05 01:30:00"}
    )
    await _call(hass, "log", {"habit": "h_read", "start": "2026-10-06 20:00:00", "duration": {"minutes": 45}})
    await hass.async_block_till_done()
    # Week starts Monday the 5th: 1.5h (after midnight) + 0.75h; the 23:00-00:00 hour belongs to last week.
    assert float(_state(hass, "sensor.reading_time_this_week")) == pytest.approx(2.25)
    # Entry counted once, on its start day (Sunday the 4th = previous week).
    assert _state(hass, "sensor.reading_count_this_week") == "1"
    assert float(_state(hass, "sensor.reading_time_all_time")) == pytest.approx(3.25)
    assert _state(hass, "sensor.reading_count_all_time") == "2"


@pytest.mark.parametrize(
    ("data", "key"),
    [
        ({"habit": "h_read", "date": "2026-10-01"}, "time_required"),
        ({"habit": "h_read", "start": "2026-10-01 10:00:00"}, "time_required"),
        (
            {"habit": "h_read", "start": "2026-10-01 10:00:00", "end": "2026-10-01 09:00:00"},
            "end_before_start",
        ),
        (
            {
                "habit": "h_read",
                "start": "2026-10-01 10:00:00",
                "end": "2026-10-01 11:00:00",
                "duration": {"hours": 1},
            },
            "end_and_duration",
        ),
        ({"habit": "h_read", "start": "2026-10-07 13:00:00", "end": "2026-10-07 18:00:00"}, "future_date"),
        (
            {"habit": "h_read", "start": "2023-01-01 10:00:00", "duration": {"days": 1101}},
            "duration_too_long",
        ),
        ({"habit": "h_run", "start": "2026-10-01 10:00:00", "duration": {"hours": 1}}, "time_not_supported"),
        ({"habit": "h_run", "date": "2026-10-08"}, "future_date"),
        ({"habit": "nope"}, "habit_not_found"),
    ],
)
async def test_log_validation(
    hass: HomeAssistant, entry: MockConfigEntry, data: dict[str, Any], key: str
) -> None:
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "log", data)
    assert err.value.translation_key == key


async def test_backfill_skips_existing_and_validates(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await _call(hass, "log", {"habit": "h_run", "date": "2026-09-02"})
    result = await _call(
        hass,
        "backfill",
        {
            "habit": "h_run",
            "start_date": "2026-09-01",
            "end_date": "2026-09-03",
            "dates": "2026-09-10, 2026-09-12\n2026-09-03",
        },
    )
    assert result == {"added": 4, "skipped": 1}
    rerun = await _call(
        hass, "backfill", {"habit": "h_run", "start_date": "2026-09-01", "end_date": "2026-09-03"}
    )
    assert rerun == {"added": 0, "skipped": 3}
    for data, key in (
        ({"habit": "h_read", "start_date": "2026-09-01"}, "backfill_timed"),
        ({"habit": "h_run"}, "backfill_empty"),
        ({"habit": "h_run", "dates": "2026-10-09"}, "future_date"),
        ({"habit": "h_run", "start_date": "2026-09-05", "end_date": "2026-09-01"}, "end_before_start"),
        ({"habit": "h_run", "dates": "2026-02-30"}, "invalid_date"),
    ):
        with pytest.raises(ServiceValidationError) as err:
            await _call(hass, "backfill", data)
        assert err.value.translation_key == key


async def test_update_and_delete_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    created = await _call(hass, "log", {"habit": "h_diet", "date": "2026-10-01"})
    await _call(hass, "log", {"habit": "h_diet", "date": "2026-10-02"})
    entry_id = created["entry_id"]

    moved = await _call(hass, "update_entry", {"entry_id": entry_id, "date": "2026-09-30", "notes": "fixed"})
    assert moved["entry"]["day"] == "2026-09-30" and moved["entry"]["notes"] == "fixed"

    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "update_entry", {"entry_id": entry_id, "date": "2026-10-02"})
    assert err.value.translation_key == "streak_day_exists"

    # Reassign to a counter habit
    reassigned = await _call(hass, "update_entry", {"entry_id": entry_id, "habit": "h_run"})
    assert reassigned["entry"]["habit_id"] == "h_run"

    await hass.services.async_call(DOMAIN, "delete_entry", {"entry_id": entry_id}, blocking=True)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "delete_entry", {"entry_id": entry_id}, blocking=True)


async def test_update_timed_entry_keeps_length_when_moving_start(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    created = await _call(
        hass, "log", {"habit": "h_read", "start": "2026-10-01 10:00:00", "end": "2026-10-01 11:30:00"}
    )
    updated = await _call(
        hass, "update_entry", {"entry_id": created["entry_id"], "start": "2026-10-02 08:00:00"}
    )
    assert updated["entry"]["seconds"] == 5400
    assert updated["entry"]["start_local"].startswith("2026-10-02T08:00:00")
    end_only = await _call(
        hass, "update_entry", {"entry_id": created["entry_id"], "end": "2026-10-02 10:00:00"}
    )
    assert end_only["entry"]["seconds"] == 7200


async def test_list_entries(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await _call(hass, "log", {"habit": "h_run", "date": "2026-10-01"})
    await _call(
        hass, "log", {"habit": "h_read", "start": "2026-09-30 23:00:00", "end": "2026-10-01 00:30:00"}
    )
    await _call(hass, "journal", {"date": "2026-10-01", "values": {"Energy": 4}})
    result = await _call(hass, "list_entries", {"start_date": "2026-10-01", "end_date": "2026-10-01"})
    assert len(result["entries"]) == 2  # timed entry touches Oct 1
    assert len(result["journal"]) == 1
    only_run = await _call(hass, "list_entries", {"habit": "h_run"})
    assert [e["habit_name"] for e in only_run["entries"]] == ["Run"]
    assert only_run["journal"] == []


async def test_journal_values_and_sensors(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await _call(hass, "journal", {"date": "2026-10-05", "values": {"Energy": 2, "Weight": "81.4"}})
    result = await _call(
        hass,
        "journal",
        {
            "values": {
                "energy": 4,
                "f_workout": "Good",
                "Notes": " felt strong ",
                "Alcohol": "no",
                "Weight": "",
            }
        },
    )
    await hass.async_block_till_done()
    assert _state(hass, "sensor.journal_energy") == "4"
    assert _state(hass, "sensor.journal_weight") == "81.4"  # empty input ignored, previous value stays
    assert hass.states.get("sensor.journal_weight").attributes["recorded_for"] == "2026-10-05"
    assert hass.states.get("sensor.journal_weight").attributes["unit_of_measurement"] == "kg"

    listed = await _call(hass, "list_entries", {"start_date": "2026-10-07"})
    values = {v["label"]: v["value"] for v in listed["journal"][0]["values"]}
    assert values == {"Energy": 4, "Workout": "Good", "Notes": "felt strong", "Alcohol": False}

    updated = await _call(
        hass,
        "update_journal_entry",
        {"entry_id": result["entry_id"], "values": {"Energy": 5, "Alcohol": None}},
    )
    assert {v["label"] for v in updated["entry"]["values"]} == {"Energy", "Workout", "Notes"}

    for values_in, key in (
        ({"Energy": 6}, "journal_invalid_value"),
        ({"Energy": 3.5}, "journal_invalid_value"),
        ({"Weight": 5}, "journal_invalid_value"),
        ({"Workout": "Amazing"}, "journal_invalid_value"),
        ({"Alcohol": "maybe"}, "journal_invalid_value"),
        ({"Mood": 3}, "journal_unknown_field"),
        ({"Energy": None}, "journal_empty"),
    ):
        with pytest.raises(ServiceValidationError) as err:
            await _call(hass, "journal", {"values": values_in})
        assert err.value.translation_key == key, values_in


@pytest.mark.parametrize(
    "options", [{CONF_ALLOWED_USERS: ["allowed-user"], "week_start": "monday", "calendar_details": False}]
)
async def test_allowed_users_enforced(
    hass: HomeAssistant, entry: MockConfigEntry, hass_ws_client: WebSocketGenerator, hass_admin_user: MockUser
) -> None:
    with pytest.raises(Unauthorized):
        await _call(hass, "log", {"habit": "h_run"}, context=Context(user_id="someone-else"))
    ok = await _call(hass, "log", {"habit": "h_run"}, context=Context(user_id="allowed-user"))
    assert ok["created"] is True
    internal = await _call(hass, "log", {"habit": "h_run"})  # automation: no user
    assert internal["created"] is True

    client = await hass_ws_client(hass)  # admin user, not in the allowed list
    await client.send_json_auto_id({"type": "habit_log/config"})
    response = await client.receive_json()
    assert response["success"] is False
    assert response["error"]["code"] == "unauthorized"


async def test_merge_habits(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await _call(hass, "backfill", {"habit": "h_run", "dates": "2026-10-01 2026-10-02"})
    await _call(hass, "log", {"habit": "h_diet", "date": "2026-10-02"})
    result = await _call(hass, "merge_habits", {"source": "h_run", "target": "h_diet"})
    assert result == {"moved": 1, "dropped_duplicates": 1, "source_archived": True}
    await hass.async_block_till_done()
    assert entry.subentries["h_run"].data["archived"] is True
    # Archived habit: device and sensors removed, history (now merged) kept
    assert dr.async_get(hass).async_get_device_by_identifier((DOMAIN, "h_run"), entry.entry_id) is None
    assert hass.states.get("sensor.run_count_all_time") is None
    assert _state(hass, "sensor.diet_days_all_time") == "2"
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "log", {"habit": "h_run"})
    assert err.value.translation_key == "habit_archived"
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "merge_habits", {"source": "h_diet", "target": "h_diet"})
    assert err.value.translation_key == "merge_same"


async def test_merge_timed_into_untimed_rejected(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await _call(hass, "log", {"habit": "h_read", "start": "2026-10-01 10:00:00", "duration": {"hours": 1}})
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "merge_habits", {"source": "h_read", "target": "h_run"})
    assert err.value.translation_key == "merge_incompatible"


async def test_deleting_habit_snapshots_and_purges(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await _call(hass, "backfill", {"habit": "h_run", "dates": "2026-10-01 2026-10-02"})
    await _call(hass, "log", {"habit": "h_diet"})
    assert hass.config_entries.async_remove_subentry(entry, "h_run")
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    folder = Path(hass.config.path("habit_log/deleted"))
    files = list(folder.glob("run_*.json"))
    assert len(files) == 1
    snapshot = json.loads(files[0].read_text())
    assert snapshot["habit"]["name"] == "Run" and len(snapshot["entries"]) == 2
    runtime = entry.runtime_data
    assert all(e.habit_id != "h_run" for e in runtime.store.entries.values())
    assert len(runtime.store.entries) == 1  # diet entry untouched
    assert hass.states.get("sensor.run_count_all_time") is None


async def test_data_persists_across_reload(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await _call(hass, "log", {"habit": "h_diet"})
    await _call(hass, "journal", {"values": {"Weight": 80}})
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    runtime = entry.runtime_data
    assert len(runtime.store.entries) == 1 and len(runtime.store.journal) == 1
    assert _state(hass, "sensor.journal_weight") == "80"


async def test_midnight_rollover_updates_sensors(
    hass: HomeAssistant, entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    await _call(hass, "log", {"habit": "h_diet", "date": "2026-10-06"})
    await hass.async_block_till_done()
    assert _state(hass, "sensor.diet_current_streak") == "1"
    freezer.move_to("2026-10-09 00:00:02-04:00")  # two days later: streak broken
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert _state(hass, "sensor.diet_current_streak") == "0"


async def test_calendar_events(hass: HomeAssistant, entry: MockConfigEntry, hass_client: Any) -> None:
    await _call(hass, "log", {"habit": "h_diet", "date": "2026-10-06", "notes": "secret"})
    await _call(
        hass, "log", {"habit": "h_read", "start": "2026-10-05 23:00:00", "end": "2026-10-06 00:30:00"}
    )
    await _call(hass, "journal", {"date": "2026-10-06", "values": {"Energy": 3}})
    client = await hass_client()
    resp = await client.get(
        "/api/calendars/calendar.habit_log?start=2026-10-06T00:00:00-04:00&end=2026-10-07T00:00:00-04:00"
    )
    assert resp.status == 200
    events = await resp.json()
    summaries = sorted(e["summary"] for e in events)
    assert summaries == ["📖 Reading", "📝 Journal", "🥗 Diet"]
    assert all(not e.get("description") for e in events)  # calendar_details off by default


async def test_websocket_commands(
    hass: HomeAssistant, entry: MockConfigEntry, hass_ws_client: WebSocketGenerator
) -> None:
    await _call(hass, "backfill", {"habit": "h_diet", "start_date": "2026-10-01", "end_date": "2026-10-07"})
    client = await hass_ws_client(hass)

    await client.send_json_auto_id({"type": "habit_log/config"})
    config = (await client.receive_json())["result"]
    assert [h["name"] for h in config["habits"]] == ["Diet", "Reading", "Run"]
    assert config["today"] == "2026-10-07" and config["time_zone"] == "America/New_York"
    assert {f["name"] for f in config["journal_fields"]} == {
        "Energy",
        "Weight",
        "Workout",
        "Notes",
        "Alcohol",
    }

    await client.send_json_auto_id(
        {"type": "habit_log/entries", "start_date": "2026-10-05", "end_date": "2026-10-07"}
    )
    entries = (await client.receive_json())["result"]
    assert len(entries["entries"]) == 3

    await client.send_json_auto_id({"type": "habit_log/stats", "habit_id": "h_diet", "period": "month"})
    stats = (await client.receive_json())["result"]
    assert stats["summary"]["active_days"] == 7 and stats["summary"]["current_streak"] == 7
    assert len(stats["buckets"]) == 31

    await client.send_json_auto_id(
        {"type": "habit_log/stats", "habit_id": "h_diet", "period": "month", "offset": -1}
    )
    previous = (await client.receive_json())["result"]
    assert previous["summary"]["start"] == "2026-09-01" and previous["summary"]["active_days"] == 0

    await client.send_json_auto_id({"type": "habit_log/subscribe"})
    sub = await client.receive_json()
    assert sub["success"]
    await _call(hass, "log", {"habit": "h_run"})
    event = await client.receive_json()
    assert event["id"] == sub["id"] and event["event"] == {"changed": True}

    # Subscription survives an entry reload (e.g. a habit was added)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    event = await client.receive_json()
    assert event["event"] == {"changed": True}

    await client.send_json_auto_id(
        {"type": "habit_log/entries", "start_date": "2026-10-07", "end_date": "2025-01-01"}
    )
    assert (await client.receive_json())["success"] is False


@pytest.mark.parametrize("subentries", [[habit("h_old", "Old", archived=True), habit("h_new", "New")]])
async def test_archived_habit_has_no_entities(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert hass.states.get("sensor.old_count_all_time") is None
    assert hass.states.get("sensor.new_count_all_time") is not None


async def test_remove_entry_exports_data(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await _call(hass, "log", {"habit": "h_diet"})
    await entry.runtime_data.store.async_flush()
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    files = list(Path(hass.config.path("habit_log/deleted")).glob("full_export_*.json"))
    assert len(files) == 1
    assert len(json.loads(files[0].read_text())["entries"]) == 1


async def test_diagnostics_has_no_personal_data(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    from custom_components.habit_log.diagnostics import async_get_config_entry_diagnostics

    await _call(hass, "log", {"habit": "h_run", "notes": "private note"})
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert "private note" not in json.dumps(diag)
    assert diag["totals"]["entries"] == 1


async def test_time_zone_day_boundaries_use_local_time(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """A 23:30 local start counts on that local day even though it is the next day in UTC."""
    created = await _call(
        hass, "log", {"habit": "h_read", "start": "2026-10-06 23:30:00", "duration": {"minutes": 20}}
    )
    result = await _call(hass, "list_entries", {"habit": "h_read"})
    assert result["entries"][0]["count_day"] == "2026-10-06"
    assert result["entries"][0]["id"] == created["entry_id"]
    assert date.fromisoformat(result["entries"][0]["days"][0]) == date(2026, 10, 6)
    assert isinstance(datetime.fromisoformat(result["entries"][0]["start"]), datetime)
    assert timedelta(seconds=result["entries"][0]["seconds"]) == timedelta(minutes=20)


@pytest.mark.parametrize(
    "options", [{CONF_ALLOWED_USERS: [], "week_start": "monday", "calendar_details": True}]
)
async def test_calendar_details_option(hass: HomeAssistant, entry: MockConfigEntry, hass_client: Any) -> None:
    await _call(hass, "log", {"habit": "h_diet", "date": "2026-10-06", "notes": "cheat-free"})
    await _call(
        hass, "journal", {"date": "2026-10-06", "values": {"Energy": 3, "Weight": 80.5, "Alcohol": False}}
    )
    client = await hass_client()
    resp = await client.get(
        "/api/calendars/calendar.habit_log?start=2026-10-06T00:00:00-04:00&end=2026-10-07T00:00:00-04:00"
    )
    events = {e["summary"]: e for e in await resp.json()}
    assert events["🥗 Diet"]["description"] == "cheat-free"
    assert (
        events["📝 Journal"]["description"] == "Alcohol: No\nEnergy: 3\nWeight: 80.5 kg"
    )  # field order, then name


async def test_reset_counter_and_undo(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """Resets keep entries; since-reset sensors and stats count from the reset."""
    await _call(hass, "log", {"habit": "h_read", "start": "2026-10-01 10:00:00", "duration": {"hours": 2}})
    await _call(hass, "log", {"habit": "h_read", "start": "2026-10-06 21:00:00", "duration": {"hours": 1}})
    await hass.async_block_till_done()
    assert float(_state(hass, "sensor.reading_time_since_reset")) == pytest.approx(3.0)
    assert hass.states.get("sensor.reading_time_since_reset").attributes.get("last_reset") is None

    # Backdated reset between the two entries.
    res = await _call(hass, "reset_counter", {"habit": "h_read", "at": "2026-10-03 00:00:00"})
    assert res["since_reset"]["seconds"] == 3600 and res["since_reset"]["count"] == 1
    await hass.async_block_till_done()
    assert float(_state(hass, "sensor.reading_time_since_reset")) == pytest.approx(1.0)
    assert _state(hass, "sensor.reading_count_since_reset") == "1"
    assert float(_state(hass, "sensor.reading_time_all_time")) == pytest.approx(3.0)  # all time unchanged
    attrs = hass.states.get("sensor.reading_time_since_reset").attributes
    assert attrs["last_reset"].startswith("2026-10-03T04:00:00")  # midnight New York, in UTC
    assert attrs["state_class"] == "total"

    # Reset now -> zero; entries remain.
    await _call(hass, "reset_counter", {"habit": "h_read"})
    await hass.async_block_till_done()
    assert float(_state(hass, "sensor.reading_time_since_reset")) == 0
    assert len(entry.runtime_data.store.entries_for_habit("h_read")) == 2

    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "habit_log/stats", "habit_id": "h_read", "period": "all"})
    stats = (await client.receive_json())["result"]
    assert stats["since_reset"]["resets"] == 2 and stats["since_reset"]["seconds"] == 0
    assert stats["summary"]["seconds"] == 3 * 3600
    summary = stats["summary"]  # period "all"
    assert stats["since_reset"]["all_time"] == {
        "count": summary["count"],
        "seconds": summary["seconds"],
        "active_days": summary["active_days"],
    }
    await client.send_json_auto_id({"type": "habit_log/config"})
    cfg = (await client.receive_json())["result"]
    assert next(h for h in cfg["habits"] if h["id"] == "h_read")["last_reset"].startswith(
        "2026-10-07T18:00:00"
    )

    # Undo goes back to the previous reset, then to none.
    undone = await _call(hass, "undo_reset", {"habit": "h_read"})
    assert undone["last_reset"].startswith("2026-10-03T04:00:00")
    await hass.async_block_till_done()
    assert float(_state(hass, "sensor.reading_time_since_reset")) == pytest.approx(1.0)
    await _call(hass, "undo_reset", {"habit": "h_read"})
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "undo_reset", {"habit": "h_read"})
    assert err.value.translation_key == "no_reset"

    # Persisted across reload
    await _call(hass, "reset_counter", {"habit": "h_run"})
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.store.last_reset("h_run") is not None


async def test_reset_validation(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "reset_counter", {"habit": "h_diet"})
    assert err.value.translation_key == "reset_streak"
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "reset_counter", {"habit": "h_run", "at": "2026-10-09 00:00:00"})
    assert err.value.translation_key == "future_date"
    assert hass.states.get("sensor.diet_count_since_reset") is None  # streaks have none
    assert hass.states.get("sensor.run_count_since_reset") is not None
    assert hass.states.get("sensor.run_time_since_reset") is None  # untimed counter: count only


async def test_deleted_habit_snapshot_includes_resets(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await _call(hass, "log", {"habit": "h_run", "date": "2026-10-01"})
    await _call(hass, "reset_counter", {"habit": "h_run"})
    assert hass.config_entries.async_remove_subentry(entry, "h_run")
    await hass.async_block_till_done()
    snapshot = json.loads(next(Path(hass.config.path("habit_log/deleted")).glob("run_*.json")).read_text())
    assert len(snapshot["resets"]) == 1
    assert "h_run" not in entry.runtime_data.store.resets
