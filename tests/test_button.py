"""Tests for the per-recipe Run button."""

from __future__ import annotations

from typing import Any

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import CONF_HEALTH_ENABLED, DOMAIN, OPTION_WORTH_FIXING, RECIPE_HEALTH

from .conftest import choice_answer, posted_bodies, register_jev_responses


def _api_response(answers: dict[str, Any], input_tokens: int = 10) -> dict[str, Any]:
    return {
        "model": "jev-latest",
        "answers": answers,
        "usage": {"input_tokens": input_tokens, "output_tokens": 0},
    }


def _register_unavailable(hass: HomeAssistant) -> None:
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("sensor", "test", "unique_selectable")
    hass.states.async_set(entry.entity_id, STATE_UNAVAILABLE)


def _health_sensor_entity_id(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{RECIPE_HEALTH}")
    assert entity_id is not None
    return entity_id


def _health_button_entity_id(hass: HomeAssistant, entry: MockConfigEntry) -> str | None:
    registry = er.async_get(hass)
    return registry.async_get_entity_id("button", DOMAIN, f"{entry.entry_id}_{RECIPE_HEALTH}_run")


async def _press(hass: HomeAssistant, entity_id: str) -> None:
    await hass.services.async_call("button", "press", {"entity_id": entity_id}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_press_after_first_run_sends_exactly_one_more_post(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """With the first run done, pressing Run sends exactly one more POST and updates the sensor."""
    _register_unavailable(hass)
    register_jev_responses(aioclient_mock, [_api_response({"e0": choice_answer(OPTION_WORTH_FIXING, 0.9)})])
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(posted_bodies(aioclient_mock)) == 1

    button_entity_id = _health_button_entity_id(hass, mock_config_entry)
    assert button_entity_id is not None

    aioclient_mock.clear_requests()
    register_jev_responses(aioclient_mock, [_api_response({"e0": choice_answer(OPTION_WORTH_FIXING, 0.9)})])
    await _press(hass, button_entity_id)

    assert len(posted_bodies(aioclient_mock)) == 1
    state = hass.states.get(_health_sensor_entity_id(hass, mock_config_entry))
    assert state is not None
    assert state.state == "1"


async def test_button_stays_available_after_a_failed_run_and_a_press_recovers_it(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A 500 leaves the sensor unavailable but the button available; a press with a healthy API recovers it."""
    _register_unavailable(hass)
    register_jev_responses(aioclient_mock, [(500, {"error": "boom"})])
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    sensor_state = hass.states.get(_health_sensor_entity_id(hass, mock_config_entry))
    assert sensor_state is not None
    assert sensor_state.state == STATE_UNAVAILABLE

    button_entity_id = _health_button_entity_id(hass, mock_config_entry)
    assert button_entity_id is not None
    button_state = hass.states.get(button_entity_id)
    assert button_state is not None
    assert button_state.state != STATE_UNAVAILABLE

    aioclient_mock.clear_requests()
    register_jev_responses(aioclient_mock, [_api_response({"e0": choice_answer(OPTION_WORTH_FIXING, 0.9)})])
    await _press(hass, button_entity_id)

    sensor_state = hass.states.get(_health_sensor_entity_id(hass, mock_config_entry))
    assert sensor_state is not None
    assert sensor_state.state == "1"


async def test_no_button_entity_when_health_check_is_disabled(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Turning the health check off in options leaves no button entity."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(mock_config_entry, options={CONF_HEALTH_ENABLED: False})
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert _health_button_entity_id(hass, mock_config_entry) is None
