"""HTTP client for the Jev systemone API, on Home Assistant's shared session."""

from __future__ import annotations

import json
import logging

import aiohttp

from .const import API_URL, REQUEST_TIMEOUT, VALIDATION_REQUEST
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


def _validate_response_shape(body: object) -> SystemOneResponse:
    if (
        not isinstance(body, dict)
        or not isinstance(body.get("answers"), dict)
        or not isinstance(body.get("usage"), dict)
        or isinstance(body["usage"].get("input_tokens"), bool)
        or not isinstance(body["usage"].get("input_tokens"), int)
    ):
        raise GutCheckApiError("unexpected response shape")
    return body  # type: ignore[return-value]


class GutCheckClient:
    """Thin wrapper posting systemone requests through HA's aiohttp session."""

    def __init__(self, session: aiohttp.ClientSession, api_key: str) -> None:
        """Store the shared session and the caller's API key."""
        self._session = session
        self._api_key = api_key

    async def async_ask(self, payload: SystemOneRequest) -> SystemOneResponse:
        """POST one systemone request and return its parsed, shape-checked body."""
        headers = {"Authorization": f"Bearer {self._api_key}"}
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        try:
            resp = await self._session.post(API_URL, json=payload, headers=headers, timeout=timeout)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise GutCheckApiError("request failed") from err

        async with resp:
            text = await resp.text()
            if resp.status == 401:
                raise GutCheckAuthError("invalid or revoked api key", status=401)
            if resp.status != 200:
                raise GutCheckApiError("unexpected status", status=resp.status)
            try:
                body = json.loads(text)
            except ValueError as err:
                raise GutCheckApiError("non-json response body") from err

        return _validate_response_shape(body)

    async def async_validate_key(self) -> None:
        """Send one cheap noul question to check the key; ignore the answer."""
        await self.async_ask(VALIDATION_REQUEST)  # type: ignore[arg-type]
