"""A run the daily budget refused retries a minute after the local midnight reset, not before."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import OPTION_WORTH_FIXING, RECIPE_HEALTH

from .conftest import api_response, choice_answer, posted_bodies, register_jev_responses, register_unavailable_entity


async def test_a_budget_refused_run_is_retried_a_minute_after_local_midnight(
    hass: HomeAssistant, freezer: Any, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """Refused at 23:00, retry_after is 3660 s; nothing is sent before 00:01, one request goes out after it."""
    freezer.move_to("2026-01-01T23:00:00-08:00")
    register_unavailable_entity(hass)
    answer = api_response({"e0": choice_answer(OPTION_WORTH_FIXING, 0.9)})
    register_jev_responses(aioclient_mock, [answer, answer])
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    coordinator = mock_config_entry.runtime_data.coordinators[RECIPE_HEALTH]
    assert len(posted_bodies(aioclient_mock)) == 1

    coordinator.force_full_rescore()
    coordinator.budget._data["spent"] = coordinator.budget.daily_budget
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert isinstance(coordinator.last_exception, UpdateFailed)
    assert coordinator.last_exception.retry_after == 3660.0
    assert coordinator.last_update_success is False

    # Past midnight the budget has reset, but the retry is not due for another 30 s.
    freezer.move_to("2026-01-02T00:00:30-08:00")
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(posted_bodies(aioclient_mock)) == 1

    freezer.move_to("2026-01-02T00:01:30-08:00")
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(posted_bodies(aioclient_mock)) == 2
    assert coordinator.last_update_success
