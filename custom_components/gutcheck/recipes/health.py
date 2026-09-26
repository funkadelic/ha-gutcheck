"""Home health check recipe: classify unavailable entities."""

from __future__ import annotations

import logging

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, State
from homeassistant.helpers import entity_registry as er

from ..const import (
    CHOICE_CONFIDENCE_THRESHOLD,
    HEALTH_ISSUE_PREFIX,
    HEALTH_OPTIONS,
    ISSUE_UNAVAILABLE_ENTITY,
    OPTION_EXPECTED,
    OPTION_SAFE_TO_REMOVE,
    OPTION_WORTH_FIXING,
    PROBABILITY_ROUNDING_ALLOWANCE,
    RECIPE_HEALTH,
)
from ..history import async_unavailable_since
from ..models import Question
from ..repairs import async_sync_issues, async_track_recovery
from .gate import gate_choice, unit_interval
from .health_const import HEALTH_CRITERIA, HEALTH_INSTRUCTIONS, HEALTH_LEAN_THRESHOLD, LEAN_NEEDS_ATTENTION
from .health_describe import describe
from .safety import SafetyRules
from .shapes import Batch, Item, RecipeResult

_NEEDS_ATTENTION_OPTIONS = (OPTION_WORTH_FIXING, OPTION_SAFE_TO_REMOVE)


def _side_total(probabilities: dict[str, object], options: tuple[str, ...]) -> float | None:
    """The summed, validated probability across options; missing options count as 0.

    Any option holding a probability outside the API's documented range
    rejects the whole side, rather than silently dropping it from the sum.
    """
    total = 0.0
    for option in options:
        value = unit_interval(probabilities.get(option, 0.0))
        if value is None:
            return None
        total += value
    return total


_LOGGER = logging.getLogger(__name__)


