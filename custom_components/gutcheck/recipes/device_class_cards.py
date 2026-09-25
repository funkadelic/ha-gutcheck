"""Device class card resolution on top of the shared suggestion card sync."""

from __future__ import annotations

import functools

from homeassistant.core import HomeAssistant

from ..const import DEVICE_CLASS_ISSUE_PREFIX, ISSUE_DEVICE_CLASS_SUGGESTION, MAX_NEW_DEVICE_CLASS_CARDS_PER_RUN
from .device_class_describe import candidate_classes, qualifying_entry
from .safety import SafetyRules
from .shapes import Item
from .suggestion_cards import Resolved, confidence_of, sync_suggestion_cards


def _resolve(hass: HomeAssistant, safety: SafetyRules, names: dict[str, str], suggested: list[Item]) -> dict[str, Resolved]:
    """Every suggestion whose sensor and choice still resolve, keyed by its card's issue id."""
    resolved: dict[str, Resolved] = {}
    for item in suggested:
        entry = qualifying_entry(hass, safety, str(item["registry_id"]))
        if entry is None:
            continue
        choice = str(item.get("choice"))
        if choice not in candidate_classes(entry.unit_of_measurement):
            continue
        placeholders = {
            "entity_id": entry.entity_id,
            "class_name": names.get(choice, choice),
            "unit": str(entry.unit_of_measurement),
        }
        resolved[f"{DEVICE_CLASS_ISSUE_PREFIX}{entry.id}"] = Resolved(
            subject_id=entry.id,
            confidence=confidence_of(item),
            placeholders=placeholders,
            data={"registry_id": entry.id, "device_class": choice},
        )
    return resolved


def _still_qualifies(hass: HomeAssistant, safety: SafetyRules, issue_id: str) -> bool:
    """Whether the sensor behind an existing card (the issue id, prefix stripped) still qualifies."""
    return qualifying_entry(hass, safety, issue_id.removeprefix(DEVICE_CLASS_ISSUE_PREFIX)) is not None


def sync_device_class_cards(hass: HomeAssistant, safety: SafetyRules, suggested: list[Item], names: dict[str, str]) -> None:
    """Create or update a capped, rejection-preserving set of device class suggestion cards."""
    resolved = _resolve(hass, safety, names, suggested)
    still_qualifies = functools.partial(_still_qualifies, hass, safety)
    sync_suggestion_cards(
        hass,
        DEVICE_CLASS_ISSUE_PREFIX,
        ISSUE_DEVICE_CLASS_SUGGESTION,
        MAX_NEW_DEVICE_CLASS_CARDS_PER_RUN,
        resolved,
        suggested,
        still_qualifies,
    )
