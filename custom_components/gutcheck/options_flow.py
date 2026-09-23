"""Options flow: toggle the home health check, set the daily budget, pick the critical label."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlowResult, OptionsFlowWithReload
from homeassistant.helpers.selector import BooleanSelector, LabelSelector

from .const import CONF_CRITICAL_LABEL, CONF_DAILY_BUDGET, CONF_HEALTH_ENABLED, CONF_UPDATES_ENABLED, DEFAULT_DAILY_BUDGET

OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HEALTH_ENABLED, default=True): BooleanSelector(),
        vol.Required(CONF_UPDATES_ENABLED, default=True): BooleanSelector(),
        vol.Required(CONF_DAILY_BUDGET, default=DEFAULT_DAILY_BUDGET): vol.All(vol.Coerce(int), vol.Range(min=1)),
        vol.Optional(CONF_CRITICAL_LABEL): LabelSelector(),
    }
)


class GutCheckOptionsFlow(OptionsFlowWithReload):
    """The health check toggle, the daily token budget, and the critical label."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the options form, prefilled with the entry's saved options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        schema = self.add_suggested_values_to_schema(OPTIONS_SCHEMA, self.config_entry.options)
        return self.async_show_form(step_id="init", data_schema=schema)
