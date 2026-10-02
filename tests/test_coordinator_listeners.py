"""A failed run redraws the sensor once, whether it follows a success or another failure."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import OPTION_WORTH_FIXING, RECIPE_HEALTH

from .conftest import api_response, choice_answer, register_jev_responses, register_unavailable_entity


async def test_each_failed_run_notifies_listeners_exactly_once(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """The first failure after a success and a second failure in a row each redraw once."""
    register_unavailable_entity(hass)
    register_jev_responses(aioclient_mock, [api_response({"e0": choice_answer(OPTION_WORTH_FIXING, 0.9)})])
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    coordinator = mock_config_entry.runtime_data.coordinators[RECIPE_HEALTH]
    redraws: list[bool] = []
    coordinator.async_add_listener(lambda: redraws.append(coordinator.last_update_success))

    aioclient_mock.clear_requests()
    register_jev_responses(aioclient_mock, [(500, {"error": "boom"}), (500, {"error": "boom"})])
    coordinator.force_full_rescore()
    await coordinator.async_refresh()
    assert redraws == [False]

    await coordinator.async_refresh()
    assert redraws == [False, False]
