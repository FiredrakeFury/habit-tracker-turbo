"""Sensors for Habit Log: per-habit period statistics and journal values."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    DOMAIN,
    PERIOD_ALL,
    PERIOD_MONTH,
    PERIOD_WEEK,
    PERIOD_YEAR,
    TITLE,
)
from .models import Habit, JournalField
from .runtime import HabitLogRuntime

if TYPE_CHECKING:
    from . import HabitLogConfigEntry

PARALLEL_UPDATES = 0

PERIOD_KEYS: dict[str, str] = {
    PERIOD_WEEK: "week",
    PERIOD_MONTH: "month",
    PERIOD_YEAR: "year",
    PERIOD_ALL: "total",
}


@dataclass(frozen=True, kw_only=True)
class HabitSensorDescription(SensorEntityDescription):
    """Describes a per-habit sensor."""

    value_fn: Callable[[HabitLogRuntime, Habit], Any]
    applies_fn: Callable[[Habit], bool]


def _count(period: str) -> Callable[[HabitLogRuntime, Habit], Any]:
    return lambda rt, habit: rt.summary(habit.id, period)["count"]


def _active_days(period: str) -> Callable[[HabitLogRuntime, Habit], Any]:
    return lambda rt, habit: rt.summary(habit.id, period)["active_days"]


def _hours(period: str) -> Callable[[HabitLogRuntime, Habit], Any]:
    return lambda rt, habit: round(rt.summary(habit.id, period)["seconds"] / 3600, 3)


def _last_day(rt: HabitLogRuntime, habit: Habit) -> date | None:
    last = rt.index(habit.id).last_day
    today = rt.today()
    if last is not None and last > today:  # time entry spilling past midnight into today+
        last = today
    return last


def _descriptions() -> list[HabitSensorDescription]:
    out: list[HabitSensorDescription] = []
    for period, key in PERIOD_KEYS.items():
        state_class = SensorStateClass.TOTAL if period == PERIOD_ALL else SensorStateClass.MEASUREMENT
        out.append(
            HabitSensorDescription(
                key=f"count_{key}",
                translation_key=f"count_{key}",
                state_class=state_class,
                native_unit_of_measurement="entries",
                value_fn=_count(period),
                applies_fn=lambda h: not h.is_streak,
            )
        )
        out.append(
            HabitSensorDescription(
                key=f"days_{key}",
                translation_key=f"days_{key}",
                state_class=state_class,
                native_unit_of_measurement=UnitOfTime.DAYS,
                value_fn=_active_days(period),
                applies_fn=lambda h: h.is_streak,
            )
        )
        out.append(
            HabitSensorDescription(
                key=f"time_{key}",
                translation_key=f"time_{key}",
                device_class=SensorDeviceClass.DURATION,
                state_class=state_class,
                native_unit_of_measurement=UnitOfTime.HOURS,
                suggested_display_precision=2,
                value_fn=_hours(period),
                applies_fn=lambda h: h.track_time,
            )
        )
    out.extend(
        [
            HabitSensorDescription(
                key="current_streak",
                translation_key="current_streak",
                state_class=SensorStateClass.MEASUREMENT,
                native_unit_of_measurement=UnitOfTime.DAYS,
                value_fn=lambda rt, h: rt.summary(h.id, PERIOD_ALL)["current_streak"],
                applies_fn=lambda h: True,
            ),
            HabitSensorDescription(
                key="best_streak",
                translation_key="best_streak",
                state_class=SensorStateClass.MEASUREMENT,
                native_unit_of_measurement=UnitOfTime.DAYS,
                value_fn=lambda rt, h: rt.summary(h.id, PERIOD_ALL)["best_streak"],
                applies_fn=lambda h: True,
            ),
            HabitSensorDescription(
                key="last_logged",
                translation_key="last_logged",
                device_class=SensorDeviceClass.DATE,
                value_fn=_last_day,
                applies_fn=lambda h: True,
            ),
        ]
    )
    return out


DESCRIPTIONS = _descriptions()


@dataclass(frozen=True, kw_only=True)
class SinceResetDescription(SensorEntityDescription):
    """Describes a since-reset sensor (state_class total with last_reset)."""

    value_fn: Callable[[dict[str, Any]], Any]
    applies_fn: Callable[[Habit], bool]


SINCE_RESET_DESCRIPTIONS = [
    SinceResetDescription(
        key="count_since_reset",
        translation_key="count_since_reset",
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement="entries",
        value_fn=lambda r: r["count"],
        applies_fn=lambda h: not h.is_streak,
    ),
    SinceResetDescription(
        key="time_since_reset",
        translation_key="time_since_reset",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfTime.HOURS,
        suggested_display_precision=2,
        value_fn=lambda r: round(r["seconds"] / 3600, 3),
        applies_fn=lambda h: h.track_time,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HabitLogConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create sensors for active habits and opted-in journal fields."""
    runtime = entry.runtime_data
    for habit in runtime.active_habits():
        async_add_entities(
            [HabitSensor(runtime, habit, d) for d in DESCRIPTIONS if d.applies_fn(habit)]
            + [SinceResetSensor(runtime, habit, d) for d in SINCE_RESET_DESCRIPTIONS if d.applies_fn(habit)],
            config_subentry_id=habit.id,
        )
    for journal_field in runtime.fields.values():
        if journal_field.create_sensor and journal_field.active:
            async_add_entities(
                [JournalFieldSensor(runtime, journal_field)],
                config_subentry_id=journal_field.id,
            )


