"""Config flow for Gut Check: one API key, validated with one cheap question."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .client import GutCheckApiError, GutCheckAuthError, GutCheckClient
from .const import DOMAIN

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

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Collect the API key and validate it before creating the entry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            error = await async_validate_api_key(self.hass, user_input[CONF_API_KEY])
            if error is None:
                return self.async_create_entry(title="Gut Check", data=user_input)
            errors["base"] = error

        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors)
