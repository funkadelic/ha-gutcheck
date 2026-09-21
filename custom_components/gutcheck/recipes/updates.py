"""Update review recipe: score every pending update on a three-level scale."""

from __future__ import annotations

import logging

from homeassistant.const import STATE_ON, Platform
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er

from ..const import RECIPE_UPDATES, UPDATE_CONFIDENCE_THRESHOLD, UPDATE_CRITERIA, UPDATE_INSTRUCTIONS, UPDATE_OPTIONS
from ..models import Question
from .gate import gate_score
from .safety import SafetyRules
from .shapes import Batch, Item, RecipeResult

_LOGGER = logging.getLogger(__name__)


class UpdateRecipe:
    """Selects pending update entities and scores each with one score question."""

    recipe_id = RECIPE_UPDATES
    options: tuple[str, ...] = UPDATE_OPTIONS
    # Every field a restore reads off a stored item before the next run replaces it.
    stored_item_keys: frozenset[str] = frozenset({"entity_id", "registry_id"})

    def __init__(self, critical_label: str | None) -> None:
        """Build the shared safety guard from the configured critical label."""
        self._safety = SafetyRules(critical_label)

    def gate(self, answer: object) -> str | None:
        """Gate one answer through the score gate at the update recipe's own threshold."""
        return gate_score(answer, UPDATE_OPTIONS, UPDATE_CONFIDENCE_THRESHOLD)

    def _select(self, hass: HomeAssistant) -> list[tuple[er.RegistryEntry, State]]:
        """Every pending, non-excluded update entity, ordered by entity id.

        BLOCKED_DOMAINS matches entry.domain, and a lock's or alarm's own
        firmware update entity has domain "update" rather than "lock" or
        "alarm_control_panel", so the shared safety guard never hides one.
        """
        registry = er.async_get(hass)
        selected: list[tuple[er.RegistryEntry, State]] = []
        for entry in registry.entities.values():
            if entry.domain != Platform.UPDATE or self._safety.excludes(hass, entry):
                continue
            state = hass.states.get(entry.entity_id)
            if state is None or state.state != STATE_ON:
                continue
            selected.append((entry, state))
        selected.sort(key=lambda pair: pair[0].entity_id)
        return selected

    def _describe(self, entry: er.RegistryEntry, state: State) -> tuple[Item, Item]:
        """Build one update's model-visible state fields and its code-only subject fields.

        release_url lives only in the subject, never in the state, so it
        never reaches the model and only ever reaches the Repairs card.
        """
        attributes = state.attributes
        state_item: Item = {
            "integration": entry.platform,
            "installed_version": attributes.get("installed_version"),
            "latest_version": attributes.get("latest_version"),
            "title": attributes.get("title"),
            "release_summary": attributes.get("release_summary"),
        }
        subject: Item = {
            "entity_id": entry.entity_id,
            "registry_id": entry.id,
            "installed_version": attributes.get("installed_version"),
            "latest_version": attributes.get("latest_version"),
            "skipped_version": attributes.get("skipped_version"),
            "release_url": attributes.get("release_url"),
        }
        return state_item, subject

    async def async_prepare(self, hass: HomeAssistant) -> Batch:
        """Select pending updates and build one score question per entity."""
        selected = self._select(hass)

        updates: list[Item] = []
        questions: dict[str, Question] = {}
        subjects: dict[str, Item] = {}
        for index, (entry, state) in enumerate(selected):
            state_item, subject = self._describe(entry, state)
            updates.append(state_item)
            question_id = f"u{index}"
            questions[question_id] = {
                "type": "score",
                "instructions": UPDATE_INSTRUCTIONS.format(index=index),
                "criteria": UPDATE_CRITERIA,
            }
            subjects[question_id] = subject

        _LOGGER.debug("update review selected=%s", len(selected))
        return Batch(state={"updates": updates}, questions=questions, subjects=subjects)

    async def async_act(self, hass: HomeAssistant, result: RecipeResult) -> None:
        """Log counts only. Repairs and the run cache arrive in the next plan."""
        _LOGGER.debug("update review run complete, counts=%s", result["counts"])

    async def restore(self, hass: HomeAssistant, result: RecipeResult) -> None:
        """Nothing to re-arm yet. Repairs tracking arrives in the next plan."""
