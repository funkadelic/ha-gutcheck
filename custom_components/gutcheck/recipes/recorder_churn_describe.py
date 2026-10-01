"""Ranking by churn and one entity's model-visible shape for recorder suggestions."""

from __future__ import annotations

from typing import Any, NamedTuple

from homeassistant.core import HomeAssistant, split_entity_id
from homeassistant.helpers import entity_registry as er

from ..const import BLOCKED_DOMAINS, DEVICE_TEXT_MAX_CHARS
from ..describe import clean_text
from .entity_text import entity_text
from .recorder_churn_const import (
    CHURN_BUCKETS,
    CHURN_FLOOR_PER_DAY,
    KEPT_STATE_CLASSES,
    LONG_TERM_STATISTICS_CLASSES,
    REASON_ENERGY,
    REASON_TOTAL_STATE_CLASS,
)
from .safety import SafetyRules
from .shapes import Item


class Ranked(NamedTuple):
    """One entity at or above the floor: its registry entry (None when it has none) and its changes per day."""

    entity_id: str
    entry: er.RegistryEntry | None
    per_day: int


def churn_bucket(per_day: int) -> str:
    """The word for a changes-per-day figure: the first bucket whose bound it reaches, else the lowest."""
    for bound, word in CHURN_BUCKETS:
        if per_day >= bound:
            return word
    return CHURN_BUCKETS[-1][1]


def rank(hass: HomeAssistant, safety: SafetyRules, counts: dict[str, int], window_days: int) -> list[Ranked]:
    """Every allowed, existing entity at or above the floor, heaviest first.

    The per-day figure is the window's count divided by its days. An entity
    the shared safety rules exclude is skipped. One with no registry entry is
    kept only while it still has a live state outside the blocked domains: it
    can carry no label, so those domains are its whole safety check.
    """
    registry = er.async_get(hass)
    ranked: list[Ranked] = []
    for entity_id, count in counts.items():
        per_day = count // window_days
        if per_day < CHURN_FLOOR_PER_DAY:
            continue
        entry = registry.async_get(entity_id)
        if entry is None:
            if hass.states.get(entity_id) is None or split_entity_id(entity_id)[0] in BLOCKED_DOMAINS:
                continue
        elif safety.excludes(hass, entry):
            continue
        ranked.append(Ranked(entity_id, entry, per_day))
    return sorted(ranked, key=lambda item: (-item.per_day, item.entity_id))


def subject(ranked: Ranked) -> Item:
    """The code-only item stored for one ranked entity; the count never leaves code."""
    return {
        "entity_id": ranked.entity_id,
        "registry_id": ranked.entry.id if ranked.entry else None,
        "changes_per_day": ranked.per_day,
        "bucket": churn_bucket(ranked.per_day),
    }


def _clean(text: str | None) -> str | None:
    """Cleaned text capped for the model, None when nothing is left."""
    return clean_text(text, DEVICE_TEXT_MAX_CHARS) or None


def keep_reason(entity_id: str, entry: er.RegistryEntry | None, energy_ids: set[str]) -> str | None:
    """Why code keeps this entity with no question: the Energy dashboard names it, or its state class is a total."""
    if entity_id in energy_ids:
        return REASON_ENERGY
    if entry is not None and (entry.capabilities or {}).get("state_class") in KEPT_STATE_CLASSES:
        return REASON_TOTAL_STATE_CLASS
    return None


def describe(
    hass: HomeAssistant, entry: er.RegistryEntry, per_day: int, *, on_dashboard: bool, referenced: bool
) -> tuple[dict[str, Any], Item]:
    """One entity's model-visible state and its code-only subject.

    Built from the registry entry and its device only: never the entity id,
    a raw count, the live state, an area or a label.
    """
    state_class = (entry.capabilities or {}).get("state_class")
    state_item: dict[str, Any] = {
        **entity_text(hass, entry),
        "domain": entry.domain,
        "integration": entry.platform,
        "device_class": _clean(entry.device_class or entry.original_device_class),
        "unit": _clean(entry.unit_of_measurement),
        "long_term_statistics": state_class in LONG_TERM_STATISTICS_CLASSES,
        "churn": churn_bucket(per_day),
        "referenced": referenced,
        "on_dashboard": on_dashboard,
    }
    asked = subject(Ranked(entry.entity_id, entry, per_day))
    asked["on_dashboard"] = on_dashboard
    return state_item, asked
