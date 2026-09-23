"""Shared select/describe/ask/act framework every recipe implements."""

from __future__ import annotations

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

from ..budget import BudgetExceededError, BudgetGate, RequestTooLargeError
from ..client import GutCheckApiError, GutCheckAuthError
from ..const import DOMAIN, FAILED_RUN_RETRY, MODEL, RECIPE_INTERVAL, STORE_VERSION
from ..models import SystemOneRequest
from .gate import carry_forward, classify
from .shapes import Recipe, RecipeResult, _parse_stored_result, recipe_store_key

if TYPE_CHECKING:
    from .. import GutCheckConfigEntry

_LOGGER = logging.getLogger(__name__)


def _seconds_until_budget_retry() -> float:
    """Seconds until the next local midnight, plus a minute for the reset to land first."""
    tomorrow = dt_util.now().date() + timedelta(days=1)
    next_midnight = dt_util.start_of_local_day(tomorrow)
    return (next_midnight - dt_util.now()).total_seconds() + 60


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
        # Only a saved run advances _force_done, so a failed forced run stays
        # pending and a press during a run forces the next run too.
        self._force_requested = 0
        self._force_done = 0

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

    async def _handle_scheduled_refresh(self, _now: datetime) -> None:
        """Run the catch-up refresh scheduled 7 days after a restored last_run."""
        await self.async_request_refresh()

    @callback
    def _handle_started_refresh(self, _hass: HomeAssistant) -> None:
        """Run the first-ever (or overdue) refresh once Home Assistant has started."""
        self.config_entry.async_create_background_task(
            self.hass,
            self.async_refresh(),
            f"{self.config_entry.entry_id}_{self.recipe.recipe_id}_first_refresh",
        )

    async def _async_update_data(self) -> RecipeResult:
        """Run one select/describe/ask/act cycle, or carry the prior result forward when nothing changed."""
        generation = self._force_requested
        previous = self.data
        batch = await self.recipe.async_prepare(self.hass, previous, force=generation > self._force_done)

        if not batch.subjects:
            last_payload = previous["last_payload"] if previous is not None else None
            result = carry_forward(batch, self.recipe.options, last_payload)
        else:
            payload: SystemOneRequest = {
                "state": batch.state,
                "model": MODEL,
                "questions": batch.questions,
            }
            try:
                response = await self.budget.async_ask(payload)
            except GutCheckAuthError as err:
                raise ConfigEntryAuthFailed("api key rejected") from err
            except BudgetExceededError as err:
                raise UpdateFailed("daily budget reached", retry_after=_seconds_until_budget_retry()) from err
            except RequestTooLargeError as err:
                raise UpdateFailed("run was too large to send") from err
            except GutCheckApiError as err:
                raise UpdateFailed("recipe run failed", retry_after=FAILED_RUN_RETRY.total_seconds()) from err
            result = classify(batch, response, self.recipe.options, payload, self.recipe.gate)

        await self.recipe.async_act(self.hass, result)
        await self._store.async_save(result)
        self._force_done = generation
        return result
