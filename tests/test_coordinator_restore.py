"""What the coordinator does with a restored result: first-run force flags, the 7-day edge, running."""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker, AiohttpClientMockResponse

from custom_components.gutcheck.const import API_URL, DOMAIN, OPTION_WORTH_FIXING, RECIPE_HEALTH, RECIPE_UPDATES
from custom_components.gutcheck.coordinator import RecipeCoordinator

from .conftest import (
    api_response,
    choice_answer,
    posted_bodies,
    register_jev_responses,
    register_pending_update,
    register_unavailable_entity,
    score_answer,
)


async def _restored_updates_coordinator(
    hass: HomeAssistant, freezer: Any, aioclient_mock: AiohttpClientMocker
) -> RecipeCoordinator:
    """Run the update recipe once, restart two days later so the result is restored, and clear the request log."""
    freezer.move_to("2026-01-01T00:00:00-08:00")
    register_pending_update(hass, "update_a")
    register_jev_responses(
        aioclient_mock, [api_response({"u0": score_answer(0, 0.9)}), api_response({"u0": score_answer(0, 0.9)})]
    )
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: "test-key"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(posted_bodies(aioclient_mock)) == 1

    freezer.move_to("2026-01-03T00:00:00-08:00")
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(posted_bodies(aioclient_mock)) == 1
    coordinator: RecipeCoordinator = entry.runtime_data.coordinators[RECIPE_UPDATES]
    assert coordinator.data is not None
    assert coordinator.running is False
    return coordinator


async def test_the_first_run_after_a_restore_keeps_an_unchanged_answer(
    hass: HomeAssistant, freezer: Any, aioclient_mock: AiohttpClientMocker
) -> None:
    """Nothing asked a full re-score, so an unchanged accepted update is not sent again."""
    coordinator = await _restored_updates_coordinator(hass, freezer, aioclient_mock)

    await coordinator.async_refresh()

    assert len(posted_bodies(aioclient_mock)) == 1
    assert coordinator.running is False


async def test_a_full_rescore_requested_after_a_restore_asks_again(
    hass: HomeAssistant, freezer: Any, aioclient_mock: AiohttpClientMocker
) -> None:
    """The first full re-score after a restart is honored even though no run has finished yet."""
    coordinator = await _restored_updates_coordinator(hass, freezer, aioclient_mock)

    coordinator.force_full_rescore()
    await coordinator.async_refresh()

    assert len(posted_bodies(aioclient_mock)) == 2


async def test_a_result_exactly_seven_days_old_is_not_restored(
    hass: HomeAssistant, freezer: Any, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """At exactly the interval the run starts from nothing, so no restored data shows while its request is open."""
    freezer.move_to("2026-01-01T00:00:00-08:00")
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

    freezer.move_to("2026-01-08T00:00:00-08:00")
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await asyncio.wait_for(started.wait(), 5)

    coordinator = mock_config_entry.runtime_data.coordinators[RECIPE_HEALTH]
    assert coordinator.data is None
    assert coordinator.running is True
    hold.set()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert coordinator.running is False
