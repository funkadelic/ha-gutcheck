"""What a recipe and its coordinator trade in, and how it is persisted."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, TypedDict

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from ..const import DOMAIN
from ..models import Question, SystemOneRequest

Item = dict[str, str | float | bool | None]


@dataclass
class Batch:
    """One recipe run's model-visible state, questions and subject index."""

    state: dict[str, Any]
    questions: dict[str, Question]
    subjects: dict[str, Item]
    # Classifications a recipe decided without asking, keyed by option. Never
    # includes unsure: an unsure verdict is never carried forward.
    carried: dict[str, list[Item]] = field(default_factory=dict)


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
    stored_item_keys: frozenset[str]

    def gate(self, answer: object) -> str | None:
        """Gate one answer through this recipe's own confidence threshold and answer type."""
        ...

    async def async_prepare(self, hass: HomeAssistant, previous: RecipeResult | None = None, *, force: bool = False) -> Batch:
        """Select subjects and build the request state and questions.

        previous is the coordinator's last completed result, or None before
        any run has completed. force asks again about everything this run
        can ask about. A recipe with no carry-forward path ignores both.
        """
        ...

    async def async_act(self, hass: HomeAssistant, result: RecipeResult) -> None:
        """Act on a completed result (logging, Repairs, and so on)."""
        ...

    async def restore(self, hass: HomeAssistant, result: RecipeResult) -> None:
        """Re-arm a restored result's side effects, without calling the API."""
        ...


def recipe_store_key(recipe_id: str) -> str:
    """The Store key holding one recipe's last run and result."""
    return f"{DOMAIN}.recipe_{recipe_id}"


_RECIPE_RESULT_KEYS = frozenset({"last_run", "counts", "items", "unsure", "last_payload"})


def _valid_items(bucket: object, item_keys: frozenset[str]) -> bool:
    """Whether one option's stored items all carry the fields its recipe's restore reads."""
    return isinstance(bucket, list) and all(isinstance(item, dict) and item.keys() >= item_keys for item in bucket)


def _parse_stored_result(stored: object, item_keys: frozenset[str]) -> tuple[RecipeResult, datetime] | None:
    """Return the stored value and its parsed last_run only when the whole shape is valid.

    item_keys is the requesting recipe's own stored_item_keys: every field its
    restore path reads. A store built for a different recipe's item shape is
    rejected rather than half-parsed.
    """
    if not isinstance(stored, dict) or not stored.keys() >= _RECIPE_RESULT_KEYS:
        return None
    last_run = stored.get("last_run")
    if not isinstance(last_run, str):
        return None
    parsed = dt_util.parse_datetime(last_run)
    if parsed is None:
        return None
    # A half-written store would otherwise only blow up later, during restore.
    counts, items = stored.get("counts"), stored.get("items")
    if not isinstance(counts, dict) or not isinstance(items, dict):
        return None
    if not all(isinstance(count, int) for count in counts.values()):
        return None
    # unsure holds the same item shape as any bucket, and a recipe's restore
    # reads the same fields off it, so it gets the same check.
    if not _valid_items(stored.get("unsure"), item_keys):
        return None
    if not all(_valid_items(bucket, item_keys) for bucket in items.values()):
        return None
    return stored, parsed  # type: ignore[return-value]
