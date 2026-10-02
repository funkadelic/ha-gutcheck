"""The config and reauth forms name their step and schema, and reauth validates the key typed."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.config_flow import STEP_USER_SCHEMA
from custom_components.gutcheck.const import DOMAIN

from .conftest import api_response, register_jev_responses


async def test_the_user_form_names_its_step_and_key_field(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """The first form and the form redrawn after a rejected key are both the user step with the key field."""
    register_jev_responses(aioclient_mock, [(401, {"error": "unauthorized"})])
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["step_id"] == "user"
    assert result["data_schema"] is STEP_USER_SCHEMA

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: "bad"})

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["data_schema"] is STEP_USER_SCHEMA


async def test_the_reauth_form_names_its_step_and_validates_the_typed_key(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """Reauth shows its own step with the key field, and the key check is sent with the key typed."""
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    assert result["data_schema"] is STEP_USER_SCHEMA
    register_jev_responses(aioclient_mock, [(401, {"error": "unauthorized"}), api_response({})])

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: "typed-key"})
    assert result["step_id"] == "reauth_confirm"
    assert result["data_schema"] is STEP_USER_SCHEMA
    assert aioclient_mock.mock_calls[0][3]["Authorization"] == "Bearer typed-key"
