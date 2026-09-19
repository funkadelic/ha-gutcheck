"""Tests for the Gut Check config flow."""

from __future__ import annotations

import aiohttp
from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import DOMAIN

from .conftest import posted_bodies, register_jev_responses


async def _start_flow(hass: HomeAssistant, api_key: str = "test-key") -> dict:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    return await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: api_key})


async def test_success_creates_one_entry_and_validates_key(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A valid key creates one entry after exactly one validation POST."""
    response = {
        "model": "jev-latest",
        "answers": {"q": {"type": "noul", "noul": 0.9}},
        "usage": {"input_tokens": 5, "output_tokens": 0},
    }
    register_jev_responses(aioclient_mock, [response])

    result = await _start_flow(hass)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Gut Check"
    assert result["data"][CONF_API_KEY] == "test-key"
    bodies = posted_bodies(aioclient_mock)
    assert len(bodies) == 1
    assert aioclient_mock.mock_calls[0][3]["Authorization"] == "Bearer test-key"


async def test_invalid_key_shows_invalid_auth_and_creates_no_entry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 401 re-shows the form with invalid_auth and creates no entry."""
    register_jev_responses(aioclient_mock, [(401, {"error": "unauthorized"})])

    result = await _start_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert hass.config_entries.async_entries(DOMAIN) == []


async def test_server_error_shows_cannot_connect_and_creates_no_entry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 500 re-shows the form with cannot_connect and creates no entry."""
    register_jev_responses(aioclient_mock, [(500, {"error": "boom"})])

    result = await _start_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert hass.config_entries.async_entries(DOMAIN) == []


async def test_client_error_shows_cannot_connect_and_creates_no_entry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A transport error re-shows the form with cannot_connect and creates no entry."""
    aioclient_mock.post("https://api.typesafe.ai/v1/systemone", exc=aiohttp.ClientConnectionError())

    result = await _start_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert hass.config_entries.async_entries(DOMAIN) == []


async def test_malformed_response_shape_shows_cannot_connect(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A 200 response missing the expected shape re-shows the form with cannot_connect."""
    register_jev_responses(aioclient_mock, [{"model": "jev-latest"}])

    result = await _start_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert hass.config_entries.async_entries(DOMAIN) == []


async def test_non_json_body_shows_cannot_connect(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A 200 response with a non-JSON body re-shows the form with cannot_connect."""
    aioclient_mock.post("https://api.typesafe.ai/v1/systemone", status=200, text="not json")

    result = await _start_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert hass.config_entries.async_entries(DOMAIN) == []


async def test_second_flow_aborts_single_instance_allowed(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A second flow while an entry exists aborts, never reaching the API."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"
    assert posted_bodies(aioclient_mock) == []