class _BaseSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, runtime: HabitLogRuntime) -> None:
        self._runtime = runtime

    async def async_added_to_hass(self) -> None:
        """Subscribe to runtime changes."""
        self.async_on_remove(self._runtime.async_add_listener(self._handle_change))

    @callback
    def _handle_change(self) -> None:
        self.async_write_ha_state()


class HabitSensor(_BaseSensor):
    """A statistic for one habit."""

    entity_description: HabitSensorDescription

    def __init__(self, runtime: HabitLogRuntime, habit: Habit, description: HabitSensorDescription) -> None:
        """Initialize."""
        super().__init__(runtime)
        self._habit = habit
        self.entity_description = description
        self._attr_unique_id = f"{habit.id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, habit.id)},
            name=habit.name,
            manufacturer=TITLE,
            model="Streak habit" if habit.is_streak else "Counter habit",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def native_value(self) -> Any:
        """Return the current value."""
        return self.entity_description.value_fn(self._runtime, self._habit)


class SinceResetSensor(HabitSensor):
    """Count or time since the habit's latest reset; last_reset marks the reset."""

    entity_description: SinceResetDescription  # type: ignore[assignment]

    @property
    def native_value(self) -> Any:
        """Return the total since the latest reset."""
        return self.entity_description.value_fn(self._runtime.since_reset(self._habit.id))

    @property
    def last_reset(self) -> datetime | None:
        """Return when the counter was last reset (None if never)."""
        return self._runtime.store.last_reset(self._habit.id)


class JournalFieldSensor(_BaseSensor):
    """Latest recorded value of a numeric journal field."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:notebook-outline"

    def __init__(self, runtime: HabitLogRuntime, journal_field: JournalField) -> None:
        """Initialize."""
        super().__init__(runtime)
        self._field = journal_field
        self._attr_unique_id = f"{journal_field.id}_latest"
        # No device: a device may belong to only one subentry, and each journal
        # field is its own subentry. The entity is tied to that subentry, so it
        # is removed automatically when the field is deleted.
        self._attr_has_entity_name = False
        self._attr_name = f"Journal {journal_field.name}"
        self._attr_native_unit_of_measurement = journal_field.unit

    @property
    def native_value(self) -> float | int | None:
        """Return the latest value."""
        latest = self._runtime.latest_value(self._field.id)
        if latest is None or isinstance(latest[0], bool):
            return None
        return latest[0]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the date the value was recorded for."""
        latest = self._runtime.latest_value(self._field.id)
        return {"recorded_for": latest[1].isoformat() if latest else None}
