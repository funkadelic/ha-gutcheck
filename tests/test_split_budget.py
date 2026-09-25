"""Budget gate tests for a run sent as several requests: all-or-nothing, failure, cancel, rollover."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.gutcheck.budget import BudgetExceededError, BudgetGate, RunOverDailyBudgetError, estimate_tokens
from custom_components.gutcheck.client import GutCheckApiError, GutCheckClient
from custom_components.gutcheck.const import API_URL
from custom_components.gutcheck.models import SystemOneRequest

from .conftest import api_response, posted_bodies

PAYLOADS: list[SystemOneRequest] = [
    {"state": {"slice": index}, "model": "jev-latest", "questions": {f"q{index}": {"type": "noul", "instructions": "?"}}}
    for index in range(3)
]
E1, E2, E3 = (estimate_tokens(payload) for payload in PAYLOADS)
FAILED = (500, {"error": "boom"})


def _client(hass: HomeAssistant) -> GutCheckClient:
    """A client bound to the test session, with a throwaway key."""
    return GutCheckClient(async_get_clientsession(hass), "test-key")


def _serve(
    aioclient_mock: AiohttpClientMocker, replies: list[Any], hold_at: int | None = None
) -> tuple[asyncio.Event, asyncio.Event]:
    """Answer POSTs in order, each reply a token count or a (status, body) pair.

    The call numbered hold_at (from 0) sets the returned started event, then
    waits on the returned hold event before replying.
    """
    started, hold = asyncio.Event(), asyncio.Event()
    calls = 0

    async def _side_effect(method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        """Reply with the next queued answer, holding the chosen call open first."""
        nonlocal calls
        reply = replies[calls]
        if calls == hold_at:
            started.set()
            await hold.wait()
        calls += 1
        status, body = reply if isinstance(reply, tuple) else (200, api_response({}, reply))
        return AiohttpClientMockResponse(method=method, url=url, status=status, json=body)

    aioclient_mock.post(API_URL, side_effect=_side_effect)
    return started, hold


async def _gate(hass: HomeAssistant, daily_budget: int = 100_000) -> BudgetGate:
    """A loaded budget gate with the given daily cap."""
    gate = BudgetGate(hass, _client(hass), daily_budget=daily_budget)
    await gate.async_load()
    return gate


async def test_run_one_token_over_what_is_left_is_refused_whole(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A run whose summed estimate is one token over what is left sends nothing, even though its first request fits."""
    _serve(aioclient_mock, [10, 20, 30])
    gate = await _gate(hass, E1 + E2 + E3 + 1)
    gate._data["spent"] = 2

    with pytest.raises(BudgetExceededError):
        await gate.async_ask_all(PAYLOADS)

    assert posted_bodies(aioclient_mock) == []
    assert gate.spent_today == 2


async def test_run_over_the_whole_budget_is_refused_as_such(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A run bigger than the whole daily cap gets its own error, sends nothing and spends nothing."""
    _serve(aioclient_mock, [10, 20, 30])
    gate = await _gate(hass, E1 + E2 + E3 - 1)

    with pytest.raises(RunOverDailyBudgetError):
        await gate.async_ask_all(PAYLOADS)

    assert posted_bodies(aioclient_mock) == []
    assert gate.spent_today == 0


async def test_cancel_during_the_reservation_save_releases_it(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cancel while the reservation is being saved leaves nothing reserved and sends nothing."""
    _serve(aioclient_mock, [10, 20, 30])
    gate = await _gate(hass)
    save = gate._store.async_save
    calls = 0

    async def _cancel_first(data: Any) -> None:
        """Cancel the first save, the reservation's; let the release's save through."""
        nonlocal calls
        calls += 1
        if calls == 1:
            raise asyncio.CancelledError
        await save(data)

    monkeypatch.setattr(gate._store, "async_save", _cancel_first)

    with pytest.raises(asyncio.CancelledError):
        await gate.async_ask_all(PAYLOADS)

    assert posted_bodies(aioclient_mock) == []
    assert gate.spent_today == 0


async def test_second_request_failing_keeps_the_first_actual_and_releases_the_rest(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Request 2 of 3 fails: request 1's actual stays spent, the unsent reservations go back."""
    _serve(aioclient_mock, [10, FAILED, 30])
    gate = await _gate(hass)

    with pytest.raises(GutCheckApiError):
        await gate.async_ask_all(PAYLOADS)

    assert len(posted_bodies(aioclient_mock)) == 2
    assert gate.spent_today == 10


async def test_cancel_mid_run_keeps_only_what_was_billed(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """While request 2 is in flight, requests 2 and 3 stay reserved; a cancel then releases both."""
    started, _hold = _serve(aioclient_mock, [10, 20, 30], hold_at=1)
    gate = await _gate(hass)

    task = asyncio.create_task(gate.async_ask_all(PAYLOADS))
    await started.wait()
    assert gate.spent_today == 10 + E2 + E3

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert gate.spent_today == 10


async def test_run_crossing_midnight_adds_later_actuals_to_the_new_day(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: Any
) -> None:
    """Requests settled after midnight add their actuals in full to the new day's counter."""
    started, hold = _serve(aioclient_mock, [100, 200, 300], hold_at=1)
    freezer.move_to("2026-01-01T23:59:59-08:00")
    gate = await _gate(hass)

    task = asyncio.create_task(gate.async_ask_all(PAYLOADS))
    await started.wait()
    assert gate.spent_today == 100 + E2 + E3

    freezer.move_to("2026-01-02T00:00:01-08:00")
    hold.set()
    await task
    assert gate.spent_today == 500


async def test_run_failing_after_midnight_leaves_the_new_day_untouched(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: Any
) -> None:
    """A request held across midnight that then fails releases nothing into the new day."""
    started, hold = _serve(aioclient_mock, [100, FAILED, 300], hold_at=1)
    freezer.move_to("2026-01-01T23:59:59-08:00")
    gate = await _gate(hass)

    task = asyncio.create_task(gate.async_ask_all(PAYLOADS))
    await started.wait()
    assert gate.spent_today == 100 + E2 + E3

    freezer.move_to("2026-01-02T00:00:01-08:00")
    hold.set()
    with pytest.raises(GutCheckApiError):
        await task
    assert gate.spent_today == 0


async def test_one_payload_run_matches_a_single_ask(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A one-request run returns the same response and spends the same as async_ask."""
    _serve(aioclient_mock, [10, 10])
    gate = await _gate(hass)

    responses = await gate.async_ask_all(PAYLOADS[:1])
    assert gate.spent_today == 10
    response = await gate.async_ask(PAYLOADS[0])

    assert responses == [response]
    assert gate.spent_today == 20
