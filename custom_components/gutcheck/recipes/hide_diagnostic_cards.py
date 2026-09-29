"""Diagnostic sensor card resolution on top of the shared suggestion card sync."""

from __future__ import annotations

import functools

from homeassistant.core import HomeAssistant

from ..const import HIDE_DIAGNOSTIC_ISSUE_PREFIX, ISSUE_HIDE_DIAGNOSTIC_SUGGESTION
from ..repairs import async_create_ignored_issue
from .hide_diagnostic_const import MAX_NEW_HIDE_DIAGNOSTIC_CARDS_PER_RUN
from .hide_diagnostic_describe import decided_in_code, qualifying_entry
from .safety import SafetyRules
from .shapes import Item
from .suggestion_cards import Resolved, confidence_of, sync_suggestion_cards


def _resolve(
    hass: HomeAssistant, safety: SafetyRules, suggested: list[Item], *, ignore_overlap: bool = False
) -> dict[str, Resolved]:
    """Every suggestion whose sensor still resolves, keyed by its card's issue id, code-decided ones ranked first."""
    resolved: dict[str, Resolved] = {}
    for item in suggested:
        entry = qualifying_entry(hass, safety, str(item["registry_id"]), ignore_overlap=ignore_overlap)
        if entry is None:
            continue
        resolved[f"{HIDE_DIAGNOSTIC_ISSUE_PREFIX}{entry.id}"] = Resolved(
            subject_id=entry.id,
            confidence=confidence_of(item),
            placeholders={"entity_id": entry.entity_id},
            data={"registry_id": entry.id},
            tier=1 if decided_in_code(entry) else 0,
        )
    return resolved


def _still_qualifies(hass: HomeAssistant, safety: SafetyRules, issue_id: str) -> bool:
    """Whether the sensor behind an existing card (the issue id, prefix stripped) still qualifies.

    Only decides whether an ignored card with no suggestion this run is kept,
    so an open device class card must not cost the user's rejection.
    """
    registry_id = issue_id.removeprefix(HIDE_DIAGNOSTIC_ISSUE_PREFIX)
    return qualifying_entry(hass, safety, registry_id, ignore_overlap=True) is not None


def reject_suggestion(hass: HomeAssistant, safety: SafetyRules, registry_id: str) -> None:
    """Record a change-back as an ignored card, the same rejection Don't suggest leaves.

    Overlap-free, so an open device class card never stops it being recorded.
    A sensor that no longer qualifies gets none: it cannot be suggested anyway.
    """
    issue_id = f"{HIDE_DIAGNOSTIC_ISSUE_PREFIX}{registry_id}"
    item = _resolve(hass, safety, [{"registry_id": registry_id}], ignore_overlap=True).get(issue_id)
    if item is None:
        return
    async_create_ignored_issue(hass, issue_id, ISSUE_HIDE_DIAGNOSTIC_SUGGESTION, item.placeholders, item.data)


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
