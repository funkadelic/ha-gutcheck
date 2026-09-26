"""Critical label card resolution on top of the shared suggestion card sync."""

from __future__ import annotations

import functools

from homeassistant.core import HomeAssistant
from homeassistant.helpers import label_registry as lr

from ..const import CRITICAL_LABEL_ISSUE_PREFIX, ISSUE_CRITICAL_LABEL_SUGGESTION
from .critical_label_const import MAX_NEW_CRITICAL_LABEL_CARDS_PER_RUN
from .critical_label_describe import configured_label, decided_in_code, qualifying_entry
from .safety import SafetyRules
from .shapes import Item
from .suggestion_cards import Resolved, confidence_of, sync_suggestion_cards


def _resolve(hass: HomeAssistant, safety: SafetyRules, label: lr.LabelEntry, suggested: list[Item]) -> dict[str, Resolved]:
    """Every suggestion whose entity still resolves, keyed by its card's issue id.

    Ranks a code-decided entity (a smoke, carbon monoxide, gas or moisture
    sensor) ahead of every model-decided one, following the live entry's own
    predicate rather than a stored marker on the item.
    """
    resolved: dict[str, Resolved] = {}
    for item in suggested:
        entry = qualifying_entry(hass, safety, str(item["registry_id"]))
        if entry is None:
            continue
        placeholders = {"entity_id": entry.entity_id, "label_name": label.name}
        resolved[f"{CRITICAL_LABEL_ISSUE_PREFIX}{entry.id}"] = Resolved(
            subject_id=entry.id,
            confidence=confidence_of(item),
            placeholders=placeholders,
            data={"registry_id": entry.id, "label_id": label.label_id},
            tier=1 if decided_in_code(entry) else 0,
        )
    return resolved


def _still_qualifies(hass: HomeAssistant, safety: SafetyRules, issue_id: str) -> bool:
    """Whether the entity behind an existing card (the issue id, prefix stripped) still qualifies."""
    return qualifying_entry(hass, safety, issue_id.removeprefix(CRITICAL_LABEL_ISSUE_PREFIX)) is not None


def sync_critical_label_cards(
    hass: HomeAssistant, safety: SafetyRules, suggested: list[Item], *, restoring: bool = False
) -> None:
    """Create or update a capped, rejection-preserving set of critical label suggestion cards."""
    label = configured_label(hass, safety)
    resolved = _resolve(hass, safety, label, suggested) if label is not None else {}
    still_qualifies = functools.partial(_still_qualifies, hass, safety)
    sync_suggestion_cards(
        hass,
        CRITICAL_LABEL_ISSUE_PREFIX,
        ISSUE_CRITICAL_LABEL_SUGGESTION,
        MAX_NEW_CRITICAL_LABEL_CARDS_PER_RUN,
        resolved,
        suggested if label is not None else [],
        still_qualifies,
        restoring=restoring,
    )
