"""Device class card resolution on top of the shared suggestion card sync."""

from __future__ import annotations

import functools

from homeassistant.core import HomeAssistant

from ..const import DEVICE_CLASS_ISSUE_PREFIX, ISSUE_DEVICE_CLASS_SUGGESTION, MAX_NEW_DEVICE_CLASS_CARDS_PER_RUN
from ..repairs import async_create_ignored_issue
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


def reject_suggestion(
    hass: HomeAssistant, safety: SafetyRules, names: dict[str, str], registry_id: str, device_class: str
) -> None:
    """Record a change-back as this sensor's rejection: an ignored card, exactly like Don't suggest.

    Resolves through the same _resolve every run uses, so a class the
    sensor's live unit no longer accepts raises no card: that class can no
    longer be suggested for it anyway.
    """
    resolved = _resolve(hass, safety, names, [{"registry_id": registry_id, "choice": device_class}])
    issue_id = f"{DEVICE_CLASS_ISSUE_PREFIX}{registry_id}"
    item = resolved.get(issue_id)
    if item is None:
        return
    async_create_ignored_issue(hass, issue_id, ISSUE_DEVICE_CLASS_SUGGESTION, item.placeholders, item.data)


def sync_device_class_cards(
    hass: HomeAssistant, safety: SafetyRules, suggested: list[Item], names: dict[str, str], *, restoring: bool = False
) -> None:
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
        restoring=restoring,
    )
