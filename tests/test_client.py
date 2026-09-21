"""Tests for the typed error taxonomy, Retry-After parsing and retry loop."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from freezegun import freeze_time
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.gutcheck.budget import BudgetGate
from custom_components.gutcheck.client import (
    GutCheckApiError,
    GutCheckAuthError,
    GutCheckClient,
    GutCheckConnectionError,
    GutCheckOverloadedError,
    GutCheckRateLimitError,
    GutCheckResponseError,
    GutCheckRetryableError,
    GutCheckValidationError,
    parse_retry_after,
    validate_response,
)
from custom_components.gutcheck.const import API_URL

from .conftest import posted_bodies

PAYLOAD: dict[str, Any] = {
    "state": {"check": "ping"},
    "model": "jev-latest",
    "questions": {"q": {"type": "noul", "instructions": "?"}},
}


def _response(input_tokens: int = 5) -> dict[str, Any]:
    """A Jev response reporting `input_tokens` spent."""
    return {
        "model": "jev-latest",
        "answers": {"q": {"type": "noul", "noul": 0.9}},
        "usage": {"input_tokens": input_tokens, "output_tokens": 0},
    }


def _queue_responses(aioclient_mock: AiohttpClientMocker, entries: list[dict[str, Any]]) -> None:
    """Queue a sequence of raw response kwargs for POSTs to API_URL, in order."""
    queue = list(entries)

    async def _side_effect(method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        """Pop and return the next queued raw response."""
        entry = queue.pop(0)
        return AiohttpClientMockResponse(method=method, url=url, **entry)

    aioclient_mock.post(API_URL, side_effect=_side_effect)


def _client(hass: HomeAssistant, api_key: str = "test-key") -> GutCheckClient:
    """A client bound to the test session, with the given key."""
    return GutCheckClient(async_get_clientsession(hass), api_key)


async def test_200_returns_parsed_response(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A 200 with a valid body returns the parsed response."""
    _queue_responses(aioclient_mock, [{"status": 200, "json": _response()}])

    result = await _client(hass).async_ask(PAYLOAD)  # type: ignore[arg-type]

    assert result["usage"]["input_tokens"] == 5


