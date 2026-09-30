"""Diagnostic sensor suggestions recipe: one choice question per primary sensor with no device class."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from ..const import HIDE_DIAGNOSTIC_ISSUE_PREFIX, OPTION_SUGGESTED, RECIPE_HIDE_DIAGNOSTIC
from ..models import Question
from .gate import gate_choice
from .hide_diagnostic_cards import sync_hide_diagnostic_cards
from .hide_diagnostic_const import (
    HIDE_DIAGNOSTIC_CHOICES,
    HIDE_DIAGNOSTIC_CONFIDENCE_THRESHOLD,
    HIDE_DIAGNOSTIC_CRITERIA,
    HIDE_DIAGNOSTIC_INSTRUCTIONS,
    OPTION_DIAGNOSTIC,
    OPTION_PRIMARY,
)
from .hide_diagnostic_describe import decided_in_code, describe, qualifies, qualifying_entry
from .safety import SafetyRules
from .shapes import Batch, Item, RecipeResult

_LOGGER = logging.getLogger(__name__)


class HideDiagnosticRecipe:
    """Selects primary sensors with no device class and classifies each with one choice question."""

    recipe_id = RECIPE_HIDE_DIAGNOSTIC
    options: tuple[str, ...] = (OPTION_SUGGESTED, OPTION_PRIMARY)
    issue_prefix = HIDE_DIAGNOSTIC_ISSUE_PREFIX
    # An unsure item carries no choice, so the registry id is the one field
    # every stored item has.
    stored_item_keys: frozenset[str] = frozenset({"registry_id"})

    def __init__(self, critical_label: str | None) -> None:
        """Build the shared safety guard."""
        self._safety = SafetyRules(critical_label)

    def gate(self, answer: object) -> str | None:
        """Gate one answer through the choice gate over diagnostic and primary.

        A confident diagnostic lands in suggested and a confident primary in
        its own bucket. OPTION_NONE is never one of the two choices, so a
        confident "none of these" lands in unsure.
        """
        choice = gate_choice(answer, HIDE_DIAGNOSTIC_CHOICES, HIDE_DIAGNOSTIC_CONFIDENCE_THRESHOLD)
        if choice is None:
            return None
        return OPTION_SUGGESTED if choice == OPTION_DIAGNOSTIC else choice

    async def async_prepare(self, hass: HomeAssistant, previous: RecipeResult | None = None, *, force: bool = False) -> Batch:
        """Select every qualifying sensor and ask one choice question per asked sensor.

        previous and force are unused, since nothing carries forward between
        runs. A signal-strength sensor goes straight into suggested with no
        question; the index of every other one counts only asked sensors, so a
        code-decided one never shifts it.
        """
        registry = er.async_get(hass)
        selected = sorted(
            (entry for entry in registry.entities.values() if qualifies(hass, self._safety, entry)),
            key=lambda entry: entry.entity_id,
        )

        sensors: list[dict[str, Any]] = []
        questions: dict[str, Question] = {}
        subjects: dict[str, Item] = {}
        carried: dict[str, list[Item]] = {OPTION_SUGGESTED: []}
        for entry in selected:
            state_item, subject = describe(hass, entry)
            if decided_in_code(entry):
                carried[OPTION_SUGGESTED].append(subject)
                continue
            index = len(sensors)
            sensors.append(state_item)
            question_id = f"h{index}"
            questions[question_id] = {
                "type": "choice",
                "instructions": HIDE_DIAGNOSTIC_INSTRUCTIONS.format(index=index),
                "criteria": HIDE_DIAGNOSTIC_CRITERIA,
            }
            subjects[question_id] = subject

        _LOGGER.debug(
            "diagnostic sensor suggestions selected=%s asked=%s decided_in_code=%s",
            len(selected),
            len(sensors),
            len(carried[OPTION_SUGGESTED]),
        )
        return Batch(
            state={"sensors": sensors},
            questions=questions,
            subjects=subjects,
            carried=carried,
            list_key="sensors",
            template=HIDE_DIAGNOSTIC_INSTRUCTIONS,
        )

    async def async_act(self, hass: HomeAssistant, result: RecipeResult) -> None:
        """Sync one fixable Repairs card per suggested sensor, and log counts."""
        sync_hide_diagnostic_cards(hass, self._safety, result["items"].get(OPTION_SUGGESTED, []))
        _LOGGER.debug("diagnostic sensor suggestions run complete, counts=%s", result["counts"])

    def _still_qualifies(self, hass: HomeAssistant, item: Item) -> bool:
        """Whether a stored suggestion's sensor, looked up by registry_id, still exists and still qualifies."""
        return qualifying_entry(hass, self._safety, str(item["registry_id"])) is not None

    async def restore(self, hass: HomeAssistant, result: RecipeResult) -> None:
        """Filter every bucket and unsure for sensors that no longer qualify, then re-sync cards.

        A restore calls no API, so a sensor hidden, disabled, categorised or
        removed since the run must drop out of the stored result. Every
        persistent effect async_act has (the card sync) is reproduced here too.
        """
        for option, items in result["items"].items():
            kept = [item for item in items if self._still_qualifies(hass, item)]
            result["items"][option] = kept
            result["counts"][option] = len(kept)
        result["unsure"] = [item for item in result["unsure"] if self._still_qualifies(hass, item)]
        sync_hide_diagnostic_cards(hass, self._safety, result["items"].get(OPTION_SUGGESTED, []), restoring=True)
