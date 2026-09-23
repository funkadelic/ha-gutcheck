"""Duration bucketing and the model-visible shape of one unavailable entity."""

from __future__ import annotations

from datetime import datetime

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_RESTORED, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from ..const import HEALTH_LEFTOVER_DAYS
from ..describe import bucket_duration, bucket_longer_than
from .shapes import Item

HistoryResult = tuple[int, dict[str, datetime | None]] | None


def _has_available_sibling(hass: HomeAssistant, registry: er.EntityRegistry, entry: er.RegistryEntry) -> bool:
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


def _unavailable_for(entity_id: str, state: State, history_result: HistoryResult) -> tuple[str, int]:
    """Bucket a duration from recorder history, falling back to last_changed.

    Returns the bucket words, plus the whole days the outage is known to
    have lasted at least: the fallback branch's own day count, the
    beyond-window branch's keep_days, or the known-start branch's elapsed
    days. The leftover rule compares this second value against its
    threshold without re-deriving it from the words.
    """
    if history_result is None or entity_id not in history_result[1]:
        unavailable_days = int((dt_util.utcnow() - state.last_changed).total_seconds() // 86400)
        return bucket_longer_than(unavailable_days), unavailable_days
    keep_days, since_map = history_result
    run_start = since_map[entity_id]
    if run_start is None:
        return bucket_longer_than(keep_days), keep_days
    elapsed = (dt_util.utcnow() - run_start).total_seconds()
    return bucket_duration(elapsed), int(elapsed // 86400)


def describe(
    hass: HomeAssistant,
    registry: er.EntityRegistry,
    entry: er.RegistryEntry,
    state: State,
    history_result: HistoryResult,
) -> tuple[Item, Item, bool]:
    """One entity's model-visible state, its code-only subject, and whether it is a code-decided leftover.

    leftover is true only when restored is true, the outage is known to have
    lasted at least HEALTH_LEFTOVER_DAYS (or the recorder's retention, if
    shorter and the recorder dated the outage), and the owning config entry is
    loaded or the entity has no config entry at all.
    """
    restored = bool(state.attributes.get(ATTR_RESTORED) is True)
    unavailable_for, known_days = _unavailable_for(entry.entity_id, state, history_result)
    # The registry refuses to link an entity to an unknown config entry and
    # drops the entity when its entry is removed, so entry.config_entry_id
    # set but unresolvable has no real path to cover.
    owning_entry = hass.config_entries.async_get_entry(entry.config_entry_id) if entry.config_entry_id else None
    state_item: Item = {
        "domain": entry.domain,
        "device_class": entry.device_class or entry.original_device_class,
        "integration": entry.platform,
        "unavailable_for": unavailable_for,
        "restored": restored,
        "entity_category": entry.entity_category.value if entry.entity_category else None,
        "device_other_entities_available": _has_available_sibling(hass, registry, entry),
    }
    if owning_entry is not None:
        state_item["config_entry_state"] = owning_entry.state.value.replace("_", " ")
    subject: Item = {
        "entity_id": entry.entity_id,
        "registry_id": entry.id,
        "restored": restored,
        "unavailable_for": unavailable_for,
    }
    threshold = HEALTH_LEFTOVER_DAYS
    if history_result is not None and entry.entity_id in history_result[1]:
        # Only a recorder-dated outage is capped, since the recorder cannot see past its own window.
        threshold = min(HEALTH_LEFTOVER_DAYS, history_result[0])
    leftover = restored and known_days >= threshold and (owning_entry is None or owning_entry.state is ConfigEntryState.LOADED)
    return state_item, subject, leftover