async def test_401_raises_auth_error(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A 401 raises GutCheckAuthError."""
    _queue_responses(aioclient_mock, [{"status": 401, "json": {"error": "unauthorized"}}])

    with pytest.raises(GutCheckAuthError) as excinfo:
        await _client(hass).async_ask(PAYLOAD)  # type: ignore[arg-type]
    assert excinfo.value.status == 401


async def test_422_raises_validation_error(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A 422 raises GutCheckValidationError."""
    _queue_responses(aioclient_mock, [{"status": 422, "json": {"error": "bad request"}}])

    with pytest.raises(GutCheckValidationError) as excinfo:
        await _client(hass).async_ask(PAYLOAD)  # type: ignore[arg-type]
    assert excinfo.value.status == 422


@pytest.mark.parametrize("status", [400, 500, 503])
async def test_other_bad_status_raises_base_error(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, status: int) -> None:
    """400, 500 and 503 raise the base class with the status set."""
    _queue_responses(aioclient_mock, [{"status": status, "json": {"error": "boom"}}])

    with pytest.raises(GutCheckApiError) as excinfo:
        await _client(hass).async_ask(PAYLOAD)  # type: ignore[arg-type]
    assert excinfo.value.status == status
    assert type(excinfo.value) is GutCheckApiError


async def test_client_error_raises_connection_error(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A transport-level aiohttp.ClientError raises GutCheckConnectionError."""
    aioclient_mock.post(API_URL, exc=aiohttp.ClientConnectionError())

    with pytest.raises(GutCheckConnectionError):
        await _client(hass).async_ask(PAYLOAD)  # type: ignore[arg-type]


async def test_timeout_raises_connection_error(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A TimeoutError raises GutCheckConnectionError."""
    aioclient_mock.post(API_URL, exc=TimeoutError())

    with pytest.raises(GutCheckConnectionError):
        await _client(hass).async_ask(PAYLOAD)  # type: ignore[arg-type]


async def test_html_body_raises_response_error(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A 200 text/html body raises GutCheckResponseError."""
    _queue_responses(aioclient_mock, [{"status": 200, "text": "<html>nope</html>"}])

    with pytest.raises(GutCheckResponseError):
        await _client(hass).async_ask(PAYLOAD)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "body",
    [
        "not a dict",
        {"answers": "not a dict", "usage": {"input_tokens": 1}},
        {"answers": {}, "usage": "not a dict"},
        {"answers": {}, "usage": {}},
        {"answers": {}, "usage": {"input_tokens": "5"}},
        {"answers": {}, "usage": {"input_tokens": True}},
        {"answers": {}, "usage": {"input_tokens": -1}},
    ],
)
def test_validate_response_rejects_malformed_shapes(body: object) -> None:
    """validate_response rejects a non-dict body, malformed answers/usage, and bad input_tokens."""
    with pytest.raises(GutCheckResponseError):
        validate_response(body)


def test_validate_response_accepts_a_valid_body() -> None:
    """validate_response returns the body unchanged when it matches the documented shape."""
    body = _response()
    assert validate_response(body) == body


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1.5", 1.5),
        ("0", 0.0),
        ("-3", 0.0),
        ("soon", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_retry_after_numeric_and_invalid_forms(value: str | None, expected: float | None) -> None:
    """A numeric string parses to seconds; anything non-numeric, empty, or absent parses to None."""
    assert parse_retry_after(value) == expected


def test_parse_retry_after_http_date_in_the_future() -> None:
    """An HTTP-date Retry-After in the future parses to the seconds remaining."""
    with freeze_time(datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)):
        assert parse_retry_after("Thu, 01 Jan 2026 12:00:30 GMT") == 30.0


def test_parse_retry_after_http_date_in_the_past() -> None:
    """An HTTP-date Retry-After already past parses to zero, never negative."""
    with freeze_time(datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)):
        assert parse_retry_after("Wed, 31 Dec 2025 12:00:00 GMT") == 0.0


def test_parse_retry_after_naive_http_date_is_treated_as_utc() -> None:
    """A Retry-After date with no timezone is treated as UTC, not local time."""
    with freeze_time(datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)):
        assert parse_retry_after("01 Jan 2026 12:00:30") == 30.0


async def test_retries_429_twice_then_succeeds_with_identical_bodies(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """429, 429, 200 succeeds after sleeping 1 then 2 seconds; the posted bodies are identical."""
    _queue_responses(
        aioclient_mock,
        [
            {"status": 429, "json": {"error": "slow down"}},
            {"status": 429, "json": {"error": "slow down"}},
            {"status": 200, "json": _response()},
        ],
    )

    with patch("custom_components.gutcheck.client.asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
        result = await _client(hass).async_ask(PAYLOAD)  # type: ignore[arg-type]

    assert result["usage"]["input_tokens"] == 5
    assert [call.args[0] for call in sleep_mock.await_args_list] == [1.0, 2.0]
    bodies = posted_bodies(aioclient_mock)
    assert bodies[0] == bodies[1] == bodies[2] == PAYLOAD


async def test_key_validation_gives_up_on_the_first_429(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Key validation runs behind an interactive form, so it must not sleep and retry."""
    _queue_responses(aioclient_mock, [{"status": 429, "json": {"error": "slow down"}} for _ in range(4)])

    with (
        patch("custom_components.gutcheck.client.asyncio.sleep", new_callable=AsyncMock) as sleep_mock,
        pytest.raises(GutCheckRateLimitError),
    ):
        await _client(hass).async_validate_key()

    assert sleep_mock.await_count == 0
    assert len(posted_bodies(aioclient_mock)) == 1


async def test_529_four_times_raises_after_three_retries(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """529 four times raises GutCheckOverloadedError after sleeps of 1, 2 and 4 seconds."""
    _queue_responses(aioclient_mock, [{"status": 529, "json": {"error": "overloaded"}} for _ in range(4)])

    with (
        patch("custom_components.gutcheck.client.asyncio.sleep", new_callable=AsyncMock) as sleep_mock,
        pytest.raises(GutCheckOverloadedError),
    ):
        await _client(hass).async_ask(PAYLOAD)  # type: ignore[arg-type]

    assert [call.args[0] for call in sleep_mock.await_args_list] == [1.0, 2.0, 4.0]


async def test_retry_after_60_is_honored(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Sitting exactly on MAX_RETRY_DELAY still waits and retries; the cap is inclusive."""
    _queue_responses(
        aioclient_mock,
        [
            {"status": 429, "json": {"error": "slow down"}, "headers": {"Retry-After": "60"}},
            {"status": 200, "json": _response()},
        ],
    )

    with patch("custom_components.gutcheck.client.asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
        await _client(hass).async_ask(PAYLOAD)  # type: ignore[arg-type]

    assert [call.args[0] for call in sleep_mock.await_args_list] == [60.0]


async def test_retry_after_over_60_raises_without_sleeping(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A Retry-After of more than 60 seconds raises at once without sleeping."""
    _queue_responses(
        aioclient_mock,
        [{"status": 429, "json": {"error": "slow down"}, "headers": {"Retry-After": "61"}}],
    )

    with (
        patch("custom_components.gutcheck.client.asyncio.sleep", new_callable=AsyncMock) as sleep_mock,
        pytest.raises(GutCheckRateLimitError),
    ):
        await _client(hass).async_ask(PAYLOAD)  # type: ignore[arg-type]

    sleep_mock.assert_not_awaited()


async def test_retry_after_http_date_form_sleeps_for_the_date_delta(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A Retry-After in HTTP-date form sleeps for the delta until that date."""
    _queue_responses(
        aioclient_mock,
        [
            {"status": 429, "json": {"error": "slow down"}, "headers": {"Retry-After": "Thu, 01 Jan 2026 12:00:15 GMT"}},
            {"status": 200, "json": _response()},
        ],
    )

    with (
        freeze_time(datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)),
        patch("custom_components.gutcheck.client.asyncio.sleep", new_callable=AsyncMock) as sleep_mock,
    ):
        await _client(hass).async_ask(PAYLOAD)  # type: ignore[arg-type]

    assert [call.args[0] for call in sleep_mock.await_args_list] == [15.0]


async def test_budget_gate_reserves_once_and_reconciles_to_final_usage(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Through BudgetGate, a 429 then 200 reserves once and ends with spent from the final usage."""
    _queue_responses(
        aioclient_mock,
        [
            {"status": 429, "json": {"error": "slow down"}},
            {"status": 200, "json": _response(input_tokens=42)},
        ],
    )
    gate = BudgetGate(hass, _client(hass), daily_budget=1000)

    with patch("custom_components.gutcheck.client.asyncio.sleep", new_callable=AsyncMock):
        await gate.async_ask(PAYLOAD)  # type: ignore[arg-type]

    assert gate.spent_today == 42


async def test_api_key_never_appears_in_errors_or_logs(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, caplog: Any
) -> None:
    """The API key never appears in a raised exception's str() or in caplog.text."""
    secret_key = "super-secret-key"
    _queue_responses(
        aioclient_mock,
        [
            {"status": 429, "json": {"error": "slow down"}},
            {"status": 401, "json": {"error": "unauthorized"}},
        ],
    )

    with (
        patch("custom_components.gutcheck.client.asyncio.sleep", new_callable=AsyncMock),
        pytest.raises(GutCheckAuthError) as excinfo,
    ):
        await _client(hass, api_key=secret_key).async_ask(PAYLOAD)  # type: ignore[arg-type]

    assert secret_key not in str(excinfo.value)
    assert secret_key not in caplog.text


async def test_retryable_error_carries_status_and_retry_after() -> None:
    """GutCheckRetryableError stores the status and parsed retry_after for its subclasses."""
    err = GutCheckRateLimitError("rate limited", 429, 12.5)
    assert isinstance(err, GutCheckRetryableError)
    assert err.status == 429
    assert err.retry_after == 12.5
