"""Options flow: toggle the seven recipes, set the daily budget and critical label, and change back what Gut Check set."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState, ConfigFlowResult, OptionsFlowWithReload
from homeassistant.helpers.selector import (
    BooleanSelector,
    LabelSelector,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_AREAS_ENABLED,
    CONF_CHANGE_BACK,
    CONF_CONFIG_ENTRIES_ENABLED,
    CONF_CRITICAL_LABEL,
    CONF_CRITICAL_LABEL_ENABLED,
    CONF_DAILY_BUDGET,
    CONF_DEVICE_CLASS_ENABLED,
    CONF_HEALTH_ENABLED,
    CONF_HIDE_DIAGNOSTIC_ENABLED,
    CONF_UNDO_HIDDEN_SENSORS,
    CONF_UNDO_SENSORS,
    CONF_UPDATES_ENABLED,
    DEFAULT_DAILY_BUDGET,
)
from .recipes.applied_records import undo_choices
from .recipes.device_class_undo import async_change_back
from .recipes.hide_diagnostic_undo import async_change_back_hidden

OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HEALTH_ENABLED, default=True): BooleanSelector(),
        vol.Required(CONF_UPDATES_ENABLED, default=True): BooleanSelector(),
        vol.Required(CONF_AREAS_ENABLED, default=True): BooleanSelector(),
        # Off by default, unlike the other three: an upgraded install must
        # not start raising device class cards unasked.
        vol.Required(CONF_DEVICE_CLASS_ENABLED, default=False): BooleanSelector(),
        # Off by default too: an upgraded install must not start raising
        # stuck-integration cards unasked.
        vol.Required(CONF_CONFIG_ENTRIES_ENABLED, default=False): BooleanSelector(),
        # Off by default too: an upgraded install must not start raising
        # critical label cards unasked.
        vol.Required(CONF_CRITICAL_LABEL_ENABLED, default=False): BooleanSelector(),
        # Off by default too: an upgraded install must not start raising
        # diagnostic sensor cards unasked.
        vol.Required(CONF_HIDE_DIAGNOSTIC_ENABLED, default=False): BooleanSelector(),
        vol.Required(CONF_DAILY_BUDGET, default=DEFAULT_DAILY_BUDGET): vol.All(vol.Coerce(int), vol.Range(min=1)),
        vol.Optional(CONF_CRITICAL_LABEL): LabelSelector(),
    }
)


class GutCheckOptionsFlow(OptionsFlowWithReload):
    """The seven recipe toggles, the daily token budget, the critical label, and the change-back of what Gut Check set."""

    _held_options: dict[str, Any]

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the options form, plus the change-back checkbox when something is recorded and the entry is loaded."""
        if user_input is not None:
            if user_input.pop(CONF_CHANGE_BACK, False):
                self._held_options = user_input
                return await self.async_step_change_back()
            return self.async_create_entry(data=user_input)

        schema = OPTIONS_SCHEMA
        if self.config_entry.state is ConfigEntryState.LOADED and self._undo_fields():
            schema = schema.extend({vol.Optional(CONF_CHANGE_BACK, default=False): BooleanSelector()})
        schema = self.add_suggested_values_to_schema(schema, self.config_entry.options)
        return self.async_show_form(step_id="init", data_schema=schema)

    def _undo_fields(self) -> dict[str, list[SelectOptionDict]]:
        """The change-back choices per form field, only for a kind that has recorded, still-registered sensors."""
        data = self.config_entry.runtime_data
        fields = {
            CONF_UNDO_SENSORS: undo_choices(self.hass, data.applied),
            CONF_UNDO_HIDDEN_SENSORS: undo_choices(self.hass, data.applied_hidden),
        }
        return {field: choices for field, choices in fields.items() if choices}

    async def async_step_change_back(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """List the recorded, still-registered sensors of each kind, then change the picked ones back."""
        if user_input is not None:
            critical_label = self._held_options.get(CONF_CRITICAL_LABEL)
            await async_change_back(self.hass, self.config_entry, user_input.get(CONF_UNDO_SENSORS, []), critical_label)
            await async_change_back_hidden(
                self.hass, self.config_entry, user_input.get(CONF_UNDO_HIDDEN_SENSORS, []), critical_label
            )
            return self.async_create_entry(data=self._held_options)

        fields = self._undo_fields()
        if not fields:
            return self.async_create_entry(data=self._held_options)
        schema = vol.Schema(
            {
                vol.Optional(field, default=[]): SelectSelector(
                    SelectSelectorConfig(options=choices, multiple=True, mode=SelectSelectorMode.LIST)
                )
                for field, choices in fields.items()
            }
        )
        return self.async_show_form(step_id="change_back", data_schema=schema)
