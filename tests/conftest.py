"""Fixtures for Habit Log tests."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.habit_log.const import (
    CONF_ACTIVE,
    CONF_ARCHIVED,
    CONF_COLOR,
    CONF_CREATE_SENSOR,
    CONF_EMOJI,
    CONF_FIELD_TYPE,
    CONF_KIND,
    CONF_MAX,
    CONF_MIN,
    CONF_OPTIONS,
    CONF_ORDER,
    CONF_STEP,
    CONF_TRACK_TIME,
    CONF_UNIT,
    DEFAULT_OPTIONS,
    DOMAIN,
    SUBENTRY_HABIT,
    SUBENTRY_JOURNAL_FIELD,
)

TZ = "America/New_York"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> Generator[None]:
    """Enable custom integrations for every test."""
    yield


def habit(
    subentry_id: str, title: str, kind: str = "counter", track_time: bool = False, **extra: Any
) -> dict[str, Any]:
    """Build habit subentry data."""
    return {
        "subentry_id": subentry_id,
        "subentry_type": SUBENTRY_HABIT,
        "title": title,
        "unique_id": None,
        "data": {
            CONF_EMOJI: extra.get("emoji", ""),
            CONF_COLOR: extra.get("color"),
            CONF_KIND: kind,
            CONF_TRACK_TIME: track_time,
            CONF_ORDER: extra.get("order", 0),
            CONF_ARCHIVED: extra.get("archived", False),
        },
    }


def journal_field(subentry_id: str, title: str, field_type: str, **data: Any) -> dict[str, Any]:
    """Build journal field subentry data."""
    return {
        "subentry_id": subentry_id,
        "subentry_type": SUBENTRY_JOURNAL_FIELD,
        "title": title,
        "unique_id": None,
        "data": {
            CONF_FIELD_TYPE: field_type,
            CONF_ORDER: data.pop("order", 0),
            CONF_ACTIVE: data.pop("active", True),
            **data,
        },
    }


DEFAULT_SUBENTRIES = [
    habit("h_diet", "Diet", "streak", emoji="🥗", color="#43a047"),
    habit("h_run", "Run", "counter", emoji="🏃"),
    habit("h_read", "Reading", "counter", track_time=True, emoji="📖"),
    journal_field("f_energy", "Energy", "rating", **{CONF_MAX: 5, CONF_CREATE_SENSOR: True}),
    journal_field(
        "f_weight",
        "Weight",
        "number",
        **{CONF_UNIT: "kg", CONF_MIN: 20, CONF_MAX: 300, CONF_STEP: 0.1, CONF_CREATE_SENSOR: True},
    ),
    journal_field("f_workout", "Workout", "select", **{CONF_OPTIONS: ["Great", "Good", "Poor"]}),
    journal_field("f_notes", "Notes", "text"),
    journal_field("f_alcohol", "Alcohol", "boolean"),
]


@pytest.fixture
def subentries() -> list[dict[str, Any]]:
    """Subentries for the default entry (override per test)."""
    return [dict(s, data=dict(s["data"])) for s in DEFAULT_SUBENTRIES]


@pytest.fixture
def options() -> dict[str, Any]:
    """Options for the default entry."""
    return dict(DEFAULT_OPTIONS)


@pytest.fixture
async def entry(
    hass: HomeAssistant, subentries: list[dict[str, Any]], options: dict[str, Any]
) -> MockConfigEntry:
    """Set up a loaded Habit Log entry in a fixed time zone."""
    await hass.config.async_set_time_zone(TZ)
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Habit Log",
        data={},
        options=options,
        subentries_data=subentries,
        version=1,
        minor_version=1,
    )
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry
