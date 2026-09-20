"""Reserve-then-reconcile daily token budget gate, persisted across restarts."""

from __future__ import annotations

import asyncio
import json
import logging
import math
from datetime import datetime
from typing import Any, TypedDict

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .client import GutCheckClient
from .const import BUDGET_STORE_KEY, CHARS_PER_TOKEN, REQUEST_TOKEN_LIMIT, SIGNAL_BUDGET_UPDATED, STATE_TOKEN_LIMIT, STORE_VERSION
from .models import SystemOneRequest, SystemOneResponse

_LOGGER = logging.getLogger(__name__)


class BudgetExceededError(Exception):
    """Raised when a run would exceed what remains of the daily budget."""


class RequestTooLargeError(Exception):
    """Raised when a single request exceeds the per-request or per-state token cap."""


class _BudgetData(TypedDict):
    """Persisted shape: the local date the counter applies to, and tokens spent."""

    date: str
    spent: int


def _estimate(value: Any) -> int:
    """Estimate a JSON-serializable value's token cost from its character count."""
    return math.ceil(len(json.dumps(value)) / CHARS_PER_TOKEN)


def estimate_tokens(payload: SystemOneRequest) -> int:
    """Estimate a whole request's token cost from its serialized character count."""
    return _estimate(payload)


def _today() -> str:
    """Today's date in the user's timezone, which is where the budget rolls over."""
    return dt_util.now().date().isoformat()


class BudgetGate:
    """Wraps a client, refusing a run that would exceed the persisted daily cap."""

    def __init__(self, hass: HomeAssistant, client: GutCheckClient, daily_budget: int) -> None:
        """Store the wrapped client and the daily cap; persistence starts at async_load."""
        self._hass = hass
        self._client = client
        self._daily_budget = daily_budget
        self._store: Store[_BudgetData] = Store(hass, STORE_VERSION, BUDGET_STORE_KEY)
        self._data: _BudgetData = {"date": _today(), "spent": 0}
        self._lock = asyncio.Lock()

    async def async_load(self) -> None:
        """Load the persisted counter; anything malformed or absent starts at zero."""
        stored = await self._store.async_load()
        if isinstance(stored, dict):
            date = stored.get("date")
            spent = stored.get("spent")
            if isinstance(date, str) and isinstance(spent, int) and not isinstance(spent, bool) and spent >= 0:
                self._data = {"date": date, "spent": spent}
        self._roll()

    def _roll(self) -> None:
        """Reset spent to 0 when the stored date is not today. Run under the lock."""
        today = _today()
        if self._data["date"] != today:
            self._data = {"date": today, "spent": 0}

    async def _save_and_notify(self) -> None:
        """Persist the counter and tell the usage sensors to rewrite their state."""
        await self._store.async_save(self._data)
        async_dispatcher_send(self._hass, SIGNAL_BUDGET_UPDATED)

    @property
    def spent_today(self) -> int:
        """Tokens spent so far today; reads 0 once the stored day has rolled over."""
        return self._data["spent"] if self._data["date"] == _today() else 0

    @property
    def daily_budget(self) -> int:
        """The configured daily cap."""
        return self._daily_budget

    @property
    def remaining(self) -> int:
        """Tokens left today, never negative."""
        return max(0, self._daily_budget - self.spent_today)

    @callback
    def async_start(self) -> CALLBACK_TYPE:
        """Roll and notify at local midnight; return the async_track_time_change unsub."""

        async def _handle_midnight(_now: datetime) -> None:
            """Roll the counter onto the new day and publish the reset."""
            async with self._lock:
                self._roll()
                await self._save_and_notify()

        return async_track_time_change(self._hass, _handle_midnight, hour=0, minute=0, second=0)

    def _release(self, reservation_date: str, estimate: int) -> None:
        """Undo a reservation on failure or cancellation."""
        self._roll()
        if self._data["date"] == reservation_date:
            self._data["spent"] = max(0, self._data["spent"] - estimate)
        # else: the day already rolled past the reservation's day, whose
        # counter was already reset to zero; there is nothing to subtract.

    def _reconcile(self, reservation_date: str, estimate: int, actual: int) -> None:
        """Replace a reservation's estimate with the actual usage the API reported."""
        self._roll()
        if self._data["date"] == reservation_date:
            self._data["spent"] += actual - estimate
        else:
            # The day rolled while the request was in flight: today's counter
            # never included the estimate, so add the actual usage in full.
            self._data["spent"] += actual

    async def async_ask(self, payload: SystemOneRequest) -> SystemOneResponse:
        """Reserve an estimate, call the client, then reconcile to actual usage."""
        estimate = estimate_tokens(payload)
        state_estimate = _estimate(payload["state"])
        longest_question = max((_estimate(question) for question in payload["questions"].values()), default=0)
        if estimate > REQUEST_TOKEN_LIMIT or state_estimate + longest_question > STATE_TOKEN_LIMIT:
            _LOGGER.debug(
                "request too large estimate=%s state_plus_longest_question=%s questions=%s",
                estimate,
                state_estimate + longest_question,
                len(payload["questions"]),
            )
            raise RequestTooLargeError("request exceeds the per-request token cap")

        async with self._lock:
            self._roll()
            if self.spent_today + estimate > self._daily_budget:
                raise BudgetExceededError("daily budget reached")
            reservation_date = self._data["date"]
            self._data["spent"] += estimate
            await self._save_and_notify()

        try:
            response = await self._client.async_ask(payload)
        except BaseException:
            async with self._lock:
                self._release(reservation_date, estimate)
                await self._save_and_notify()
            raise

        async with self._lock:
            self._reconcile(reservation_date, estimate, response["usage"]["input_tokens"])
            await self._save_and_notify()

        return response
