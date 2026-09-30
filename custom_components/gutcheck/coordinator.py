"""The coordinator that runs one recipe on its schedule and stores its result."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .budget import BudgetExceededError, BudgetGate, RequestTooLargeError, RunOverDailyBudgetError
from .client import GutCheckApiError, GutCheckAuthError
from .const import DOMAIN, FAILED_RUN_RETRY, RECIPE_INTERVAL, STORE_VERSION
from .models import SystemOneRequest, SystemOneResponse
from .recipes.gate import carry_forward, classify
from .recipes.shapes import LastPayload, Recipe, RecipeResult, _parse_stored_result, recipe_store_key
from .split import merge, split_batch

if TYPE_CHECKING:
    from . import GutCheckConfigEntry

_LOGGER = logging.getLogger(__name__)


def _seconds_until_budget_retry() -> float:
    """Seconds until the next local midnight, plus a minute for the reset to land first."""
    tomorrow = dt_util.now().date() + timedelta(days=1)
    next_midnight = dt_util.start_of_local_day(tomorrow)
    return (next_midnight - dt_util.now()).total_seconds() + 60


def _request_key(payload: SystemOneRequest) -> str:
    """A stable key for a request's exact state and questions."""
    return json.dumps(payload, sort_keys=True)


class RecipeCoordinator(DataUpdateCoordinator[RecipeResult]):
    """Runs one recipe's select/describe/ask/act cycle on a fixed interval."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: GutCheckConfigEntry,
        budget: BudgetGate,
        recipe: Recipe,
    ) -> None:
        """Store the recipe and budget gate and configure the update schedule."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{recipe.recipe_id}",
            update_interval=RECIPE_INTERVAL,
        )
        self.budget = budget
        self.recipe = recipe
        self._store: Store[RecipeResult] = Store(hass, STORE_VERSION, recipe_store_key(recipe.recipe_id))
        # Only a saved run advances _force_done, so a failed forced run stays pending.
        self._force_requested = 0
        self._force_done = 0
        self.running = False
        self._previous_success = True
        # Responses already billed in a failed run, keyed by their exact request; memory only.
        self._answered: dict[str, SystemOneResponse] = {}
        entry.async_on_unload(self._answered.clear)

    @callback
    def force_full_rescore(self) -> None:
        """Mark the next run to ask about everything, ignoring what carried forward last time."""
        self._force_requested += 1

    async def async_restore_or_schedule(self) -> None:
        """Restore a fresh-enough stored result for free, or schedule the first run at startup."""
        parsed = _parse_stored_result(await self._store.async_load(), self.recipe.stored_item_keys)
        # A last_run in the future (clock skew, a restored backup) reads as overdue
        # rather than restoring and scheduling the catch-up run further out still.
        if parsed is not None and timedelta(0) <= dt_util.utcnow() - parsed[1] < RECIPE_INTERVAL:
            result, last_run = parsed
            # Restore first: it can drop findings the safety rules now exclude,
            # and the sensor should publish what survived, not the stored set.
            await self.recipe.restore(self.hass, result)
            self.async_set_updated_data(result)
            remaining = (last_run + RECIPE_INTERVAL - dt_util.utcnow()).total_seconds()
            self.config_entry.async_on_unload(async_call_later(self.hass, remaining, self._handle_scheduled_refresh))
        else:
            self.config_entry.async_on_unload(async_at_started(self.hass, self._handle_started_refresh))

    @callback
    def _handle_scheduled_refresh(self, _now: datetime) -> None:
        """Start the catch-up run, due 7 days after a restored last_run, as a task unload cancels."""
        self.config_entry.async_create_background_task(
            self.hass,
            self.async_refresh(),
            f"{self.config_entry.entry_id}_{self.recipe.recipe_id}_catch_up",
        )

    @callback
    def _handle_started_refresh(self, _hass: HomeAssistant) -> None:
        """Run the first-ever (or overdue) refresh once Home Assistant has started."""
        self.config_entry.async_create_background_task(
            self.hass,
            self.async_refresh(),
            f"{self.config_entry.entry_id}_{self.recipe.recipe_id}_first_refresh",
        )

    @callback
    def _async_refresh_finished(self) -> None:
        """Redraw a failure that follows another, which the base class skips, leaving last_error stale."""
        super()._async_refresh_finished()
        if not self.last_update_success and not self._previous_success:
            self.async_update_listeners()

    def _keep_answer(self, payload: SystemOneRequest, response: SystemOneResponse) -> None:
        """Keep a billed response until the run succeeds, so a retry of the same request is free."""
        self._answered[_request_key(payload)] = response

    async def _async_send(self, payloads: list[SystemOneRequest]) -> list[SystemOneResponse]:
        """Send the run's requests not already answered, mapping each failure to the coordinator's own.

        A request identical to one already billed in a failed run reuses that
        response; answers kept for any other request are dropped.
        """
        keys = [_request_key(payload) for payload in payloads]
        for stale in self._answered.keys() - set(keys):
            del self._answered[stale]
        to_send = [payload for payload, key in zip(payloads, keys, strict=True) if key not in self._answered]
        try:
            await self.budget.async_ask_all(to_send, self._keep_answer)
        except GutCheckAuthError as err:
            raise ConfigEntryAuthFailed("api key rejected") from err
        except BudgetExceededError as err:
            if self.last_update_success:
                _LOGGER.info("%s paused until the daily budget resets after midnight", self.name)
                # Marked failed first so the coordinator skips its own ERROR line for an expected state.
                self.last_update_success = False
            raise UpdateFailed("daily budget reached", retry_after=_seconds_until_budget_retry()) from err
        except RequestTooLargeError as err:
            raise UpdateFailed("run was too large to send") from err
        except RunOverDailyBudgetError as err:
            raise UpdateFailed("run needs more than the whole daily budget; raise the budget to let it run") from err
        except GutCheckApiError as err:
            raise UpdateFailed("recipe run failed", retry_after=FAILED_RUN_RETRY.total_seconds()) from err
        return [self._answered[key] for key in keys]

    async def _async_update_data(self) -> RecipeResult:
        """Run the recipe with running set for its whole length, whoever started it."""
        self.running = True
        # Read before the run: a budget refusal flips last_update_success mid-run.
        self._previous_success = self.last_update_success
        try:
            return await self._async_run()
        finally:
            self.running = False

    async def _async_run(self) -> RecipeResult:
        """Run one select/describe/ask/act cycle, or carry the prior result forward when nothing changed."""
        generation = self._force_requested
        previous = self.data
        batch = await self.recipe.async_prepare(self.hass, previous, force=generation > self._force_done)

        if not batch.subjects:
            last_payload = previous["last_payload"] if previous is not None else None
            result = carry_forward(batch, self.recipe.options, last_payload)
        else:
            payloads = split_batch(batch)
            responses = await self._async_send(payloads)
            # One request keeps the single-dict shape stored results already hold.
            sent: LastPayload = payloads[0] if len(payloads) == 1 else payloads
            # getattr: only the health recipe offers a lean; the Recipe Protocol stays untouched.
            result = classify(
                batch,
                merge(payloads, responses),
                self.recipe.options,
                sent,
                self.recipe.gate,
                lean=getattr(self.recipe, "lean", None),
            )

        await self.recipe.async_act(self.hass, result)
        await self._store.async_save(result)
        self._force_done = generation
        self._answered.clear()
        return result
