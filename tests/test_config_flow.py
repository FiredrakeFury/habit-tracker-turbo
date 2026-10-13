"""Tests for the config, options and subentry flows (the GUI)."""

from __future__ import annotations

from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser

from custom_components.habit_log.const import DOMAIN


@pytest.fixture(autouse=True)
def _freeze(freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to("2026-10-07 14:00:00-04:00")


async def _sub_init(
    hass: HomeAssistant, entry: MockConfigEntry, kind: str, source: str = SOURCE_USER, **ctx: Any
):
    return await hass.config_entries.subentries.async_init(
        (entry.entry_id, kind), context={"source": source, **ctx}
    )


async def test_user_flow_and_single_instance(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"] == {"allowed_users": [], "week_start": "monday", "calendar_details": False}
    await hass.async_block_till_done()
    second = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert second["type"] is FlowResultType.ABORT
    assert second["reason"] == "single_instance_allowed"


async def test_options_flow(hass: HomeAssistant, entry: MockConfigEntry, hass_admin_user: MockUser) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    with pytest.raises(InvalidData):  # only real, active, non-system users can be chosen
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            {"allowed_users": ["unknown-id"], "week_start": "sunday", "calendar_details": True},
        )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"allowed_users": [hass_admin_user.id], "week_start": "sunday", "calendar_details": True},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options == {
        "allowed_users": [hass_admin_user.id],
        "week_start": "sunday",
        "calendar_details": True,
    }
    assert entry.runtime_data.week_start == "sunday"  # reloaded with new options


async def test_add_habit(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    result = await _sub_init(hass, entry, "habit")
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": " Meditate ",
            "emoji": "🧘",
            "color": [255, 0, 128],
            "kind": "streak",
            "track_time": False,
            "order": 3,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    subentry = next(s for s in entry.subentries.values() if s.title == "Meditate")
    assert subentry.data["color"] == "#ff0080"
    assert hass.states.get("sensor.meditate_current_streak") is not None


@pytest.mark.parametrize(
    ("user_input", "errors"),
    [
        ({"name": "", "kind": "counter", "track_time": False, "order": 0}, {"name": "name_required"}),
        ({"name": "diet", "kind": "counter", "track_time": False, "order": 0}, {"name": "name_exists"}),
        ({"name": "x" * 41, "kind": "counter", "track_time": False, "order": 0}, {"name": "name_too_long"}),
        (
            {"name": "Plank", "kind": "streak", "track_time": True, "order": 0},
            {"track_time": "streak_time_tracking"},
        ),
        (
            {"name": "Plank", "emoji": "x" * 17, "kind": "counter", "track_time": False, "order": 0},
            {"emoji": "emoji_too_long"},
        ),
    ],
)
async def test_add_habit_errors(
    hass: HomeAssistant, entry: MockConfigEntry, user_input: dict[str, Any], errors: dict[str, str]
) -> None:
    result = await _sub_init(hass, entry, "habit")
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], user_input)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == errors


async def test_reconfigure_habit_rename_and_archive(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    result = await _sub_init(hass, entry, "habit", SOURCE_RECONFIGURE, subentry_id="h_run")
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"name": "Jog", "emoji": "🏃", "kind": "counter", "track_time": False, "order": 1, "archived": False},
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.subentries["h_run"].title == "Jog"
    # Entity IDs are stable across renames; the friendly name follows the device name.
    state = hass.states.get("sensor.run_count_all_time")
    assert state is not None and state.name == "Jog Count all time"

    result = await _sub_init(hass, entry, "habit", SOURCE_RECONFIGURE, subentry_id="h_run")
    await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"name": "Jog", "kind": "counter", "track_time": False, "order": 1, "archived": True},
    )
    await hass.async_block_till_done()
    assert hass.states.get("sensor.run_count_all_time") is None


async def test_cannot_disable_time_tracking_with_timed_entries(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    await hass.services.async_call(
        DOMAIN,
        "log",
        {"habit": "h_read", "start": "2026-10-01 10:00:00", "duration": {"hours": 1}},
        blocking=True,
    )
    result = await _sub_init(hass, entry, "habit", SOURCE_RECONFIGURE, subentry_id="h_read")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"name": "Reading", "kind": "counter", "track_time": False, "order": 0, "archived": False},
    )
    assert result["errors"] == {"track_time": "has_timed_entries"}


async def test_add_number_journal_field(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    result = await _sub_init(hass, entry, "journal_field")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Sleep", "field_type": "number", "order": 2}
    )
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "details"
    bad = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"unit": "h", "min": 10, "max": 5, "step": 0.5, "create_sensor": True}
    )
    assert bad["errors"] == {"max": "max_not_above_min"}
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"unit": "h", "min": 0, "max": 24, "step": 0.5, "create_sensor": True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    state = hass.states.get("sensor.journal_sleep")
    assert state is not None and state.attributes["unit_of_measurement"] == "h"


async def test_add_select_and_boolean_fields(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    result = await _sub_init(hass, entry, "journal_field")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Mood", "field_type": "select", "order": 0}
    )
    empty = await hass.config_entries.subentries.async_configure(result["flow_id"], {"options": ["  "]})
    assert empty["errors"] == {"options": "options_required"}
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"options": ["Happy", "Calm", "Happy", " Tired "]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    mood = next(s for s in entry.subentries.values() if s.title == "Mood")
    assert mood.data["options"] == ["Happy", "Calm", "Tired"]

    result = await _sub_init(hass, entry, "journal_field")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Stretched", "field_type": "boolean", "order": 0}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY  # no details step for yes/no


async def test_retype_field_affects_only_future_entries(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await hass.services.async_call(
        DOMAIN, "journal", {"date": "2026-10-01", "values": {"Workout": "Good"}}, blocking=True
    )
    result = await _sub_init(hass, entry, "journal_field", SOURCE_RECONFIGURE, subentry_id="f_workout")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Workout quality", "field_type": "rating", "order": 0, "active": True}
    )
    assert result["step_id"] == "details"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"max": 10, "create_sensor": False}
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()

    await hass.services.async_call(DOMAIN, "journal", {"values": {"Workout quality": 8}}, blocking=True)
    listed = await hass.services.async_call(DOMAIN, "list_entries", {}, blocking=True, return_response=True)
    old, new = listed["journal"]
    assert old["values"][0] == {
        "field_id": "f_workout",
        "label": "Workout",
        "field_type": "select",
        "unit": None,
        "value": "Good",
    }
    assert new["values"][0]["label"] == "Workout quality" and new["values"][0]["value"] == 8
