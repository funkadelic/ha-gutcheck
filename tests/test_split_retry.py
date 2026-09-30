"""A run split into several requests keeps the answers already billed when a later request fails."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.budget import BudgetGate
from custom_components.gutcheck.client import GutCheckClient
from custom_components.gutcheck.const import (
    FAILED_RUN_RETRY,
    OPTION_SUGGESTED,
    RECIPE_HIDE_DIAGNOSTIC,
    SUBJECTS_PER_REQUEST,
)
from custom_components.gutcheck.recipes.hide_diagnostic_const import OPTION_DIAGNOSTIC, OPTION_PRIMARY

from .conftest import (
    api_response,
    hide_diagnostic_answer,
    posted_bodies,
    press_recipe_run,
    register_jev_responses,
    register_unit_sensor,
)

COUNT = 25
OVERLOADED = (529, {"error": "overloaded"})
# The client's first try plus its three retries.
EXHAUSTED_529 = [OVERLOADED] * 4


def _answer(index: int) -> dict[str, Any]:
    """Confident diagnostic for the first slice, confident primary for the second, unsure for the third."""
    if index < SUBJECTS_PER_REQUEST:
        return hide_diagnostic_answer(OPTION_DIAGNOSTIC, 0.9)
    if index < 2 * SUBJECTS_PER_REQUEST:
        return hide_diagnostic_answer(OPTION_PRIMARY, 0.9)
    return hide_diagnostic_answer(OPTION_DIAGNOSTIC, 0.2)


def _slice(start: int, input_tokens: int) -> dict[str, Any]:
    """The response to the request starting at subject start, reporting input_tokens spent."""
    end = min(COUNT, start + SUBJECTS_PER_REQUEST)
    return api_response({f"h{index}": _answer(index) for index in range(start, end)}, input_tokens)


def _first_question_ids(aioclient_mock: AiohttpClientMocker) -> list[str]:
    """The first question id of every request posted, in order."""
    return [next(iter(body["questions"])) for body in posted_bodies(aioclient_mock)]


async def _fail_then_retry(hass: HomeAssistant, entry: MockConfigEntry, before_retry: Any = None) -> tuple[dict[str, Any], int]:
    """Set up the entry (whose first run fails on request two), optionally change something, then let the retry fire."""
    entry.add_to_hass(hass)
    with patch("custom_components.gutcheck.client.asyncio.sleep", new_callable=AsyncMock):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        coordinator = entry.runtime_data.coordinators[RECIPE_HIDE_DIAGNOSTIC]
        assert coordinator.data is None
        assert not coordinator.last_update_success
        if before_retry is not None:
            before_retry()
        async_fire_time_changed(hass, dt_util.utcnow() + FAILED_RUN_RETRY + timedelta(seconds=1))
        await hass.async_block_till_done(wait_background_tasks=True)
    assert coordinator.last_update_success
    return dict(coordinator.data), entry.runtime_data.budget.spent_today


async def test_a_retry_after_a_mid_run_failure_sends_only_the_unanswered_requests(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hide_diagnostic_entry: MockConfigEntry
) -> None:
    """Request two fails with an exhausted 529; the retry sends two and three only, bills each once, classifies as a clean run."""
    for index in range(COUNT):
        register_unit_sensor(hass, f"s{index:02d}", unit=None, name=f"Sensor {index}", device_name=f"Device {index}")
    register_jev_responses(aioclient_mock, [_slice(0, 100), *EXHAUSTED_529, _slice(10, 200), _slice(20, 300)])

    result, spent = await _fail_then_retry(hass, hide_diagnostic_entry)

    assert _first_question_ids(aioclient_mock) == ["h0", *["h10"] * 4, "h10", "h20"]
    assert spent == 100 + 200 + 300
    assert result["counts"][OPTION_SUGGESTED] == SUBJECTS_PER_REQUEST
    assert result["counts"][OPTION_PRIMARY] == SUBJECTS_PER_REQUEST
    assert len(result["unsure"]) == COUNT - 2 * SUBJECTS_PER_REQUEST
    bodies = posted_bodies(aioclient_mock)
    assert result["last_payload"] == [bodies[0], bodies[5], bodies[6]]


async def test_the_run_after_a_successful_retry_sends_and_bills_every_request(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hide_diagnostic_entry: MockConfigEntry
) -> None:
    """Answers kept across the failure are dropped once the run succeeds, so an identical later run asks again."""
    for index in range(COUNT):
        register_unit_sensor(hass, f"s{index:02d}", unit=None, name=f"Sensor {index}", device_name=f"Device {index}")
    clean = [_slice(0, 100), _slice(10, 200), _slice(20, 300)]
    register_jev_responses(aioclient_mock, [clean[0], *EXHAUSTED_529, *clean[1:], *clean])
    await _fail_then_retry(hass, hide_diagnostic_entry)

    await press_recipe_run(hass, hide_diagnostic_entry, RECIPE_HIDE_DIAGNOSTIC)

    bodies = posted_bodies(aioclient_mock)
    assert bodies[7:] == [bodies[0], bodies[5], bodies[6]]
    assert hide_diagnostic_entry.runtime_data.budget.spent_today == 2 * (100 + 200 + 300)


async def test_a_request_whose_payload_changed_is_sent_again(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hide_diagnostic_entry: MockConfigEntry
) -> None:
    """Renaming a sensor in the answered slice before the retry re-sends that slice rather than reusing the stale answer."""
    sensors = [
        register_unit_sensor(hass, f"s{index:02d}", unit=None, name=f"Sensor {index}", device_name=f"Device {index}")
        for index in range(COUNT)
    ]
    register_jev_responses(aioclient_mock, [_slice(0, 100), *EXHAUSTED_529, _slice(0, 150), _slice(10, 200), _slice(20, 300)])

    def _rename() -> None:
        """Rename the first sensor, which changes the first request's state."""
        er.async_get(hass).async_update_entity(sensors[0].entity_id, name="Renamed sensor")

    result, spent = await _fail_then_retry(hass, hide_diagnostic_entry, _rename)

    assert _first_question_ids(aioclient_mock) == ["h0", *["h10"] * 4, "h0", "h10", "h20"]
    bodies = posted_bodies(aioclient_mock)
    assert bodies[5] != bodies[0]
    assert spent == 100 + 150 + 200 + 300
    assert result["last_payload"] == [bodies[5], bodies[6], bodies[7]]


@pytest.mark.parametrize("spent_over", [False, True])
async def test_nothing_to_send_costs_nothing(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, spent_over: bool) -> None:
    """An empty run returns no responses and posts nothing, even with the day's budget already overspent."""
    gate = BudgetGate(hass, GutCheckClient(async_get_clientsession(hass), "test-key"), 100)
    await gate.async_load()
    if spent_over:
        register_jev_responses(aioclient_mock, [api_response({}, 1000)])
        await gate.async_ask({"state": {"check": "ping"}, "model": "jev-latest", "questions": {}})
        assert gate.spent_today == 1000

    assert await gate.async_ask_all([]) == []

    assert len(posted_bodies(aioclient_mock)) == int(spent_over)
