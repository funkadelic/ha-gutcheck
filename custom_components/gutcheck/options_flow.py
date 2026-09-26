"""Options flow: toggle the five recipes, set the daily budget and critical label, and change a device class back."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState, ConfigFlowResult, OptionsFlowWithReload
from homeassistant.helpers.selector import (
    BooleanSelector,
    LabelSelector,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_AREAS_ENABLED,
    CONF_CONFIG_ENTRIES_ENABLED,
    CONF_CRITICAL_LABEL,
    CONF_DAILY_BUDGET,
    CONF_DEVICE_CLASS_ENABLED,
    CONF_HEALTH_ENABLED,
    CONF_UNDO_DEVICE_CLASS,
    CONF_UNDO_SENSORS,
    CONF_UPDATES_ENABLED,
    DEFAULT_DAILY_BUDGET,
)
from .recipes.device_class_undo import async_change_back, undo_choices

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
        vol.Required(CONF_DAILY_BUDGET, default=DEFAULT_DAILY_BUDGET): vol.All(vol.Coerce(int), vol.Range(min=1)),
        vol.Optional(CONF_CRITICAL_LABEL): LabelSelector(),
    }
)


class GutCheckOptionsFlow(OptionsFlowWithReload):
    """The five recipe toggles, the daily token budget, the critical label, and the device class change-back."""

    _held_options: dict[str, Any]

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the options form, plus the change-back checkbox when something is recorded and the entry is loaded."""
        if user_input is not None:
            if user_input.pop(CONF_UNDO_DEVICE_CLASS, False):
                self._held_options = user_input
                return await self.async_step_undo_device_class()
            return self.async_create_entry(data=user_input)

        schema = OPTIONS_SCHEMA
        if self.config_entry.state is ConfigEntryState.LOADED and undo_choices(self.hass, self.config_entry.runtime_data.applied):
            schema = schema.extend({vol.Optional(CONF_UNDO_DEVICE_CLASS, default=False): BooleanSelector()})
        schema = self.add_suggested_values_to_schema(schema, self.config_entry.options)
        return self.async_show_form(step_id="init", data_schema=schema)

    async def async_step_undo_device_class(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """List the recorded, still-registered sensors, then clear the picked ones back."""
        if user_input is not None:
            critical_label = self._held_options.get(CONF_CRITICAL_LABEL)
            await async_change_back(self.hass, self.config_entry, user_input.get(CONF_UNDO_SENSORS, []), critical_label)
            return self.async_create_entry(data=self._held_options)

        choices = undo_choices(self.hass, self.config_entry.runtime_data.applied)
        schema = vol.Schema(
            {
                vol.Optional(CONF_UNDO_SENSORS, default=[]): SelectSelector(
                    SelectSelectorConfig(options=choices, multiple=True, mode=SelectSelectorMode.LIST)
                )
            }
        )
        return self.async_show_form(step_id="undo_device_class", data_schema=schema)
