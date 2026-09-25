"""Timeline test for a reload that lands while the weekly catch-up run is in flight."""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker, AiohttpClientMockResponse

from custom_components.gutcheck.const import API_URL, BUDGET_STORE_KEY, OPTION_WORTH_FIXING

from .conftest import api_response, choice_answer, register_unavailable_entity


async def test_a_reload_mid_catch_up_cancels_the_run_and_keeps_one_budget_count(
    hass: HomeAssistant,
    freezer: Any,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    hass_storage: dict[str, Any],
) -> None:
    """A reload while the catch-up run's request is held leaves the stored budget and the new gate in step."""
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

    # A restart two days later restores the run and arms the catch-up for Jan 8.
    freezer.move_to("2026-01-03T00:00:00-08:00")
    assert await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert calls == 1

    freezer.move_to("2026-01-08T00:00:05-08:00")
    await asyncio.wait_for(started.wait(), 5)
    old_gate = mock_config_entry.runtime_data.budget
    assert hass_storage[BUDGET_STORE_KEY]["data"]["spent"] > 0

    assert await hass.config_entries.async_reload(mock_config_entry.entry_id)
    new_gate = mock_config_entry.runtime_data.budget
    assert new_gate is not old_gate

    hold.set()
    await hass.async_block_till_done(wait_background_tasks=True)

    assert hass_storage[BUDGET_STORE_KEY]["data"]["spent"] == new_gate.spent_today
    assert new_gate.spent_today == 10
