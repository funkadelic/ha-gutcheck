"""Home health check recipe: classify unavailable entities."""

from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.const import ATTR_RESTORED, STATE_UNAVAILABLE
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, State
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from ..const import (
    BLOCKED_DOMAINS,
    DOMAIN,
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
from ..models import ChoiceQuestion
from ..repairs import async_sync_issues, async_track_recovery
from .base import Batch, Item, RecipeResult

_LOGGER = logging.getLogger(__name__)


class HealthRecipe:
    """Selects unavailable entities and classifies each with one choice question."""

    recipe_id = RECIPE_HEALTH
    options: tuple[str, ...] = HEALTH_OPTIONS

    def __init__(self, critical_label: str | None) -> None:
        """Store the label that marks an entity or device as critical."""
        self._critical_label = critical_label
        self._unsub_recovery: CALLBACK_TYPE | None = None

    def _is_critical(self, entry: er.RegistryEntry, device_registry: dr.DeviceRegistry) -> bool:
        if not self._critical_label:
            return False
        if self._critical_label in entry.labels:
            return True
        if entry.device_id:
            device = device_registry.async_get(entry.device_id)
            if device and self._critical_label in device.labels:
                return True
        return False

    def _has_available_sibling(self, hass: HomeAssistant, registry: er.EntityRegistry, entry: er.RegistryEntry) -> bool:
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
        device_registry = dr.async_get(hass)
        excluded_by_safety_rules = 0
        selected: list[tuple[er.RegistryEntry, State]] = []
        for entry in registry.entities.values():
            if entry.disabled or entry.platform == DOMAIN or entry.domain in BLOCKED_DOMAINS:
                continue
            if self._is_critical(entry, device_registry):
                excluded_by_safety_rules += 1
                continue
            state = hass.states.get(entry.entity_id)
            if state is None or state.state != STATE_UNAVAILABLE:
                continue
            selected.append((entry, state))
        selected.sort(key=lambda pair: pair[0].entity_id)

        history_result = await async_unavailable_since(hass, [entry.entity_id for entry, _ in selected])

        entities: list[Item] = []
        questions: dict[str, ChoiceQuestion] = {}
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
        """Whether a stored finding's entity has since come under the safety rules."""
        entry = er.async_get(hass).async_get(str(item["entity_id"]))
        if entry is None:
            # Unknown now (removed or renamed). Left alone, so an ignore survives it.
            return False
        if entry.disabled or entry.domain in BLOCKED_DOMAINS:
            return True
        return self._is_critical(entry, dr.async_get(hass))

    async def restore(self, hass: HomeAssistant, result: RecipeResult) -> None:
        """Re-sync issues and re-arm recovery tracking for a restored result, without calling the API.

        A restore can follow a disable/re-enable, which deletes every issue under
        HEALTH_ISSUE_PREFIX, so the restored result's issues must be recreated too.
        Findings whose entity has since been labelled critical, disabled or moved
        into a blocked domain are dropped rather than raised again, out of the
        result itself so the summary sensor does not list them either.
        """
        worth_fixing = [item for item in result["items"].get(OPTION_WORTH_FIXING, []) if not self._now_excluded(hass, item)]
        if OPTION_WORTH_FIXING in result["items"]:
            result["items"][OPTION_WORTH_FIXING] = worth_fixing
            result["counts"][OPTION_WORTH_FIXING] = len(worth_fixing)
        async_sync_issues(hass, HEALTH_ISSUE_PREFIX, ISSUE_UNAVAILABLE_ENTITY, self._wanted_issues(worth_fixing))
        self._rearm_recovery(hass, worth_fixing)

    def _wanted_issues(self, worth_fixing: list[Item]) -> dict[str, dict[str, str]]:
        return {
            f"{HEALTH_ISSUE_PREFIX}{item['registry_id']}": {
                "entity_id": str(item["entity_id"]),
                "unavailable_for": str(item["unavailable_for"]),
            }
            for item in worth_fixing
        }

    def _rearm_recovery(self, hass: HomeAssistant, worth_fixing: list[Item]) -> None:
        self.shutdown()
        watched = {str(item["entity_id"]): f"{HEALTH_ISSUE_PREFIX}{item['registry_id']}" for item in worth_fixing}
        if watched:
            self._unsub_recovery = async_track_recovery(hass, watched)

    def shutdown(self) -> None:
        """Cancel the recovery subscription, if any."""
        if self._unsub_recovery is not None:
            self._unsub_recovery()
            self._unsub_recovery = None
