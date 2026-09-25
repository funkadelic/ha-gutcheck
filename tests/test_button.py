"""Tests for the per-recipe Run button."""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker, AiohttpClientMockResponse

from custom_components.gutcheck.const import (
    API_URL,
    BUDGET_STORE_KEY,
    CONF_HEALTH_ENABLED,
    DOMAIN,
    OPTION_WORTH_FIXING,
    RECIPE_HEALTH,
)

from .conftest import (
    api_response,
    choice_answer,
    health_sensor_entity_id,
    posted_bodies,
    register_jev_responses,
    register_unavailable_entity,
)


def _health_button_entity_id(hass: HomeAssistant, entry: MockConfigEntry) -> str | None:
    """The health recipe's Run button entity id, or None if it was not created."""
    registry = er.async_get(hass)
    return registry.async_get_entity_id("button", DOMAIN, f"{entry.entry_id}_{RECIPE_HEALTH}_run")


async def _press(hass: HomeAssistant, entity_id: str) -> None:
    """Press the button and let its background run finish."""
    await hass.services.async_call("button", "press", {"entity_id": entity_id}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_press_after_first_run_sends_exactly_one_more_post(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """With the first run done, pressing Run sends exactly one more POST and updates the sensor."""
    register_unavailable_entity(hass)
    register_jev_responses(aioclient_mock, [api_response({"e0": choice_answer(OPTION_WORTH_FIXING, 0.9)})])
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(posted_bodies(aioclient_mock)) == 1

    button_entity_id = _health_button_entity_id(hass, mock_config_entry)
    assert button_entity_id is not None

    aioclient_mock.clear_requests()
    register_jev_responses(aioclient_mock, [api_response({"e0": choice_answer(OPTION_WORTH_FIXING, 0.9)})])
    await _press(hass, button_entity_id)

    assert len(posted_bodies(aioclient_mock)) == 1
    state = hass.states.get(health_sensor_entity_id(hass, mock_config_entry))
    assert state is not None
    assert state.state == "1"


async def test_button_stays_available_after_a_failed_run_and_a_press_recovers_it(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A 500 leaves the sensor unavailable but the button available; a press with a healthy API recovers it."""
    register_unavailable_entity(hass)
    register_jev_responses(aioclient_mock, [(500, {"error": "boom"})])
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    sensor_state = hass.states.get(health_sensor_entity_id(hass, mock_config_entry))
    assert sensor_state is not None
    assert sensor_state.state == STATE_UNAVAILABLE

    button_entity_id = _health_button_entity_id(hass, mock_config_entry)
    assert button_entity_id is not None
    button_state = hass.states.get(button_entity_id)
    assert button_state is not None
    assert button_state.state != STATE_UNAVAILABLE

    aioclient_mock.clear_requests()
    register_jev_responses(aioclient_mock, [api_response({"e0": choice_answer(OPTION_WORTH_FIXING, 0.9)})])
    await _press(hass, button_entity_id)

    sensor_state = hass.states.get(health_sensor_entity_id(hass, mock_config_entry))
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


async def test_a_reload_mid_press_cancels_the_run_and_keeps_one_budget_count(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    hass_storage: dict[str, Any],
) -> None:
    """A reload while a pressed run's request is held leaves the stored budget and the new gate at one count."""
    register_unavailable_entity(hass)
    started, hold = asyncio.Event(), asyncio.Event()
    calls = 0

    async def _serve(method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        """Answer every POST, holding the second one open until released."""
        nonlocal calls
        calls += 1
        if calls == 2:
            started.set()
            await hold.wait()
        body = api_response({"e0": choice_answer(OPTION_WORTH_FIXING, 0.9)})
        return AiohttpClientMockResponse(method=method, url=url, status=200, json=body)

    aioclient_mock.post(API_URL, side_effect=_serve)
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    old_gate = mock_config_entry.runtime_data.budget
    assert old_gate.spent_today == 10

    button_entity_id = _health_button_entity_id(hass, mock_config_entry)
    assert button_entity_id is not None
    await hass.services.async_call("button", "press", {"entity_id": button_entity_id}, blocking=False)
    await started.wait()
    assert hass_storage[BUDGET_STORE_KEY]["data"]["spent"] > 10

    assert await hass.config_entries.async_reload(mock_config_entry.entry_id)
    new_gate = mock_config_entry.runtime_data.budget
    assert new_gate is not old_gate

    hold.set()
    await hass.async_block_till_done(wait_background_tasks=True)

    assert hass_storage[BUDGET_STORE_KEY]["data"]["spent"] == new_gate.spent_today
    assert new_gate.spent_today == 10


async def test_presses_beyond_one_waiting_run_are_dropped(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Pressing four times while a pressed run is held sends two runs, and a later press runs again."""
    register_unavailable_entity(hass)
    started, hold = asyncio.Event(), asyncio.Event()
    calls = 0

    async def _serve(method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        """Answer every POST, holding the second one open until released."""
        nonlocal calls
        calls += 1
        if calls == 2:
            started.set()
            await hold.wait()
        body = api_response({"e0": choice_answer(OPTION_WORTH_FIXING, 0.9)})
        return AiohttpClientMockResponse(method=method, url=url, status=200, json=body)

    aioclient_mock.post(API_URL, side_effect=_serve)
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    button_entity_id = _health_button_entity_id(hass, mock_config_entry)
    assert button_entity_id is not None

    for _ in range(4):
        await hass.services.async_call("button", "press", {"entity_id": button_entity_id}, blocking=True)
    await started.wait()
    hold.set()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert calls == 3

    await _press(hass, button_entity_id)
    assert calls == 4
