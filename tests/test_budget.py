"""Tests for the budget hard stop: overlap safety, the per-request cap, and midnight retry."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.gutcheck.budget import BudgetExceededError, BudgetGate, RequestTooLargeError, estimate_tokens
from custom_components.gutcheck.client import GutCheckApiError, GutCheckClient
from custom_components.gutcheck.const import API_URL, DOMAIN, OPTION_EXPECTED, RECIPE_HEALTH

from .conftest import choice_answer, posted_bodies, register_jev_responses

PAYLOAD: dict[str, Any] = {
    "state": {"check": "ping"},
    "model": "jev-latest",
    "questions": {"q": {"type": "noul", "instructions": "?"}},
}


def _response(input_tokens: int) -> dict[str, Any]:
    return {
        "model": "jev-latest",
        "answers": {"q": {"type": "noul", "noul": 0.9}},
        "usage": {"input_tokens": input_tokens, "output_tokens": 0},
    }


def _client(hass: HomeAssistant) -> GutCheckClient:
    return GutCheckClient(async_get_clientsession(hass), "test-key")


def _padded_payload(state_chars: int, question_chars: int = 10) -> dict[str, Any]:
    return {
        "state": {"pad": "x" * state_chars},
        "model": "jev-latest",
        "questions": {"q": {"type": "choice", "instructions": "x" * question_chars, "criteria": {"a": None}}},
    }


async def test_overlapping_calls_that_exactly_fit_both_succeed(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Two reservations summing exactly to the budget both reserve and both POST."""
    call_count = 0
    hold_first = asyncio.Event()
    first_started = asyncio.Event()

    async def _side_effect(method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            first_started.set()
            await hold_first.wait()
            return AiohttpClientMockResponse(method=method, url=url, status=200, json=_response(300))
        return AiohttpClientMockResponse(method=method, url=url, status=200, json=_response(450))

    aioclient_mock.post(API_URL, side_effect=_side_effect)

    payload = _padded_payload(2_000)
    estimate = estimate_tokens(payload)
    gate = BudgetGate(hass, _client(hass), daily_budget=2 * estimate)
    await gate.async_load()

    task_a = asyncio.create_task(gate.async_ask(payload))
    await first_started.wait()
    result_b = await gate.async_ask(payload)
    hold_first.set()
    result_a = await task_a

    assert result_a["usage"]["input_tokens"] == 300
    assert result_b["usage"]["input_tokens"] == 450
    assert call_count == 2
    assert gate.spent_today == 750


async def test_overlapping_calls_one_token_short_refuses_the_second(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """One token under what two reservations need: the second is refused, only one POST fires."""
    call_count = 0
    hold_first = asyncio.Event()
    first_started = asyncio.Event()

    async def _side_effect(method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        nonlocal call_count
        call_count += 1
        first_started.set()
        await hold_first.wait()
        return AiohttpClientMockResponse(method=method, url=url, status=200, json=_response(300))

    aioclient_mock.post(API_URL, side_effect=_side_effect)

    payload = _padded_payload(2_000)
    estimate = estimate_tokens(payload)
    gate = BudgetGate(hass, _client(hass), daily_budget=2 * estimate - 1)
    await gate.async_load()

    task_a = asyncio.create_task(gate.async_ask(payload))
    await first_started.wait()

    with pytest.raises(BudgetExceededError):
        await gate.async_ask(payload)

    hold_first.set()
    await task_a

    assert call_count == 1
    assert gate.spent_today == 300


async def test_failed_call_releases_its_reservation(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A 500 response leaves nothing reserved once the failure propagates."""
    aioclient_mock.post(API_URL, status=500, json={"error": "boom"})
    gate = BudgetGate(hass, _client(hass), daily_budget=1000)
    await gate.async_load()

    with pytest.raises(GutCheckApiError):
        await gate.async_ask(PAYLOAD)

    assert gate.spent_today == 0


async def test_cancelled_call_releases_its_reservation(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Cancelling an in-flight call leaves nothing reserved."""
    hold = asyncio.Event()
    started = asyncio.Event()

    async def _side_effect(method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        started.set()
        await hold.wait()
        return AiohttpClientMockResponse(method=method, url=url, status=200, json=_response(10))

    aioclient_mock.post(API_URL, side_effect=_side_effect)
    gate = BudgetGate(hass, _client(hass), daily_budget=1000)
    await gate.async_load()

    task = asyncio.create_task(gate.async_ask(PAYLOAD))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert gate.spent_today == 0


async def test_reservation_crossing_midnight_adds_actual_to_the_new_day(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: Any
) -> None:
    """A reservation made before midnight and settled after adds in full, never subtracted."""
    hold = asyncio.Event()
    started = asyncio.Event()

    async def _side_effect(method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        started.set()
        await hold.wait()
        return AiohttpClientMockResponse(method=method, url=url, status=200, json=_response(777))

    aioclient_mock.post(API_URL, side_effect=_side_effect)

    freezer.move_to("2026-01-01T23:59:59-08:00")
    gate = BudgetGate(hass, _client(hass), daily_budget=100_000)
    await gate.async_load()

    task = asyncio.create_task(gate.async_ask(PAYLOAD))
    await started.wait()

    freezer.move_to("2026-01-02T00:00:01-08:00")
    hold.set()
    await task

    assert gate.spent_today == 777


async def test_request_over_the_per_request_cap_is_refused_before_reserving(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An oversize request never reserves and never sends."""
    aioclient_mock.post(API_URL, status=200, json=_response(10))
    gate = BudgetGate(hass, _client(hass), daily_budget=1_000_000)
    await gate.async_load()

    huge_payload = _padded_payload(300_000)

    with pytest.raises(RequestTooLargeError):
        await gate.async_ask(huge_payload)

    assert gate.spent_today == 0
    assert len(posted_bodies(aioclient_mock)) == 0


async def test_state_plus_longest_question_over_cap_is_refused_before_reserving(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """State plus the longest question over its own cap is refused, even under the request cap."""
    aioclient_mock.post(API_URL, status=200, json=_response(10))
    gate = BudgetGate(hass, _client(hass), daily_budget=1_000_000)
    await gate.async_load()

    payload = _padded_payload(state_chars=90_000, question_chars=90_000)
    assert estimate_tokens(payload) < 64_000  # under the whole-request cap

    with pytest.raises(RequestTooLargeError):
        await gate.async_ask(payload)

    assert gate.spent_today == 0
    assert len(posted_bodies(aioclient_mock)) == 0


async def test_lowering_budget_below_spent_gives_remaining_zero(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A reload with a lower daily budget than today's spend never goes negative."""
    aioclient_mock.post(API_URL, status=200, json=_response(500))
    gate = BudgetGate(hass, _client(hass), daily_budget=1000)
    await gate.async_load()
    await gate.async_ask(PAYLOAD)
    assert gate.spent_today == 500

    reloaded = BudgetGate(hass, _client(hass), daily_budget=100)
    await reloaded.async_load()

    assert reloaded.spent_today == 500
    assert reloaded.remaining == 0


def _health_sensor_entity_id(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{RECIPE_HEALTH}")
    assert entity_id is not None
    return entity_id


async def test_budget_refused_run_makes_health_unavailable_and_retries_after_midnight(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry, freezer: Any
) -> None:
    """A refused run keeps the last payload, sends nothing, raises no issue, and retries by itself after midnight."""
    freezer.move_to("2026-01-01T12:00:00-08:00")
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("sensor", "test", "unique_selectable")
    hass.states.async_set(entry.entity_id, STATE_UNAVAILABLE)

    register_jev_responses(aioclient_mock, [_first_run_response(), _first_run_response()])
    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(posted_bodies(aioclient_mock)) == 1
    state = hass.states.get(_health_sensor_entity_id(hass, mock_config_entry))
    assert state is not None
    assert state.state != STATE_UNAVAILABLE

    coordinator = mock_config_entry.runtime_data.coordinators[RECIPE_HEALTH]
    first_payload = coordinator.data["last_payload"]
    assert first_payload is not None

    # Exhaust today's remaining budget so the next run is refused; there is no
    # public setter for this, so the test pokes the gate's internal counter
    # directly. This only maxes out *today's* count, the same shape a real
    # day of runs would leave behind, so tomorrow's reset still restores the
    # full daily_budget.
    coordinator.budget._data["spent"] = coordinator.budget.daily_budget

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert len(posted_bodies(aioclient_mock)) == 1
    state = hass.states.get(_health_sensor_entity_id(hass, mock_config_entry))
    assert state is not None
    assert state.state == STATE_UNAVAILABLE
    assert coordinator.data["last_payload"] == first_payload

    issue_registry = ir.async_get(hass)
    assert not any(domain == DOMAIN and issue_id.startswith("unavailable_") for domain, issue_id in issue_registry.issues)

    # Past local midnight, the budget resets and the coordinator's own
    # retry_after schedule (next local midnight plus a minute) fires by
    # itself, without anything re-triggering it from the test.
    freezer.move_to("2026-01-02T00:00:00-08:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    freezer.tick(65)
    for _ in range(5):
        await asyncio.sleep(0)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(posted_bodies(aioclient_mock)) == 2
    state = hass.states.get(_health_sensor_entity_id(hass, mock_config_entry))
    assert state is not None
    assert state.state != STATE_UNAVAILABLE


def _first_run_response() -> dict[str, Any]:
    return {
        "model": "jev-latest",
        "answers": {"e0": choice_answer(OPTION_EXPECTED, 0.9)},
        "usage": {"input_tokens": 10, "output_tokens": 0},
    }
