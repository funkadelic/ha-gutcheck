"""Client details the shared tests leave open: messages, zero usage, the timeout and the key-check body."""

from __future__ import annotations

from typing import Any

import aiohttp
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.client import (
    GutCheckApiError,
    GutCheckClient,
    GutCheckResponseError,
    GutCheckRetryableError,
    validate_response,
)
from custom_components.gutcheck.const import REQUEST_TIMEOUT, VALIDATION_REQUEST

from .conftest import api_response, posted_bodies, register_jev_responses


def test_errors_carry_their_message() -> None:
    """str() of an API error and of a retryable error is the message it was raised with."""
    assert str(GutCheckApiError("boom", 500)) == "boom"
    assert str(GutCheckRetryableError("slow", 429, 2.0)) == "slow"


def test_a_response_billing_zero_input_tokens_is_accepted() -> None:
    """Zero is a valid usage figure; only a negative one is rejected."""
    body = api_response({}, 0)
    assert validate_response(body) is body
    negative = api_response({}, -1)
    with pytest.raises(GutCheckResponseError):
        validate_response(negative)


async def test_every_request_carries_the_configured_timeout(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """The POST passes a ClientTimeout with REQUEST_TIMEOUT as its total."""
    register_jev_responses(aioclient_mock, [api_response({})])
    session = async_get_clientsession(hass)
    seen: list[Any] = []
    forward = session._request

    async def _record(*args: Any, **kwargs: Any) -> Any:
        """Note the timeout kwarg, then hand the request to the mocker."""
        seen.append(kwargs.get("timeout"))
        return await forward(*args, **kwargs)

    object.__setattr__(session, "_request", _record)
    await GutCheckClient(session, "test-key").async_ask({"state": "s", "model": "jev-latest", "questions": {}})

    assert len(seen) == 1
    assert isinstance(seen[0], aiohttp.ClientTimeout)
    assert seen[0].total == REQUEST_TIMEOUT


async def test_key_validation_posts_the_fixed_check_question(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """The key check sends VALIDATION_REQUEST as its body."""
    register_jev_responses(aioclient_mock, [api_response({})])

    await GutCheckClient(async_get_clientsession(hass), "test-key").async_validate_key()

    assert posted_bodies(aioclient_mock) == [VALIDATION_REQUEST]
