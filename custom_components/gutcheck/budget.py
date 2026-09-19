"""Reserve-then-reconcile daily token budget gate."""

from __future__ import annotations

import asyncio
import json
import math

from .client import GutCheckClient
from .const import CHARS_PER_TOKEN
from .models import SystemOneRequest, SystemOneResponse


class BudgetExceededError(Exception):
    """Raised when a run would exceed what remains of the daily budget."""


def estimate_tokens(payload: SystemOneRequest) -> int:
    """Estimate a request's token cost from its serialized character count."""
    return math.ceil(len(json.dumps(payload)) / CHARS_PER_TOKEN)


class BudgetGate:
    """Wraps a client, refusing a run that would exceed the daily cap."""

    def __init__(self, client: GutCheckClient, daily_budget: int) -> None:
        """Store the wrapped client and the daily cap; start at zero spent."""
        self._client = client
        self._daily_budget = daily_budget
        self._spent_today = 0
        self._lock = asyncio.Lock()

    @property
    def spent_today(self) -> int:
        """Tokens spent so far in the current period."""
        return self._spent_today

    @property
    def daily_budget(self) -> int:
        """The configured daily cap."""
        return self._daily_budget

    async def async_ask(self, payload: SystemOneRequest) -> SystemOneResponse:
        """Reserve an estimate, call the client, then reconcile to actual usage."""
        estimate = estimate_tokens(payload)
        async with self._lock:
            if self._spent_today + estimate > self._daily_budget:
                raise BudgetExceededError("daily budget reached")
            self._spent_today += estimate

        try:
            response = await self._client.async_ask(payload)
        except BaseException:
            async with self._lock:
                self._spent_today -= estimate
            raise

        async with self._lock:
            self._spent_today += response["usage"]["input_tokens"] - estimate

        return response
