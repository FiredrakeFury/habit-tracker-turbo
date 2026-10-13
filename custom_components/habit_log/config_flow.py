"""Config flow, options flow and subentry flows for Habit Log."""

from __future__ import annotations

from typing import Any, override

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_ACTIVE,
    CONF_ALLOWED_USERS,
    CONF_ARCHIVED,
    CONF_CALENDAR_DETAILS,
    CONF_COLOR,
    CONF_CREATE_SENSOR,
    CONF_EMOJI,
    CONF_FIELD_TYPE,
    CONF_KIND,
    CONF_MAX,
    CONF_MIN,
    CONF_MULTILINE,
    CONF_NAME,
    CONF_OPTIONS,
    CONF_ORDER,
    CONF_STEP,
    CONF_TRACK_TIME,
    CONF_UNIT,
    CONF_WEEK_START,
    DEFAULT_OPTIONS,
    DOMAIN,
    FIELD_BOOLEAN,
    FIELD_NUMBER,
    FIELD_RATING,
    FIELD_SELECT,
    FIELD_TEXT,
    FIELD_TYPES,
    KIND_COUNTER,
    KIND_STREAK,
    KINDS,
    MAX_EMOJI_LENGTH,
    MAX_NAME_LENGTH,
    MAX_SELECT_OPTIONS,
    SUBENTRY_HABIT,
    SUBENTRY_JOURNAL_FIELD,
    TITLE,
    WEEK_START_MONDAY,
    WEEK_START_SUNDAY,
)

ORDER_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(min=0, max=999, step=1, mode=selector.NumberSelectorMode.BOX)
)
NAME_SELECTOR = selector.TextSelector()
EMOJI_SELECTOR = selector.TextSelector()


def _hex_to_rgb(value: str | None) -> list[int] | None:
    if not value or len(value) != 7 or not value.startswith("#"):
        return None
    try:
        return [int(value[i : i + 2], 16) for i in (1, 3, 5)]
    except ValueError:
        return None


def _rgb_to_hex(value: Any) -> str | None:
    if not value:
        return None
    r, g, b = (max(0, min(255, int(c))) for c in value)
    return f"#{r:02x}{g:02x}{b:02x}"


def _name_error(name: str, existing: list[str]) -> str | None:
    if not name:
        return "name_required"
    if len(name) > MAX_NAME_LENGTH:
        return "name_too_long"
    if name.lower() in (n.lower() for n in existing):
        return "name_exists"
    return None


class HabitLogConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create the single Habit Log entry."""

    VERSION = 1
    MINOR_VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Confirm setup."""
        if user_input is not None:
            return self.async_create_entry(title=TITLE, data={}, options=dict(DEFAULT_OPTIONS))
        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))

    @staticmethod
    @callback
    @override
    def async_get_options_flow(config_entry: ConfigEntry) -> HabitLogOptionsFlow:
        """Return the options flow."""
        return HabitLogOptionsFlow()

    @classmethod
    @callback
    @override
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return supported subentry types."""
        return {
            SUBENTRY_HABIT: HabitSubentryFlow,
            SUBENTRY_JOURNAL_FIELD: JournalFieldSubentryFlow,
        }


class HabitLogOptionsFlow(OptionsFlow):
    """Integration-wide options."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage options."""
        users = [u for u in await self.hass.auth.async_get_users() if not u.system_generated and u.is_active]
        user_options = [selector.SelectOptionDict(value=u.id, label=u.name or u.id) for u in users]
        valid_ids = {u.id for u in users}

        if user_input is not None:
            options = {
                CONF_ALLOWED_USERS: [
                    uid for uid in user_input.get(CONF_ALLOWED_USERS, []) if uid in valid_ids
                ],
                CONF_WEEK_START: user_input[CONF_WEEK_START],
                CONF_CALENDAR_DETAILS: user_input[CONF_CALENDAR_DETAILS],
            }
            return self.async_create_entry(data=options)

        current = {**DEFAULT_OPTIONS, **self.config_entry.options}
        schema = vol.Schema(
            {
                vol.Optional(CONF_ALLOWED_USERS): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=user_options,
                        multiple=True,
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
                vol.Required(CONF_WEEK_START): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[WEEK_START_MONDAY, WEEK_START_SUNDAY],
                        translation_key=CONF_WEEK_START,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(CONF_CALENDAR_DETAILS): selector.BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                schema,
                {
                    CONF_ALLOWED_USERS: [u for u in current[CONF_ALLOWED_USERS] if u in valid_ids],
                    CONF_WEEK_START: current[CONF_WEEK_START],
                    CONF_CALENDAR_DETAILS: current[CONF_CALENDAR_DETAILS],
                },
            ),
        )


