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
from .device_class_wording import instructions_for, template_for
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
        """Select every qualifying sensor and ask one choice question per sensor whose unit at least one class accepts.

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
        templates: dict[str, str] = {}
        for entry in selected:
            candidates = candidate_classes(entry.unit_of_measurement)
            if not candidates:
                continue
            # index counts asked sensors, so a sensor whose unit no class
            # accepts cannot desync it from the state list split.py slices.
            index = len(sensors)
            state_item, subject = describe(hass, entry)
            sensors.append(state_item)
            question_id = f"s{index}"
            # Asked even when one class fits, and the instructions carry only
            # the boundary case this sensor's unit needs (never every
            # question's), since a shared addition measurably lowers
            # confidence on unrelated sensors in the same run.
            templates[question_id] = template_for(entry.unit_of_measurement)
            questions[question_id] = {
                "type": "choice",
                "instructions": instructions_for(entry.unit_of_measurement, index),
                "criteria": criteria(candidates, names),
            }
            subjects[question_id] = subject

        _LOGGER.debug("device class suggestions selected=%s asked=%s", len(selected), len(sensors))
        return Batch(
            state={"sensors": sensors},
            questions=questions,
            subjects=subjects,
            list_key="sensors",
            template=DEVICE_CLASS_INSTRUCTIONS,
            templates=templates,
        )

    async def async_act(self, hass: HomeAssistant, result: RecipeResult) -> None:
        """Sync one fixable Repairs card per suggested sensor, and log counts."""
        names = await class_names(hass)
        sync_device_class_cards(hass, self._safety, result["items"].get(OPTION_SUGGESTED, []), names)
        _LOGGER.debug("device class suggestions run complete, counts=%s", result["counts"])

    def _still_qualifies(self, hass: HomeAssistant, item: Item) -> bool:
        """Whether a stored suggestion's sensor, looked up by registry_id, still exists and still qualifies."""
        return qualifying_entry(hass, self._safety, str(item["registry_id"])) is not None

    def _still_fits(self, hass: HomeAssistant, item: Item) -> bool:
        """Whether a stored SUGGESTED item's sensor still qualifies and its stored class still fits the live unit."""
        entry = qualifying_entry(hass, self._safety, str(item["registry_id"]))
        if entry is None:
            return False
        return str(item.get("choice")) in candidate_classes(entry.unit_of_measurement)

    async def restore(self, hass: HomeAssistant, result: RecipeResult) -> None:
        """Filter every bucket and unsure for sensors that no longer qualify, then re-sync cards.

        A restore calls no API, so a sensor given a class by hand, labelled
        critical, disabled, removed, or moved to a unit that no longer
        accepts its stored class since the run must still drop out of the
        stored result, rather than sitting exposed for up to a week until
        the next paid run notices. Every persistent effect async_act has
        (the card sync) is reproduced here too.
        """
        for option, items in result["items"].items():
            kept = [item for item in items if self._still_fits(hass, item)]
            result["items"][option] = kept
            result["counts"][option] = len(kept)
        result["unsure"] = [item for item in result["unsure"] if self._still_qualifies(hass, item)]
        names = await class_names(hass)
        sync_device_class_cards(hass, self._safety, result["items"].get(OPTION_SUGGESTED, []), names, restoring=True)
