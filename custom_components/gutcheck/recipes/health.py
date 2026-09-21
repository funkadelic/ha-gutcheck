"""Home health check recipe: classify unavailable entities."""

from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.const import ATTR_RESTORED, STATE_UNAVAILABLE
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from ..const import (
    CHOICE_CONFIDENCE_THRESHOLD,
    HEALTH_CRITERIA,
    HEALTH_INSTRUCTIONS,
    HEALTH_ISSUE_PREFIX,
    HEALTH_OPTIONS,
    ISSUE_UNAVAILABLE_ENTITY,
    OPTION_WORTH_FIXING,
    RECIPE_HEALTH,
)
from ..describe import bucket_duration, bucket_longer_than
from ..history import async_unavailable_since
from ..models import Question
from ..repairs import async_sync_issues, async_track_recovery
from .gate import gate_choice
from .safety import SafetyRules
from .shapes import Batch, Item, RecipeResult

_LOGGER = logging.getLogger(__name__)


class HealthRecipe:
    """Selects unavailable entities and classifies each with one choice question."""

    recipe_id = RECIPE_HEALTH
    options: tuple[str, ...] = HEALTH_OPTIONS
    # Every field a restore reads off a stored item before the next run replaces it.
    stored_item_keys: frozenset[str] = frozenset({"entity_id", "registry_id", "unavailable_for"})

    def __init__(self, critical_label: str | None) -> None:
        """Build the shared safety guard from the configured critical label."""
        self._safety = SafetyRules(critical_label)
        self._unsub_recovery: CALLBACK_TYPE | None = None

    def gate(self, answer: object) -> str | None:
        """Gate one answer through the choice gate at the health recipe's threshold."""
        return gate_choice(answer, HEALTH_OPTIONS, CHOICE_CONFIDENCE_THRESHOLD)

    def _has_available_sibling(self, hass: HomeAssistant, registry: er.EntityRegistry, entry: er.RegistryEntry) -> bool:
        """Whether the same device still has an entity reporting, which separates a dead device from a dead entity."""
        if not entry.device_id:
            return False
        for sibling in er.async_entries_for_device(registry, entry.device_id):
            if sibling.entity_id == entry.entity_id:
                continue
            sibling_state = hass.states.get(sibling.entity_id)
            if sibling_state is not None and sibling_state.state != STATE_UNAVAILABLE:
                return True
        return False

    def _unavailable_for(
        self,
        entity_id: str,
        state: State,
        history_result: tuple[int, dict[str, datetime | None]] | None,
    ) -> str:
        """Bucket a duration from recorder history, falling back to last_changed."""
        if history_result is None or entity_id not in history_result[1]:
            unavailable_days = int((dt_util.utcnow() - state.last_changed).total_seconds() // 86400)
            return bucket_longer_than(unavailable_days)
        keep_days, since_map = history_result
        run_start = since_map[entity_id]
        if run_start is None:
            return bucket_longer_than(keep_days)
        return bucket_duration((dt_util.utcnow() - run_start).total_seconds())

    async def async_prepare(self, hass: HomeAssistant) -> Batch:
        """Select unavailable, non-critical entities and build the request."""
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
        for index, (entry, state) in enumerate(selected):
            restored = bool(state.attributes.get(ATTR_RESTORED) is True)
            unavailable_for = self._unavailable_for(entry.entity_id, state, history_result)
            entities.append(
                {
                    "domain": entry.domain,
                    "device_class": entry.device_class or entry.original_device_class,
                    "integration": entry.platform,
                    "unavailable_for": unavailable_for,
                    "restored": restored,
                    "entity_category": entry.entity_category.value if entry.entity_category else None,
                    "device_other_entities_available": self._has_available_sibling(hass, registry, entry),
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
                "unavailable_for": unavailable_for,
            }

        _LOGGER.debug("health check selected=%s excluded_by_safety_rules=%s", len(selected), excluded_by_safety_rules)
        return Batch(state={"entities": entities}, questions=questions, subjects=subjects)

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
        return self._safety.excludes_stored(hass, str(item["registry_id"]))

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
        for option, items in list(result["items"].items()):
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
