"""Diagnostic sensor card resolution on top of the shared suggestion card sync."""

from __future__ import annotations

import functools

from homeassistant.core import HomeAssistant

from ..const import HIDE_DIAGNOSTIC_ISSUE_PREFIX, ISSUE_HIDE_DIAGNOSTIC_SUGGESTION
from .hide_diagnostic_const import MAX_NEW_HIDE_DIAGNOSTIC_CARDS_PER_RUN
from .hide_diagnostic_describe import qualifying_entry
from .safety import SafetyRules
from .shapes import Item
from .suggestion_cards import Resolved, confidence_of, sync_suggestion_cards


def _resolve(hass: HomeAssistant, safety: SafetyRules, suggested: list[Item]) -> dict[str, Resolved]:
    """Every suggestion whose sensor still resolves, keyed by its card's issue id."""
    resolved: dict[str, Resolved] = {}
    for item in suggested:
        entry = qualifying_entry(hass, safety, str(item["registry_id"]))
        if entry is None:
            continue
        resolved[f"{HIDE_DIAGNOSTIC_ISSUE_PREFIX}{entry.id}"] = Resolved(
            subject_id=entry.id,
            confidence=confidence_of(item),
            placeholders={"entity_id": entry.entity_id},
            data={"registry_id": entry.id},
        )
    return resolved


def _still_qualifies(hass: HomeAssistant, safety: SafetyRules, issue_id: str) -> bool:
    """Whether the sensor behind an existing card (the issue id, prefix stripped) still qualifies."""
    return qualifying_entry(hass, safety, issue_id.removeprefix(HIDE_DIAGNOSTIC_ISSUE_PREFIX)) is not None


def sync_hide_diagnostic_cards(
    hass: HomeAssistant, safety: SafetyRules, suggested: list[Item], *, restoring: bool = False
) -> None:
    """Create or update a capped, rejection-preserving set of diagnostic sensor cards."""
    sync_suggestion_cards(
        hass,
        HIDE_DIAGNOSTIC_ISSUE_PREFIX,
        ISSUE_HIDE_DIAGNOSTIC_SUGGESTION,
        MAX_NEW_HIDE_DIAGNOSTIC_CARDS_PER_RUN,
        _resolve(hass, safety, suggested),
        suggested,
        functools.partial(_still_qualifies, hass, safety),
        restoring=restoring,
    )
