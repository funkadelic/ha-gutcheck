"""Device class suggestions recipe: one choice question per sensor with a unit and no device class."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from ..const import (
    DEVICE_CLASS_CONFIDENCE_THRESHOLD,
    DEVICE_CLASS_INSTRUCTIONS,
    DEVICE_CLASS_ISSUE_PREFIX,
    OPTION_SUGGESTED,
    RECIPE_DEVICE_CLASS,
)
from ..models import Question
from .device_class_cards import sync_device_class_cards
from .device_class_describe import KNOWN_CLASSES, candidate_classes, class_names, criteria, describe, qualifies, qualifying_entry
from .gate import gate_choice
from .safety import SafetyRules
from .shapes import Batch, Item, RecipeResult

_LOGGER = logging.getLogger(__name__)


class DeviceClassRecipe:
    """Selects sensors with a unit and no device class and classifies each with one choice question."""

    recipe_id = RECIPE_DEVICE_CLASS
    options: tuple[str, ...] = (OPTION_SUGGESTED,)
    issue_prefix = DEVICE_CLASS_ISSUE_PREFIX
    # An unsure item carries no choice, so the registry id is the one field
    # every stored item has.
    stored_item_keys: frozenset[str] = frozenset({"registry_id"})

    def __init__(self, critical_label: str | None) -> None:
        """Build the shared safety guard."""
        self._safety = SafetyRules(critical_label)

    def gate(self, answer: object) -> str | None:
        """Gate one answer through the choice gate over every known device class.

        OPTION_NONE is never a device class, so a confident "none of these"
        lands in unsure rather than being suggested.
        """
        return OPTION_SUGGESTED if gate_choice(answer, KNOWN_CLASSES, DEVICE_CLASS_CONFIDENCE_THRESHOLD) is not None else None

    async def async_prepare(self, hass: HomeAssistant, previous: RecipeResult | None = None, *, force: bool = False) -> Batch:
        """Select every qualifying sensor and ask one choice question per sensor with two or more candidates.

        previous and force are unused, since nothing carries forward between
        runs.
        """
        names = await class_names(hass)
        registry = er.async_get(hass)
        selected = sorted(
            (entry for entry in registry.entities.values() if qualifies(hass, self._safety, entry)),
            key=lambda entry: entry.entity_id,
        )

        sensors: list[dict[str, Any]] = []
        questions: dict[str, Question] = {}
        subjects: dict[str, Item] = {}
        decided: list[Item] = []
        for entry in selected:
            candidates = candidate_classes(entry.unit_of_measurement)
            if not candidates:
                continue
            if len(candidates) == 1:
                # Code-certain: only one Home Assistant class accepts this
                # unit. classify() never runs on a carried item, so the
                # confidence has to be set here, or the card sync's
                # most-confident-first ranking would read it as 0.0 and sort
                # every code-decided suggestion behind every model answer.
                decided.append(
                    {"registry_id": entry.id, "entity_id": entry.entity_id, "choice": candidates[0], "confidence": 1.0}
                )
                continue
            # index tracks how many sensors have been asked so far, never the
            # loop position: a code-decided sensor sitting between two asked
            # ones must not desynchronize the question index from the state
            # list split.py slices.
            index = len(sensors)
            state_item, subject = describe(hass, entry)
            sensors.append(state_item)
            question_id = f"s{index}"
            questions[question_id] = {
                "type": "choice",
                "instructions": DEVICE_CLASS_INSTRUCTIONS.format(index=index),
                "criteria": criteria(candidates, names),
            }
            subjects[question_id] = subject

        _LOGGER.debug("device class suggestions selected=%s asked=%s decided=%s", len(selected), len(sensors), len(decided))
        return Batch(
            state={"sensors": sensors},
            questions=questions,
            subjects=subjects,
            carried={OPTION_SUGGESTED: decided},
            list_key="sensors",
            template=DEVICE_CLASS_INSTRUCTIONS,
        )

    async def async_act(self, hass: HomeAssistant, result: RecipeResult) -> None:
        """Sync one fixable Repairs card per suggested sensor, and log counts."""
        names = await class_names(hass)
        sync_device_class_cards(hass, self._safety, result["items"].get(OPTION_SUGGESTED, []), names)
        _LOGGER.debug("device class suggestions run complete, counts=%s", result["counts"])

    def _still_qualifies(self, hass: HomeAssistant, item: Item) -> bool:
        """Whether a stored suggestion's sensor, looked up by registry_id, still exists and still qualifies."""
        return qualifying_entry(hass, self._safety, str(item["registry_id"])) is not None

    async def restore(self, hass: HomeAssistant, result: RecipeResult) -> None:
        """Filter every bucket and unsure for sensors that no longer qualify, then re-sync cards.

        A restore calls no API, so a sensor given a class by hand, labelled
        critical, disabled or removed since the run must still drop out of
        the stored result, rather than sitting exposed for up to a week
        until the next paid run notices. Every persistent effect async_act
        has (the card sync) is reproduced here too.
        """
        for option, items in result["items"].items():
            kept = [item for item in items if self._still_qualifies(hass, item)]
            result["items"][option] = kept
            result["counts"][option] = len(kept)
        result["unsure"] = [item for item in result["unsure"] if self._still_qualifies(hass, item)]
        names = await class_names(hass)
        sync_device_class_cards(hass, self._safety, result["items"].get(OPTION_SUGGESTED, []), names, restoring=True)