class _BaseSubentryFlow(ConfigSubentryFlow):
    """Shared helpers."""

    def _other_titles(self, subentry_type: str, exclude: str | None = None) -> list[str]:
        return [
            s.title
            for s in self._get_entry().subentries.values()
            if s.subentry_type == subentry_type and s.subentry_id != exclude
        ]

    def _entry_loaded(self) -> bool:
        return self._get_entry().state is ConfigEntryState.LOADED


class HabitSubentryFlow(_BaseSubentryFlow):
    """Add or edit a habit."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Add a habit."""
        return await self._async_step_form("user", user_input, None)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Edit a habit."""
        return await self._async_step_form("reconfigure", user_input, self._get_reconfigure_subentry())

    async def _async_step_form(
        self, step_id: str, user_input: dict[str, Any] | None, subentry: Any
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        is_new = subentry is None

        if user_input is not None:
            name = str(user_input.get(CONF_NAME, "")).strip()
            emoji = str(user_input.get(CONF_EMOJI, "") or "").strip()
            kind = user_input[CONF_KIND]
            track_time = bool(user_input.get(CONF_TRACK_TIME, False))
            exclude = None if is_new else subentry.subentry_id
            if err := _name_error(name, self._other_titles(SUBENTRY_HABIT, exclude)):
                errors[CONF_NAME] = err
            if len(emoji) > MAX_EMOJI_LENGTH:
                errors[CONF_EMOJI] = "emoji_too_long"
            if kind not in KINDS:
                errors[CONF_KIND] = "invalid_kind"
            if track_time and kind == KIND_STREAK:
                errors[CONF_TRACK_TIME] = "streak_time_tracking"
            if (
                not is_new
                and not errors
                and not (track_time and kind == KIND_COUNTER)
                and self._entry_loaded()
                and self._get_entry().runtime_data.store.habit_has_timed_entries(subentry.subentry_id)
            ):
                errors[CONF_TRACK_TIME] = "has_timed_entries"

            if not errors:
                data = {
                    CONF_EMOJI: emoji,
                    CONF_COLOR: _rgb_to_hex(user_input.get(CONF_COLOR)),
                    CONF_KIND: kind,
                    CONF_TRACK_TIME: track_time and kind == KIND_COUNTER,
                    CONF_ORDER: int(user_input.get(CONF_ORDER, 0)),
                    CONF_ARCHIVED: bool(user_input.get(CONF_ARCHIVED, False)),
                }
                if is_new:
                    return self.async_create_entry(title=name, data=data)
                return self.async_update_and_abort(self._get_entry(), subentry, title=name, data=data)

        schema_dict: dict[Any, Any] = {
            vol.Required(CONF_NAME): NAME_SELECTOR,
            vol.Optional(CONF_EMOJI): EMOJI_SELECTOR,
            vol.Optional(CONF_COLOR): selector.ColorRGBSelector(),
            vol.Required(CONF_KIND): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(KINDS),
                    translation_key=CONF_KIND,
                    mode=selector.SelectSelectorMode.LIST,
                )
            ),
            vol.Required(CONF_TRACK_TIME): selector.BooleanSelector(),
            vol.Required(CONF_ORDER): ORDER_SELECTOR,
        }
        if not is_new:
            schema_dict[vol.Required(CONF_ARCHIVED)] = selector.BooleanSelector()

        if user_input is not None:
            suggested = user_input
        elif is_new:
            suggested = {CONF_KIND: KIND_COUNTER, CONF_TRACK_TIME: False, CONF_ORDER: 0}
        else:
            data = subentry.data
            suggested = {
                CONF_NAME: subentry.title,
                CONF_EMOJI: data.get(CONF_EMOJI, ""),
                CONF_KIND: data.get(CONF_KIND, KIND_COUNTER),
                CONF_TRACK_TIME: data.get(CONF_TRACK_TIME, False),
                CONF_ORDER: data.get(CONF_ORDER, 0),
                CONF_ARCHIVED: data.get(CONF_ARCHIVED, False),
            }
            if rgb := _hex_to_rgb(data.get(CONF_COLOR)):
                suggested[CONF_COLOR] = rgb

        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(vol.Schema(schema_dict), suggested),
            errors=errors,
        )


class JournalFieldSubentryFlow(_BaseSubentryFlow):
    """Add or edit a journal field (two steps: basics, then type details)."""

    def __init__(self) -> None:
        """Initialize."""
        super().__init__()
        self._basics: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Add a field: basics."""
        return await self._async_basics("user", user_input, None)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Edit a field: basics."""
        return await self._async_basics("reconfigure", user_input, self._get_reconfigure_subentry())

    async def _async_basics(
        self, step_id: str, user_input: dict[str, Any] | None, subentry: Any
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        is_new = subentry is None
        if user_input is not None:
            name = str(user_input.get(CONF_NAME, "")).strip()
            exclude = None if is_new else subentry.subentry_id
            if err := _name_error(name, self._other_titles(SUBENTRY_JOURNAL_FIELD, exclude)):
                errors[CONF_NAME] = err
            if user_input.get(CONF_FIELD_TYPE) not in FIELD_TYPES:
                errors[CONF_FIELD_TYPE] = "invalid_field_type"
            if not errors:
                self._basics = {
                    CONF_NAME: name,
                    CONF_FIELD_TYPE: user_input[CONF_FIELD_TYPE],
                    CONF_ORDER: int(user_input.get(CONF_ORDER, 0)),
                    CONF_ACTIVE: bool(user_input.get(CONF_ACTIVE, True)),
                }
                return await self.async_step_details()

        schema_dict: dict[Any, Any] = {
            vol.Required(CONF_NAME): NAME_SELECTOR,
            vol.Required(CONF_FIELD_TYPE): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(FIELD_TYPES),
                    translation_key=CONF_FIELD_TYPE,
                    mode=selector.SelectSelectorMode.LIST,
                )
            ),
            vol.Required(CONF_ORDER): ORDER_SELECTOR,
        }
        if not is_new:
            schema_dict[vol.Required(CONF_ACTIVE)] = selector.BooleanSelector()

        if user_input is not None:
            suggested = user_input
        elif is_new:
            suggested = {CONF_FIELD_TYPE: FIELD_RATING, CONF_ORDER: 0}
        else:
            suggested = {
                CONF_NAME: subentry.title,
                CONF_FIELD_TYPE: subentry.data[CONF_FIELD_TYPE],
                CONF_ORDER: subentry.data.get(CONF_ORDER, 0),
                CONF_ACTIVE: subentry.data.get(CONF_ACTIVE, True),
            }
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(vol.Schema(schema_dict), suggested),
            errors=errors,
        )

    async def async_step_details(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Type-specific settings."""
        field_type = self._basics[CONF_FIELD_TYPE]
        is_new = self.source == "user"
        subentry = None if is_new else self._get_reconfigure_subentry()
        errors: dict[str, str] = {}

        if field_type == FIELD_BOOLEAN:
            # Nothing to configure.
            return self._finish({}, subentry)

        if user_input is not None:
            details: dict[str, Any] = {}
            if field_type == FIELD_NUMBER:
                minimum = user_input.get(CONF_MIN)
                maximum = user_input.get(CONF_MAX)
                step = user_input.get(CONF_STEP)
                if minimum is not None and maximum is not None and minimum >= maximum:
                    errors[CONF_MAX] = "max_not_above_min"
                if step is not None and step <= 0:
                    errors[CONF_STEP] = "step_not_positive"
                details = {
                    CONF_UNIT: str(user_input.get(CONF_UNIT, "") or "").strip()[:16],
                    CONF_MIN: minimum,
                    CONF_MAX: maximum,
                    CONF_STEP: step,
                    CONF_CREATE_SENSOR: bool(user_input.get(CONF_CREATE_SENSOR, False)),
                }
            elif field_type == FIELD_RATING:
                details = {
                    CONF_MAX: int(user_input[CONF_MAX]),
                    CONF_CREATE_SENSOR: bool(user_input.get(CONF_CREATE_SENSOR, False)),
                }
            elif field_type == FIELD_SELECT:
                options: list[str] = []
                for option in user_input.get(CONF_OPTIONS, []):
                    cleaned = str(option).strip()[:MAX_NAME_LENGTH]
                    if cleaned and cleaned not in options:
                        options.append(cleaned)
                if not options:
                    errors[CONF_OPTIONS] = "options_required"
                elif len(options) > MAX_SELECT_OPTIONS:
                    errors[CONF_OPTIONS] = "too_many_options"
                details = {CONF_OPTIONS: options}
            elif field_type == FIELD_TEXT:
                details = {CONF_MULTILINE: bool(user_input.get(CONF_MULTILINE, False))}
            if not errors:
                return self._finish(details, subentry)

        previous: dict[str, Any] = {}
        if subentry is not None and subentry.data.get(CONF_FIELD_TYPE) == field_type:
            previous = dict(subentry.data)

        number_box = selector.NumberSelector(
            selector.NumberSelectorConfig(step="any", mode=selector.NumberSelectorMode.BOX)
        )
        if field_type == FIELD_NUMBER:
            schema = vol.Schema(
                {
                    vol.Optional(CONF_UNIT): selector.TextSelector(),
                    vol.Optional(CONF_MIN): number_box,
                    vol.Optional(CONF_MAX): number_box,
                    vol.Optional(CONF_STEP): number_box,
                    vol.Required(CONF_CREATE_SENSOR): selector.BooleanSelector(),
                }
            )
            suggested = {CONF_CREATE_SENSOR: False, CONF_STEP: 0.1, **previous}
        elif field_type == FIELD_RATING:
            schema = vol.Schema(
                {
                    vol.Required(CONF_MAX): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=2, max=10, step=1, mode=selector.NumberSelectorMode.SLIDER
                        )
                    ),
                    vol.Required(CONF_CREATE_SENSOR): selector.BooleanSelector(),
                }
            )
            suggested = {CONF_MAX: 5, CONF_CREATE_SENSOR: True, **previous}
        elif field_type == FIELD_SELECT:
            schema = vol.Schema(
                {
                    vol.Required(CONF_OPTIONS): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=list(previous.get(CONF_OPTIONS, [])),
                            multiple=True,
                            custom_value=True,
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    )
                }
            )
            suggested = {CONF_OPTIONS: list(previous.get(CONF_OPTIONS, []))}
        else:  # FIELD_TEXT
            schema = vol.Schema({vol.Required(CONF_MULTILINE): selector.BooleanSelector()})
            suggested = {CONF_MULTILINE: True, **previous}

        if user_input is not None:
            suggested = user_input
        return self.async_show_form(
            step_id="details",
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            errors=errors,
            description_placeholders={"field_type": field_type},
        )

    def _finish(self, details: dict[str, Any], subentry: Any) -> SubentryFlowResult:
        data = {
            CONF_FIELD_TYPE: self._basics[CONF_FIELD_TYPE],
            CONF_ORDER: self._basics[CONF_ORDER],
            CONF_ACTIVE: self._basics[CONF_ACTIVE],
            **details,
        }
        title = self._basics[CONF_NAME]
        if subentry is None:
            return self.async_create_entry(title=title, data=data)
        return self.async_update_and_abort(self._get_entry(), subentry, title=title, data=data)
