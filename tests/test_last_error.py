"""A summary sensor's last_error follows each failed run, not just the first of a streak."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import ATTR_LAST_ERROR, OPTION_WORTH_FIXING, RECIPE_HEALTH

from .conftest import api_response, choice_answer, recipe_sensor_entity_id, register_jev_responses, register_unavailable_entity


async def test_a_second_failure_with_another_cause_replaces_last_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """A 500 and then a budget refusal each show their own reason, and the result is kept throughout."""
    register_unavailable_entity(hass)
    register_jev_responses(aioclient_mock, [api_response({"e0": choice_answer(OPTION_WORTH_FIXING, 0.9)})])
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    coordinator = mock_config_entry.runtime_data.coordinators[RECIPE_HEALTH]
    sensor_id = recipe_sensor_entity_id(hass, mock_config_entry, RECIPE_HEALTH)

    aioclient_mock.clear_requests()
    register_jev_responses(aioclient_mock, [(500, {"error": "boom"})])
    coordinator.force_full_rescore()
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    state = hass.states.get(sensor_id)
    assert state is not None
    assert state.state == "1"
    assert state.attributes[ATTR_LAST_ERROR] == "recipe run failed"

    # No public setter; spend today's whole budget so the next run is refused.
    coordinator.budget._data["spent"] = coordinator.budget.daily_budget
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    state = hass.states.get(sensor_id)
    assert state is not None
    assert state.state == "1"
    assert state.attributes[ATTR_LAST_ERROR] == "daily budget reached"