class HealthRecipe:
    """Selects unavailable entities and classifies each with one choice question."""

    recipe_id = RECIPE_HEALTH
    options: tuple[str, ...] = HEALTH_OPTIONS
    issue_prefix = HEALTH_ISSUE_PREFIX
    # Every field a restore reads off a stored item before the next run replaces it.
    stored_item_keys: frozenset[str] = frozenset({"entity_id", "registry_id", "unavailable_for"})

    def __init__(self, critical_label: str | None) -> None:
        """Build the shared safety guard from the configured critical label."""
        self._safety = SafetyRules(critical_label)
        self._unsub_recovery: CALLBACK_TYPE | None = None

    def gate(self, answer: object) -> str | None:
        """Gate one answer through the choice gate at the health recipe's threshold."""
        return gate_choice(answer, HEALTH_OPTIONS, CHOICE_CONFIDENCE_THRESHOLD)

    def lean(self, answer: object) -> str | None:
        """Which side an unsure choice answer clearly leans toward, if only one side clears the threshold.

        none_of_these is ignored, so its probability counts toward neither side.
        """
        if not isinstance(answer, dict) or answer.get("type") != "choice":
            return None
        probabilities = answer.get("probabilities")
        if not isinstance(probabilities, dict):
            return None
        needs_attention = _side_total(probabilities, _NEEDS_ATTENTION_OPTIONS)
        expected = _side_total(probabilities, (OPTION_EXPECTED,))
        if needs_attention is None or expected is None:
            return None
        # Mutually exclusive options summing past 1 is a malformed spread, not a lean.
        if needs_attention + expected > 1.0 + PROBABILITY_ROUNDING_ALLOWANCE:
            return None
        attention_clears = needs_attention >= HEALTH_LEAN_THRESHOLD
        expected_clears = expected >= HEALTH_LEAN_THRESHOLD
        if attention_clears == expected_clears:
            return None
        return LEAN_NEEDS_ATTENTION if attention_clears else OPTION_EXPECTED

    async def async_prepare(self, hass: HomeAssistant, previous: RecipeResult | None = None, *, force: bool = False) -> Batch:
        """Select unavailable, non-critical entities and build the request.

        previous and force are unused, since nothing carries over from the
        last run. A long-gone, restored entity whose owning integration is
        loaded (or which has none) is decided in code and carried straight
        into safe_to_remove; everything else is asked.
        """
        registry = er.async_get(hass)
        excluded_by_safety_rules = 0
        selected: list[tuple[er.RegistryEntry, State]] = []
        for entry in registry.entities.values():
            if self._safety.excludes(hass, entry):
                excluded_by_safety_rules += 1
                continue
            state = hass.states.get(entry.entity_id)
            if state is None or state.state != STATE_UNAVAILABLE:
                continue
            selected.append((entry, state))
        selected.sort(key=lambda pair: pair[0].entity_id)

        history_result = await async_unavailable_since(hass, [entry.entity_id for entry, _ in selected])

        entities: list[Item] = []
        questions: dict[str, Question] = {}
        subjects: dict[str, Item] = {}
        carried: dict[str, list[Item]] = {OPTION_SAFE_TO_REMOVE: []}
        for entry, state in selected:
            state_item, subject, leftover = describe(hass, registry, entry, state, history_result)
            if leftover:
                carried[OPTION_SAFE_TO_REMOVE].append(subject)
                continue
            index = len(entities)
            entities.append(state_item)
            question_id = f"e{index}"
            questions[question_id] = {
                "type": "choice",
                "instructions": HEALTH_INSTRUCTIONS.format(index=index),
                "criteria": HEALTH_CRITERIA,
            }
            subjects[question_id] = subject

        _LOGGER.debug(
            "health check selected=%s asked=%s decided_by_rule=%s excluded_by_safety_rules=%s",
            len(selected),
            len(entities),
            len(carried[OPTION_SAFE_TO_REMOVE]),
            excluded_by_safety_rules,
        )
        return Batch(
            state={"entities": entities},
            questions=questions,
            subjects=subjects,
            carried=carried,
            list_key="entities",
            template=HEALTH_INSTRUCTIONS,
        )

    async def async_act(self, hass: HomeAssistant, result: RecipeResult) -> None:
        """Sync a Repairs issue per worth-fixing entity, re-arm recovery tracking, and log counts."""
        worth_fixing = result["items"].get(OPTION_WORTH_FIXING, [])
        async_sync_issues(hass, HEALTH_ISSUE_PREFIX, ISSUE_UNAVAILABLE_ENTITY, self._wanted_issues(worth_fixing))
        self._rearm_recovery(hass, worth_fixing)

        _LOGGER.debug("health check run complete, counts=%s", result["counts"])

    def _now_excluded(self, hass: HomeAssistant, item: Item) -> bool:
        """Whether a stored finding's entity has since come under the safety rules.

        Looks the entity up by registry id, which survives a rename that leaves
        the stored entity id resolving to nothing.
        """
        return self._safety.excludes_entity_id(hass, str(item["registry_id"]))

    async def restore(self, hass: HomeAssistant, result: RecipeResult) -> None:
        """Re-sync issues and re-arm recovery tracking for a restored result, without calling the API.

        A restore can follow a disable/re-enable, which deletes every issue under
        HEALTH_ISSUE_PREFIX, so the restored result's issues must be recreated too.
        Findings whose entity has since been labelled critical, disabled or moved
        into a blocked domain are dropped rather than raised again, out of the
        result itself so the summary sensor does not list them either. Every
        bucket is filtered, not just worth-fixing: a newly critical entity must
        not sit in safe-to-remove either, waiting for the next paid run.
        """
        for option, items in result["items"].items():
            kept = [item for item in items if not self._now_excluded(hass, item)]
            result["items"][option] = kept
            result["counts"][option] = len(kept)
        worth_fixing = result["items"].get(OPTION_WORTH_FIXING, [])
        async_sync_issues(hass, HEALTH_ISSUE_PREFIX, ISSUE_UNAVAILABLE_ENTITY, self._wanted_issues(worth_fixing))
        self._rearm_recovery(hass, worth_fixing)

    def _wanted_issues(self, worth_fixing: list[Item]) -> dict[str, dict[str, str]]:
        """The issue id and placeholders for each worth-fixing finding, keyed by registry id."""
        return {
            f"{HEALTH_ISSUE_PREFIX}{item['registry_id']}": {
                "entity_id": str(item["entity_id"]),
                "unavailable_for": str(item["unavailable_for"]),
            }
            for item in worth_fixing
        }

    def _rearm_recovery(self, hass: HomeAssistant, worth_fixing: list[Item]) -> None:
        """Point the recovery watcher at the current findings, dropping the previous subscription."""
        self.shutdown()
        watched = {str(item["entity_id"]): f"{HEALTH_ISSUE_PREFIX}{item['registry_id']}" for item in worth_fixing}
        if watched:
            self._unsub_recovery = async_track_recovery(hass, watched)

    def shutdown(self) -> None:
        """Cancel the recovery subscription, if any."""
        if self._unsub_recovery is not None:
            self._unsub_recovery()
            self._unsub_recovery = None
