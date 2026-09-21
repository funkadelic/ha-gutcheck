"""HTTP client for the Jev systemone API, on Home Assistant's shared session."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import aiohttp

from .const import API_URL, BACKOFF_BASE, MAX_RETRIES, MAX_RETRY_DELAY, REQUEST_TIMEOUT, VALIDATION_REQUEST
from .models import SystemOneRequest, SystemOneResponse

_LOGGER = logging.getLogger(__name__)


class GutCheckApiError(Exception):
    """Base error for all Jev API failures."""

    def __init__(self, message: str, status: int | None = None) -> None:
        """Store the optional HTTP status alongside the message."""
        super().__init__(message)
        self.status = status


class GutCheckAuthError(GutCheckApiError):
    """401 - invalid or revoked API key."""


class GutCheckValidationError(GutCheckApiError):
    """422 - the request body failed the API's own validation."""


class GutCheckRetryableError(GutCheckApiError):
    """A status the caller should retry, carrying the server's Retry-After."""

    def __init__(self, message: str, status: int, retry_after: float | None) -> None:
        """Store the status and the parsed Retry-After delay, if any."""
        super().__init__(message, status)
        self.retry_after = retry_after


class GutCheckRateLimitError(GutCheckRetryableError):
    """429 - rate limited."""


class GutCheckOverloadedError(GutCheckRetryableError):
    """529 - the API is overloaded."""


class GutCheckResponseError(GutCheckApiError):
    """The response body was not JSON, or not the documented shape."""


class GutCheckConnectionError(GutCheckApiError):
    """A transport-level failure; no response was received at all."""


def parse_retry_after(value: str | None) -> float | None:
    """Parse a Retry-After header in either delay-seconds or HTTP-date form (RFC 9110)."""
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(value)
    except TypeError, ValueError, IndexError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return max(0.0, (parsed - datetime.now(UTC)).total_seconds())


def validate_response(data: object) -> SystemOneResponse:
    """Check that a response body has the documented shape."""
    if not isinstance(data, dict):
        raise GutCheckResponseError("response body is not an object")
    answers = data.get("answers")
    usage = data.get("usage")
    if not isinstance(answers, dict) or not isinstance(usage, dict):
        raise GutCheckResponseError("response is missing answers or usage")
    input_tokens = usage.get("input_tokens")
    if isinstance(input_tokens, bool) or not isinstance(input_tokens, int) or input_tokens < 0:
        raise GutCheckResponseError("usage.input_tokens is missing or invalid")
    return data  # type: ignore[return-value]


class GutCheckClient:
    """Thin wrapper posting systemone requests through HA's aiohttp session, with retries."""

    def __init__(self, session: aiohttp.ClientSession, api_key: str) -> None:
        """Store the shared session and the caller's API key."""
        self._session = session
        self._api_key = api_key

    async def _post(self, payload: SystemOneRequest) -> SystemOneResponse:
        """One POST to the API, with every failure mapped onto the error taxonomy."""
        headers = {"Authorization": f"Bearer {self._api_key}"}
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        try:
            resp = await self._session.post(API_URL, json=payload, headers=headers, timeout=timeout)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise GutCheckConnectionError("request failed") from err

        async with resp:
            status = resp.status
            if status == 200:
                text = await resp.text()
                try:
                    body = json.loads(text)
                except ValueError as err:
                    raise GutCheckResponseError("non-json response body") from err
                return validate_response(body)

            # Status and redirect count only. WWW-Authenticate is server-controlled
            # free text and the key length is an oracle, and these lines get pasted
            # into public issues.
            _LOGGER.debug("api error status=%s redirects=%s", status, len(resp.history))
            if status in (429, 529):
                retry_after = parse_retry_after(resp.headers.get("Retry-After"))
                if status == 429:
                    raise GutCheckRateLimitError("rate limited", status, retry_after)
                raise GutCheckOverloadedError("overloaded", status, retry_after)

            if status == 401:
                raise GutCheckAuthError("invalid or revoked api key", status)
            if status == 422:
                raise GutCheckValidationError("request rejected as invalid", status)
            raise GutCheckApiError("unexpected status", status)

    async def async_ask(self, payload: SystemOneRequest, *, max_retries: int = MAX_RETRIES) -> SystemOneResponse:
        """POST one systemone request, retrying 429/529 per Retry-After or backoff."""
        for attempt in range(max_retries + 1):
            try:
                return await self._post(payload)
            except GutCheckRetryableError as err:
                if attempt == max_retries:
                    raise
                delay = err.retry_after if err.retry_after is not None else BACKOFF_BASE * 2**attempt
                if delay > MAX_RETRY_DELAY:
                    raise
                _LOGGER.debug("retrying status=%s attempt=%s delay=%s", err.status, attempt, delay)
                await asyncio.sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover

    async def async_validate_key(self) -> None:
        """Send one cheap noul question to check the key; ignore the answer.

        No retries: this runs behind an interactive form, where backing off for
        up to a Retry-After per attempt would freeze the dialog with no feedback.
        """
        await self.async_ask(VALIDATION_REQUEST, max_retries=0)  # type: ignore[arg-type]
