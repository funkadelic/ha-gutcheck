"""Tracer test: config entry to a filled Home health check sensor."""

from __future__ import annotations

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, STATE_UNAVAILABLE
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import (
    CONF_DAILY_BUDGET,
    DEFAULT_DAILY_BUDGET,
    OPTION_EXPECTED,
    OPTION_NONE,
    OPTION_SAFE_TO_REMOVE,
    OPTION_WORTH_FIXING,
    RECIPE_HEALTH,
)
from custom_components.gutcheck.recipes.health_const import LEAN_NEEDS_ATTENTION

from .conftest import (
    api_response,
    choice_answer,
    posted_bodies,
    recipe_sensor_entity_id,
    register_jev_responses,
)


def _register_entities(hass: HomeAssistant) -> str:
    """Register one selectable sensor, one lock and one available sensor."""
    registry = er.async_get(hass)

    selectable = registry.async_get_or_create("sensor", "test", "unique_selectable")
    hass.states.async_set(selectable.entity_id, STATE_UNAVAILABLE)

    lock = registry.async_get_or_create("lock", "test", "unique_lock")
    hass.states.async_set(lock.entity_id, STATE_UNAVAILABLE)

    available = registry.async_get_or_create("sensor", "test", "unique_available")
    hass.states.async_set(available.entity_id, "20")

    return selectable.entity_id


async def test_no_post_before_startup_completes(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Setup waits for HA startup before sending anything."""
    hass.set_state(CoreState.not_running)
    _register_entities(hass)
    register_jev_responses(aioclient_mock, [api_response({"e0": choice_answer(OPTION_WORTH_FIXING, 0.9)})])
    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert posted_bodies(aioclient_mock) == []


async def test_one_batched_post_fills_the_sensor(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """After startup, one POST classifies the selected entities and fills the sensor."""
    hass.set_state(CoreState.not_running)
    _register_entities(hass)
    register_jev_responses(aioclient_mock, [api_response({"e0": choice_answer(OPTION_WORTH_FIXING, 0.9)})])
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done(wait_background_tasks=True)

    bodies = posted_bodies(aioclient_mock)
    assert len(bodies) == 1
    body = bodies[0]
    assert set(body["questions"].keys()) == {"e0"}
    assert len(body["state"]["entities"]) == 1

    state = hass.states.get(recipe_sensor_entity_id(hass, mock_config_entry, RECIPE_HEALTH))
    assert state is not None
    assert state.state == "1"
    assert state.attributes["counts"][OPTION_WORTH_FIXING] == 1
    assert state.attributes["last_payload"] == body

    budget = mock_config_entry.runtime_data.budget
    assert budget.daily_budget == DEFAULT_DAILY_BUDGET
    assert budget.spent_today == 10


async def test_unsure_entity_carries_its_lean_in_the_sensor(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """An unsure answer whose probabilities clearly lean toward needing attention carries that lean."""
    hass.set_state(CoreState.not_running)
    _register_entities(hass)
    answer = {
        "type": "choice",
        "choice": OPTION_WORTH_FIXING,
        "confidence": 0.35,
        "probabilities": {
            OPTION_EXPECTED: 0.2,
            OPTION_WORTH_FIXING: 0.45,
            OPTION_SAFE_TO_REMOVE: 0.3,
            OPTION_NONE: 0.05,
        },
    }
    register_jev_responses(aioclient_mock, [api_response({"e0": answer})])
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done(wait_background_tasks=True)

    state = hass.states.get(recipe_sensor_entity_id(hass, mock_config_entry, RECIPE_HEALTH))
    assert state is not None
    assert state.state == "0"
    assert sum(state.attributes["counts"].values()) == 0
    assert state.attributes["unsure"][0]["lean"] == LEAN_NEEDS_ATTENTION


async def test_budget_refusal_leaves_sensor_unavailable_with_no_post(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A daily budget of 1 token refuses the run before any request is sent."""
    hass.set_state(CoreState.not_running)
    _register_entities(hass)
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(mock_config_entry, options={CONF_DAILY_BUDGET: 1})
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert posted_bodies(aioclient_mock) == []
    state = hass.states.get(recipe_sensor_entity_id(hass, mock_config_entry, RECIPE_HEALTH))
    assert state is not None
    assert state.state == "unavailable"


async def test_api_failure_then_recovery(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A failed API call makes the sensor unavailable; a later success recovers it."""
    hass.set_state(CoreState.not_running)
    _register_entities(hass)
    register_jev_responses(aioclient_mock, [(500, {"error": "boom"})])
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done(wait_background_tasks=True)

    state = hass.states.get(recipe_sensor_entity_id(hass, mock_config_entry, RECIPE_HEALTH))
    assert state is not None
    assert state.state == "unavailable"

    aioclient_mock.clear_requests()
    register_jev_responses(aioclient_mock, [api_response({"e0": choice_answer(OPTION_WORTH_FIXING, 0.9)})])
    coordinator = next(iter(mock_config_entry.runtime_data.coordinators.values()))
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    state = hass.states.get(recipe_sensor_entity_id(hass, mock_config_entry, RECIPE_HEALTH))
    assert state is not None
    assert state.state == "1"
