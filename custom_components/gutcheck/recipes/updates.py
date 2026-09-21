"""Update review recipe: score every pending update on a three-level scale."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.components.update import DATA_COMPONENT, UpdateEntity, UpdateEntityFeature  # type: ignore[attr-defined]
from homeassistant.const import STATE_ON, Platform
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er

from ..const import (
    MAX_UPDATES_PER_RUN,
    OPTION_POSSIBLY_BREAKING,
    RECIPE_UPDATES,
    UPDATE_CONFIDENCE_THRESHOLD,
    UPDATE_CRITERIA,
    UPDATE_INSTRUCTIONS,
    UPDATE_OPTIONS,
)
from ..describe import clean_release_notes, version_jump
from ..models import Question
from .gate import gate_score
from .safety import SafetyRules
from .shapes import Batch, Item, RecipeResult
from .update_cadence import carry_bucket, most_significant_first
from .update_repairs import UpdateIssueTracker

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
        self._issues = UpdateIssueTracker()

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

    async def _async_fetch_one_note(self, hass: HomeAssistant, entry: er.RegistryEntry) -> str | None:
        """Fetch one entity's full release notes through the same path HA's own websocket handler uses.

        Requires the entity to exist, be available, and support the
        release-notes feature; anything else returns None so the caller
        falls back to release_summary. A raised exception is left to
        propagate: the caller gathers with return_exceptions=True so one
        entity's failure cannot stall or fail the whole run.
        """
        entity: UpdateEntity | None = hass.data[DATA_COMPONENT].get_entity(entry.entity_id)
        if entity is None or not entity.available or UpdateEntityFeature.RELEASE_NOTES not in entity.supported_features:
            return None
        return await entity.async_release_notes()

    async def _async_fetch_all_notes(
        self, hass: HomeAssistant, selected: list[tuple[er.RegistryEntry, State]]
    ) -> list[str | BaseException | None]:
        """Fetch every selected entity's release notes concurrently, one outcome per entity in order."""
        return await asyncio.gather(
            *(self._async_fetch_one_note(hass, entry) for entry, _ in selected),
            return_exceptions=True,
        )

    def _describe(self, entry: er.RegistryEntry, state: State, fetched_notes: str | BaseException | None) -> tuple[Item, Item]:
        """Build one update's model-visible state fields and its code-only subject fields.

        release_url lives only in the subject, never in the state, so it
        never reaches the model and only ever reaches the Repairs card.
        """
        attributes = state.attributes
        installed_version = attributes.get("installed_version")
        latest_version = attributes.get("latest_version")
        release_summary = attributes.get("release_summary")
        if isinstance(fetched_notes, BaseException):
            _LOGGER.debug(
                "update review release notes fetch failed integration=%s error=%s",
                entry.platform,
                type(fetched_notes).__name__,
            )
            notes_text = release_summary
        else:
            notes_text = fetched_notes or release_summary
        state_item: Item = {
            "integration": entry.platform,
            "installed_version": installed_version,
            "latest_version": latest_version,
            "version_jump": version_jump(installed_version, latest_version),
            "title": attributes.get("title"),
            "release_summary": release_summary,
            "release_notes": clean_release_notes(notes_text),
        }
        subject: Item = {
            "entity_id": entry.entity_id,
            "registry_id": entry.id,
            "installed_version": installed_version,
            "latest_version": latest_version,
            "skipped_version": attributes.get("skipped_version"),
            "release_url": attributes.get("release_url"),
        }
        return state_item, subject

    async def async_prepare(self, hass: HomeAssistant, previous: RecipeResult | None = None) -> Batch:
        """Select pending updates, carry forward unchanged accepted ones, and ask about the rest.

        A per-run cap keeps the request under the state token limit on a
        large install: only the most significant version jumps are asked
        about this run, and the rest wait for the next one.
        """
        selected = self._select(hass)
        fetched_notes = await self._async_fetch_all_notes(hass, selected)
        described = [self._describe(entry, state, notes) for (entry, state), notes in zip(selected, fetched_notes, strict=True)]

        carried: dict[str, list[Item]] = {}
        to_ask: list[tuple[Item, Item]] = []
        for state_item, subject in described:
            carry = carry_bucket(previous, self.options, state_item, subject)
            if carry is not None:
                option, carried_item = carry
                carried.setdefault(option, []).append(carried_item)
            else:
                to_ask.append((state_item, subject))

        updates: list[Item] = []
        questions: dict[str, Question] = {}
        subjects: dict[str, Item] = {}
        for index, (state_item, subject) in enumerate(most_significant_first(to_ask, MAX_UPDATES_PER_RUN)):
            updates.append(state_item)
            question_id = f"u{index}"
            questions[question_id] = {
                "type": "score",
                "instructions": UPDATE_INSTRUCTIONS.format(index=index),
                "criteria": UPDATE_CRITERIA,
            }
            subjects[question_id] = subject

        carried_count = sum(len(bucket) for bucket in carried.values())
        _LOGGER.debug("update review selected=%s asked=%s carried=%s", len(selected), len(updates), carried_count)
        return Batch(state={"updates": updates}, questions=questions, subjects=subjects, carried=carried)

    async def async_act(self, hass: HomeAssistant, result: RecipeResult) -> None:
        """Sync a Repairs issue per possibly-breaking update, re-arm recovery tracking, and log counts."""
        self._issues.sync(hass, result["items"].get(OPTION_POSSIBLY_BREAKING, []))
        _LOGGER.debug("update review run complete, counts=%s", result["counts"])

    def _excluded(self, hass: HomeAssistant, item: Item) -> bool:
        """Whether a stored finding's entity has since come under the safety rules.

        Looks the entity up by registry id, which survives a rename that
        leaves the stored entity id resolving to nothing.
        """
        return self._safety.excludes_entity_id(hass, str(item["registry_id"]))

    async def restore(self, hass: HomeAssistant, result: RecipeResult) -> None:
        """Filter every bucket for entities the safety rules now exclude, then re-sync Repairs.

        A restore calls no API, so an entity that has since been labelled
        critical, disabled, or moved into a blocked domain must still drop
        out of every bucket, including unsure, rather than sitting exposed
        for up to a week until the next paid run notices.
        """
        for option, items in list(result["items"].items()):
            kept = [item for item in items if not self._excluded(hass, item)]
            result["items"][option] = kept
            result["counts"][option] = len(kept)
        result["unsure"] = [item for item in result["unsure"] if not self._excluded(hass, item)]
        self._issues.sync(hass, result["items"].get(OPTION_POSSIBLY_BREAKING, []))

    def shutdown(self) -> None:
        """Cancel the recovery subscription, if any."""
        self._issues.shutdown()
