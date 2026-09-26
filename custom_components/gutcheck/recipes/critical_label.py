"""Critical label suggestions recipe: one choice question per valve, switch or siren."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from ..const import CRITICAL_LABEL_ISSUE_PREFIX, OPTION_SUGGESTED, RECIPE_CRITICAL_LABEL
from ..models import Question
from .critical_label_cards import sync_critical_label_cards
from .critical_label_const import (
    CRITICAL_LABEL_CHOICES,
    CRITICAL_LABEL_CONFIDENCE_THRESHOLD,
    CRITICAL_LABEL_CRITERIA,
    CRITICAL_LABEL_INSTRUCTIONS,
    OPTION_CRITICAL,
    OPTION_NOT_CRITICAL,
)
from .critical_label_describe import describe, qualifies, qualifying_entry
from .gate import gate_choice
from .safety import SafetyRules
from .shapes import Batch, Item, RecipeResult

_LOGGER = logging.getLogger(__name__)


class CriticalLabelRecipe:
    """Selects valves, switches and sirens and classifies each with one choice question."""

    recipe_id = RECIPE_CRITICAL_LABEL
    options: tuple[str, ...] = (OPTION_SUGGESTED, OPTION_NOT_CRITICAL)
    issue_prefix = CRITICAL_LABEL_ISSUE_PREFIX
    # An unsure item carries no choice, so the registry id is the one field
    # every stored item has.
    stored_item_keys: frozenset[str] = frozenset({"registry_id"})

    def __init__(self, critical_label: str | None) -> None:
        """Build the shared safety guard."""
        self._safety = SafetyRules(critical_label)

    def gate(self, answer: object) -> str | None:
        """Gate one answer through the choice gate over critical and not_critical.

        A confident critical lands in suggested; a confident not_critical
        lands in its own bucket rather than unsure. OPTION_NONE is never one
        of the two choices, so a confident "none of these" lands in unsure.
        """
        choice = gate_choice(answer, CRITICAL_LABEL_CHOICES, CRITICAL_LABEL_CONFIDENCE_THRESHOLD)
        if choice is None:
            return None
        return OPTION_SUGGESTED if choice == OPTION_CRITICAL else choice

    async def async_prepare(self, hass: HomeAssistant, previous: RecipeResult | None = None, *, force: bool = False) -> Batch:
        """Select every qualifying valve, switch or siren and ask one choice question per entity.

        previous and force are unused, since nothing carries forward between
        runs.
        """
        registry = er.async_get(hass)
        selected = sorted(
            (entry for entry in registry.entities.values() if qualifies(hass, self._safety, entry)),
            key=lambda entry: entry.entity_id,
        )

        entities: list[dict[str, Any]] = []
        questions: dict[str, Question] = {}
        subjects: dict[str, Item] = {}
        for entry in selected:
            index = len(entities)
            state_item, subject = describe(hass, entry)
            entities.append(state_item)
            question_id = f"k{index}"
            questions[question_id] = {
                "type": "choice",
                "instructions": CRITICAL_LABEL_INSTRUCTIONS.format(index=index),
                "criteria": CRITICAL_LABEL_CRITERIA,
            }
            subjects[question_id] = subject

        _LOGGER.debug("critical label suggestions selected=%s asked=%s", len(selected), len(entities))
        return Batch(
            state={"entities": entities},
            questions=questions,
            subjects=subjects,
            list_key="entities",
            template=CRITICAL_LABEL_INSTRUCTIONS,
        )

    async def async_act(self, hass: HomeAssistant, result: RecipeResult) -> None:
        """Sync one fixable Repairs card per suggested entity, and log counts."""
        sync_critical_label_cards(hass, self._safety, result["items"].get(OPTION_SUGGESTED, []))
        _LOGGER.debug("critical label suggestions run complete, counts=%s", result["counts"])

    def _still_qualifies(self, hass: HomeAssistant, item: Item) -> bool:
        """Whether a stored suggestion's entity, looked up by registry_id, still exists and still qualifies."""
        return qualifying_entry(hass, self._safety, str(item["registry_id"])) is not None

    async def restore(self, hass: HomeAssistant, result: RecipeResult) -> None:
        """Filter every bucket and unsure for entities that no longer qualify, then re-sync cards.

        A restore calls no API, so an entity given the label by hand,
        disabled, removed, or moved to a blocked domain since the run must
        still drop out of the stored result, rather than sitting exposed for
        up to a week until the next paid run notices. Every persistent
        effect async_act has (the card sync) is reproduced here too.
        """
        for option, items in result["items"].items():
            kept = [item for item in items if self._still_qualifies(hass, item)]
            result["items"][option] = kept
            result["counts"][option] = len(kept)
        result["unsure"] = [item for item in result["unsure"] if self._still_qualifies(hass, item)]
        sync_critical_label_cards(hass, self._safety, result["items"].get(OPTION_SUGGESTED, []), restoring=True)
