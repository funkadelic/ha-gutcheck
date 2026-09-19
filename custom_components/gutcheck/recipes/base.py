"""Shared select/describe/ask/act framework every recipe implements."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Protocol, TypedDict

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from ..budget import BudgetExceededError, BudgetGate, RequestTooLargeError
from ..client import GutCheckApiError, GutCheckAuthError
from ..const import DOMAIN, MODEL, RECIPE_INTERVAL
from ..models import ChoiceQuestion, SystemOneRequest
from .gate import classify

if TYPE_CHECKING:
    from .. import GutCheckConfigEntry

_LOGGER = logging.getLogger(__name__)

Item = dict[str, str | float | bool | None]


@dataclass
class Batch:
    """One recipe run's model-visible state, questions and subject index."""

    state: dict[str, Any]
    questions: dict[str, ChoiceQuestion]
    subjects: dict[str, Item]


class RecipeResult(TypedDict):
    """A completed run's classification, ready for the summary sensor."""

    last_run: str
    counts: dict[str, int]
    items: dict[str, list[Item]]
    unsure: list[Item]
    last_payload: SystemOneRequest | None


class Recipe(Protocol):
    """A recipe: select and describe its subjects, then act on the answers."""

    recipe_id: str
    options: tuple[str, ...]

    async def async_prepare(self, hass: HomeAssistant) -> Batch:
        """Select subjects and build the request state and questions."""
        ...

    async def async_act(self, hass: HomeAssistant, result: RecipeResult) -> None:
        """Act on a completed result (logging, Repairs, and so on)."""
        ...


def _seconds_until_budget_retry() -> float:
    """Seconds until the next local midnight, plus a minute for the reset to land first."""
    tomorrow = dt_util.now().date() + timedelta(days=1)
    next_midnight = dt_util.start_of_local_day(tomorrow)
    return (next_midnight - dt_util.now()).total_seconds() + 60


def _empty_result() -> RecipeResult:
    return {
        "last_run": "",
        "counts": {},
        "items": {},
        "unsure": [],
        "last_payload": None,
    }


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

    async def _async_update_data(self) -> RecipeResult:
        """Run one select/describe/ask/act cycle."""
        batch = await self.recipe.async_prepare(self.hass)

        if not batch.subjects:
            result = _empty_result()
        else:
            payload: SystemOneRequest = {
                "state": batch.state,
                "model": MODEL,
                "questions": batch.questions,  # type: ignore[typeddict-item]
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
                raise UpdateFailed("recipe run failed") from err
            result = classify(batch, response, self.recipe.options, payload)

        await self.recipe.async_act(self.hass, result)
        return result
