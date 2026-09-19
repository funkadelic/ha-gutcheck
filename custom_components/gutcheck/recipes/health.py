"""Home health check recipe: classify unavailable entities."""

from __future__ import annotations

import logging

from homeassistant.const import ATTR_RESTORED, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from ..const import BLOCKED_DOMAINS, DOMAIN, HEALTH_CRITERIA, HEALTH_INSTRUCTIONS, HEALTH_OPTIONS, RECIPE_HEALTH
from ..models import ChoiceQuestion
from .base import Batch, Item, RecipeResult

_LOGGER = logging.getLogger(__name__)


class HealthRecipe:
    """Selects unavailable entities and classifies each with one choice question."""

    recipe_id = RECIPE_HEALTH
    options: tuple[str, ...] = HEALTH_OPTIONS

    def __init__(self, critical_label: str | None) -> None:
        """Store the label that marks an entity or device as critical."""
        self._critical_label = critical_label

    async def async_prepare(self, hass: HomeAssistant) -> Batch:
        """Select unavailable, non-critical entities and build the request."""
        registry = er.async_get(hass)
        selected = []
        for entry in registry.entities.values():
            if entry.disabled or entry.platform == DOMAIN or entry.domain in BLOCKED_DOMAINS:
                continue
            state = hass.states.get(entry.entity_id)
            if state is None or state.state != STATE_UNAVAILABLE:
                continue
            selected.append(entry)
        selected.sort(key=lambda entry: entry.entity_id)

        entities: list[Item] = []
        questions: dict[str, ChoiceQuestion] = {}
        subjects: dict[str, Item] = {}
        for index, entry in enumerate(selected):
            state = hass.states.get(entry.entity_id)
            restored = bool(state and state.attributes.get(ATTR_RESTORED) is True)
            entities.append(
                {
                    "domain": entry.domain,
                    "device_class": entry.device_class or entry.original_device_class,
                    "integration": entry.platform,
                    "restored": restored,
                    "entity_category": entry.entity_category.value if entry.entity_category else None,
                }
            )
            question_id = f"e{index}"
            questions[question_id] = {
                "type": "choice",
                "instructions": HEALTH_INSTRUCTIONS.format(index=index),
                "criteria": HEALTH_CRITERIA,
            }
            subjects[question_id] = {
                "entity_id": entry.entity_id,
                "registry_id": entry.id,
                "restored": restored,
            }

        return Batch(state={"entities": entities}, questions=questions, subjects=subjects)

    async def async_act(self, hass: HomeAssistant, result: RecipeResult) -> None:
        """Log the run's counts. No entity, device or area names, ever."""
        _LOGGER.debug("health check run complete, counts=%s", result["counts"])
