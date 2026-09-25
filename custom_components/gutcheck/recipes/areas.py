"""Area suggestions recipe: one choice question per device with no area."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from ..const import AREA_CONFIDENCE_THRESHOLD, AREA_INSTRUCTIONS, AREA_ISSUE_PREFIX, OPTION_SUGGESTED, RECIPE_AREAS
from ..models import Question
from .area_cards import sync_area_cards
from .area_describe import area_criteria, area_options, describe
from .gate import gate_choice
from .safety import SafetyRules
from .shapes import Batch, Item, RecipeResult

_LOGGER = logging.getLogger(__name__)


class AreaRecipe:
    """Selects devices with no area and classifies each with one choice question."""

    recipe_id = RECIPE_AREAS
    options: tuple[str, ...] = (OPTION_SUGGESTED,)
    issue_prefix = AREA_ISSUE_PREFIX
    # An unsure item carries no choice, so the registry id is the one field
    # every stored item has.
    stored_item_keys: frozenset[str] = frozenset({"registry_id"})

    def __init__(self, critical_label: str | None) -> None:
        """Build the shared safety guard; the allowed area set fills in on the first prepare."""
        self._safety = SafetyRules(critical_label)
        self._allowed: tuple[str, ...] = ()

    def gate(self, answer: object) -> str | None:
        """Gate one answer through the choice gate over this run's own areas.

        OPTION_NONE is never in _allowed, so a confident "none of these"
        lands in unsure rather than being suggested.
        """
        return OPTION_SUGGESTED if gate_choice(answer, self._allowed, AREA_CONFIDENCE_THRESHOLD) is not None else None

    async def async_prepare(self, hass: HomeAssistant, previous: RecipeResult | None = None, *, force: bool = False) -> Batch:
        """Select every qualifying device with no area and ask one choice question per device.

        previous and force are unused, since nothing carries forward between
        runs.
        """
        options = area_options(hass)
        self._allowed = tuple(options.keys())
        if not options:
            _LOGGER.debug("area suggestions found no areas, nothing to ask")
            return Batch(state={"devices": []}, questions={}, subjects={})

        criteria = area_criteria(options)
        registry = dr.async_get(hass)
        selected = sorted(
            (device for device in registry.devices if not self._safety.excludes_device(hass, device)),
            key=lambda device: device.id,
        )

        devices: list[dict[str, Any]] = []
        questions: dict[str, Question] = {}
        subjects: dict[str, Item] = {}
        for index, device in enumerate(selected):
            state_item, subject = describe(hass, device)
            devices.append(state_item)
            question_id = f"d{index}"
            questions[question_id] = {
                "type": "choice",
                "instructions": AREA_INSTRUCTIONS.format(index=index),
                "criteria": criteria,
            }
            subjects[question_id] = subject

        _LOGGER.debug("area suggestions selected=%s asked=%s areas=%s", len(selected), len(devices), len(options))
        return Batch(
            state={"devices": devices}, questions=questions, subjects=subjects, list_key="devices", template=AREA_INSTRUCTIONS
        )

    async def async_act(self, hass: HomeAssistant, result: RecipeResult) -> None:
        """Sync one fixable Repairs card per suggested device, and log counts."""
        sync_area_cards(hass, self._safety, result["items"].get(OPTION_SUGGESTED, []))
        _LOGGER.debug("area suggestions run complete, counts=%s", result["counts"])

    def _still_qualifies(self, hass: HomeAssistant, item: Item) -> bool:
        """Whether a stored suggestion's device, looked up by registry_id, still exists and still qualifies.

        Only registry_id is read off the stored item; its choice is not
        checked here, since a renamed or deleted area is a card-sync
        concern, not a reason to drop the device from the sensor's own list.
        """
        device = dr.async_get(hass).async_get(str(item["registry_id"]))
        return isinstance(device, dr.DeviceEntry) and not self._safety.excludes_device(hass, device)

    async def restore(self, hass: HomeAssistant, result: RecipeResult) -> None:
        """Filter every bucket and unsure for devices that no longer qualify, then re-sync cards.

        A restore calls no API, so a device given an area, labelled
        critical, disabled or removed since the run must still drop out of
        the stored result, rather than sitting exposed for up to a week
        until the next paid run notices. Every persistent effect async_act
        has (the card sync) is reproduced here too, which is what makes a
        restore free but not silent about state that has since changed.
        """
        for option, items in result["items"].items():
            kept = [item for item in items if self._still_qualifies(hass, item)]
            result["items"][option] = kept
            result["counts"][option] = len(kept)
        result["unsure"] = [item for item in result["unsure"] if self._still_qualifies(hass, item)]
        sync_area_cards(hass, self._safety, result["items"].get(OPTION_SUGGESTED, []))
