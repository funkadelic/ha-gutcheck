"""Update review recipe: score every pending update on a three-level scale."""

from __future__ import annotations

import logging

from homeassistant.const import STATE_ON, Platform
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er

from ..const import (
    MAX_UPDATES_PER_RUN,
    NO_VERDICT_STATES,
    OPTION_POSSIBLY_BREAKING,
    RECIPE_UPDATES,
    UPDATE_CONFIDENCE_THRESHOLD,
    UPDATE_CRITERIA,
    UPDATE_INSTRUCTIONS,
    UPDATE_OPTIONS,
)
from ..models import Question
from .gate import gate_score
from .safety import SafetyRules
from .shapes import Batch, Item, RecipeResult
from .update_cadence import carry_prior, carry_unasked, decided_in_code, most_significant_first
from .update_describe import async_fetch_notes, describe, describe_subject
from .update_repairs import UpdateIssueTracker

_LOGGER = logging.getLogger(__name__)


class UpdateRecipe:
    """Selects pending update entities and scores each with one score question."""

    recipe_id = RECIPE_UPDATES
    options: tuple[str, ...] = UPDATE_OPTIONS
    # Every field a restore reads off a stored item before the next run replaces it.
    stored_item_keys: frozenset[str] = frozenset({"entity_id", "registry_id", "latest_version"})

    def __init__(self, critical_label: str | None) -> None:
        """Build the shared safety guard from the configured critical label."""
        self._safety = SafetyRules(critical_label)
        self._issues = UpdateIssueTracker()

    def gate(self, answer: object) -> str | None:
        """Gate one answer through the score gate at the update recipe's own threshold."""
        return gate_score(answer, UPDATE_OPTIONS, UPDATE_CONFIDENCE_THRESHOLD)

    def _select(self, hass: HomeAssistant) -> tuple[list[tuple[er.RegistryEntry, State]], list[str]]:
        """Every pending, non-excluded update entity ordered by entity id, plus the ids of the silent ones.

        BLOCKED_DOMAINS matches entry.domain, and a lock's or alarm's own
        firmware update entity has domain "update" rather than "lock" or
        "alarm_control_panel", so the shared safety guard never hides one.

        An update entity reports unavailable, unknown or no state at all on
        every reload of its owning integration, none of which says the
        update stopped being pending. Those registry ids come back
        separately to carry their prior classification rather than dropping
        out of the result and losing their card.
        """
        registry = er.async_get(hass)
        selected: list[tuple[er.RegistryEntry, State]] = []
        silent: list[str] = []
        for entry in registry.entities.values():
            if entry.domain != Platform.UPDATE or self._safety.excludes(hass, entry):
                continue
            state = hass.states.get(entry.entity_id)
            if state is not None and state.state == STATE_ON:
                selected.append((entry, state))
            elif state is None or state.state in NO_VERDICT_STATES:
                silent.append(entry.id)
        selected.sort(key=lambda pair: pair[0].entity_id)
        return selected, silent

    async def async_prepare(self, hass: HomeAssistant, previous: RecipeResult | None = None, *, force: bool = False) -> Batch:
        """Select pending updates, carry forward unchanged accepted ones, and ask about the rest.

        force asks again about every update this run can ask about. Silent
        and cap-deferred updates still keep their prior classification.

        A per-run cap keeps the request under the state token limit on a
        large install: only the most significant version jumps are asked
        about this run, and the rest wait for the next one holding the
        classification they already have.
        """
        selected, silent = self._select(hass)
        carried: dict[str, list[Item]] = {}
        to_fetch: list[tuple[er.RegistryEntry, State]] = []
        for entry, state in selected:
            carry = carry_prior(None if force else previous, self.options, describe_subject(entry, state))
            if carry is not None:
                carried.setdefault(carry[0], []).append(carry[1])
            else:
                to_fetch.append((entry, state))

        fetched_notes = await async_fetch_notes(hass, to_fetch)
        to_ask: list[tuple[Item, Item]] = []
        for (entry, state), notes in zip(to_fetch, fetched_notes, strict=True):
            state_item, subject = describe(entry, state, notes), describe_subject(entry, state)
            if decided_in_code(state_item):
                carried.setdefault(OPTION_POSSIBLY_BREAKING, []).append(subject)
            else:
                to_ask.append((state_item, subject))

        asked, deferred = most_significant_first(to_ask, MAX_UPDATES_PER_RUN)
        unasked = set(silent) | {str(subject["registry_id"]) for _, subject in deferred}
        for option, carried_item in carry_unasked(previous, self.options, unasked):
            carried.setdefault(option, []).append(carried_item)

        updates: list[Item] = []
        questions: dict[str, Question] = {}
        subjects: dict[str, Item] = {}
        for index, (state_item, subject) in enumerate(asked):
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
        for option, items in result["items"].items():
            kept = [item for item in items if not self._excluded(hass, item)]
            result["items"][option] = kept
            result["counts"][option] = len(kept)
        result["unsure"] = [item for item in result["unsure"] if not self._excluded(hass, item)]
        self._issues.sync(hass, result["items"].get(OPTION_POSSIBLY_BREAKING, []))

    def shutdown(self) -> None:
        """Cancel the recovery subscription, if any."""
        self._issues.shutdown()
