"""Config flow for Gut Check: one API key, validated with one cheap question."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .client import GutCheckApiError, GutCheckAuthError, GutCheckClient
from .const import DOMAIN
from .options_flow import GutCheckOptionsFlow

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_KEY): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
    }
)


async def async_validate_api_key(hass: HomeAssistant, api_key: str) -> str | None:
    """Validate the key with one cheap question; return an error code or None."""
    client = GutCheckClient(async_get_clientsession(hass), api_key)
    try:
        await client.async_validate_key()
    except GutCheckAuthError:
        return "invalid_auth"
    except GutCheckApiError:
        return "cannot_connect"
    return None


class GutCheckConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Gut Check."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> GutCheckOptionsFlow:
        """Return the options flow for a Gut Check config entry."""
        return GutCheckOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Collect the API key and validate it before creating the entry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            error = await async_validate_api_key(self.hass, api_key)
            if error is None:
                return self.async_create_entry(title="Gut Check", data={CONF_API_KEY: api_key})
            errors["base"] = error

        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start reauth after the stored key is rejected during a run."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Validate a replacement key and update the existing entry with it."""
        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            error = await async_validate_api_key(self.hass, api_key)
            if error is None:
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(),
                    data_updates={CONF_API_KEY: api_key},
                )
            errors["base"] = error

        return self.async_show_form(step_id="reauth_confirm", data_schema=STEP_USER_SCHEMA, errors=errors)
