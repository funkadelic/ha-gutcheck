"""Tests for the reauth flow triggered by a rejected API key."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import CONF_API_KEY, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import (
    API_URL,
    DOMAIN,
    OPTION_WORTH_FIXING,
    RECIPE_HEALTH,
)

from .conftest import (
    api_response,
    choice_answer,
    posted_bodies,
    recipe_sensor_entity_id,
    register_jev_responses,
    register_unavailable_entity,
)

OLD_KEY = "test-key"
NEW_KEY = "new-key"


def _reauth_flow_id(hass: HomeAssistant) -> str:
    """The flow id of the single in-progress reauth flow."""
    flows = hass.config_entries.flow.async_progress()
    reauth_flows = [flow for flow in flows if flow["handler"] == DOMAIN and flow["context"]["source"] == SOURCE_REAUTH]
    assert len(reauth_flows) == 1
    return str(reauth_flows[0]["flow_id"])


async def test_rejected_key_starts_reauth_and_a_valid_key_recovers(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    caplog: Any,
) -> None:
    """A 401 during a run opens reauth; a rejected key changes nothing; a valid key recovers."""
    # The only log line that touches the key path is a DEBUG one, which caplog
    # drops unless the level is lowered. Without this the key assertions below
    # pass even if a key is logged.
    caplog.set_level(logging.DEBUG, logger="custom_components.gutcheck")
    register_unavailable_entity(hass)
    register_jev_responses(
        aioclient_mock,
        [
            (401, {"error": "unauthorized"}),  # initial health run
            (401, {"error": "unauthorized"}),  # reauth confirm, wrong key
            api_response({"q": {"type": "noul", "noul": 0.9}}),  # reauth confirm, valid key
            api_response({"e0": choice_answer(OPTION_WORTH_FIXING, 0.9)}),  # health run after reload
        ],
    )
    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    flow_id = _reauth_flow_id(hass)
    state = hass.states.get(recipe_sensor_entity_id(hass, mock_config_entry, RECIPE_HEALTH))
    assert state is not None
    assert state.state == STATE_UNAVAILABLE

    result = await hass.config_entries.flow.async_configure(flow_id, {CONF_API_KEY: "still-wrong"})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert mock_config_entry.data[CONF_API_KEY] == OLD_KEY

    result = await hass.config_entries.flow.async_configure(flow_id, {CONF_API_KEY: NEW_KEY})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data[CONF_API_KEY] == NEW_KEY

    await hass.async_block_till_done(wait_background_tasks=True)

    bodies = posted_bodies(aioclient_mock)
    assert len(bodies) == 4
    auth_headers = [headers["Authorization"] for _method, url, _data, headers in aioclient_mock.mock_calls if str(url) == API_URL]
    assert auth_headers[-1] == f"Bearer {NEW_KEY}"

    state = hass.states.get(recipe_sensor_entity_id(hass, mock_config_entry, RECIPE_HEALTH))
    assert state is not None
    assert state.state == "1"

    assert OLD_KEY not in caplog.text
    assert NEW_KEY not in caplog.text
