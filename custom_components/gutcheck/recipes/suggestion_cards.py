"""Fixable Repairs card sync shared by every suggestion recipe: rejection memory and a per-run cap."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from ..const import DOMAIN, ITEM_HELD_BACK
from ..repairs import async_sync_issues
from .shapes import Item

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Resolved:
    """One suggestion whose subject and choice both still resolve, ready to become a card."""

    subject_id: str
    confidence: float
    placeholders: dict[str, str]
    data: dict[str, str | int | float | None]


def confidence_of(item: Item) -> float:
    """An item's confidence as a plain float, or 0.0 when it carries none."""
    confidence = item.get("confidence")
    if isinstance(confidence, int | float) and not isinstance(confidence, bool):
        return float(confidence)
    return 0.0


def _split_new(existing_ids: set[str], resolved: dict[str, Resolved]) -> tuple[set[str], list[str]]:
    """Resolved ids that already carry a card, and the rest ordered most confident first, subject id ascending.

    The subject-id tie-break keeps the cut deterministic when two suggestions
    share a confidence value.
    """
    open_ids = existing_ids & resolved.keys()
    new_ids = resolved.keys() - existing_ids
    ordered_new = sorted(new_ids, key=lambda issue_id: (-resolved[issue_id].confidence, resolved[issue_id].subject_id))
    return open_ids, ordered_new


def _attempted_ids(prefix: str, suggested: list[Item]) -> set[str]:
    """Every subject this run's suggested list names, whether or not it fully resolved.

    Built straight from each item's own registry_id, with no registry
    lookup: a subject the model answered about this run but whose target no
    longer resolves is still "attempted", which is what tells an existing
    card to be swept rather than kept as a rejection.
    """
    return {f"{prefix}{item['registry_id']}" for item in suggested}


def _accept_new(prefix: str, suggested: list[Item], new_ids: list[str], cap: int, restoring: bool) -> list[str]:
    """New card ids to raise now; a run marks the rest held back on their own stored items.

    A run takes the cap's worth. A restore takes no cap of its own: it
    re-raises every card the run raised, including one a switch-off
    deleted, and none the run held back.
    """
    if restoring:
        held = {f"{prefix}{item['registry_id']}" for item in suggested if item.get(ITEM_HELD_BACK)}
        return [issue_id for issue_id in new_ids if issue_id not in held]
    deferred = set(new_ids[cap:])
    for item in suggested:
        if f"{prefix}{item['registry_id']}" in deferred:
            item[ITEM_HELD_BACK] = True
    return new_ids[:cap]


def sync_suggestion_cards(
    hass: HomeAssistant,
    prefix: str,
    translation_key: str,
    cap: int,
    resolved: dict[str, Resolved],
    suggested: list[Item],
    still_qualifies: Callable[[str], bool],
    *,
    restoring: bool = False,
) -> None:
    """Create or update a capped, rejection-preserving set of suggestion cards.

    An open card (its id already exists) is always re-created with this
    run's own suggestion, which is what lets an ignored card's content
    follow the model while HA's own re-create keeps dismissed_version
    untouched. A new card arrives only up to cap per run, most confident
    first; a restore re-raises the run's own cards instead. An existing
    card for a subject this run never suggested at all (an unsure or
    none-of-these answer) is passed straight through
    untouched as long as its target still qualifies, which is what lets a
    rejection outlive a noisy run; once the target stops qualifying, its
    card is swept like any other stale one. A subject this run did
    suggest, but whose choice no longer resolves, is swept too rather than
    kept: that suggestion is stale, not rejected.
    """
    attempted = _attempted_ids(prefix, suggested)

    registry = ir.async_get(hass)
    existing_ids = {issue_id for domain, issue_id in registry.issues if domain == DOMAIN and issue_id.startswith(prefix)}

    open_ids, ordered_new = _split_new(existing_ids, resolved)
    accepted_new = _accept_new(prefix, suggested, ordered_new, cap, restoring)
    wanted_ids = open_ids | set(accepted_new)

    kept = {issue_id for issue_id in existing_ids - wanted_ids if issue_id not in attempted and still_qualifies(issue_id)}

    async_sync_issues(
        hass,
        prefix,
        translation_key,
        {issue_id: resolved[issue_id].placeholders for issue_id in wanted_ids},
        is_fixable=True,
        # A kept card's suggestion came from an earlier run and cannot be rebuilt after a restart.
        is_persistent=True,
        issue_data={issue_id: resolved[issue_id].data for issue_id in wanted_ids},
        keep=kept,
    )
    _LOGGER.debug(
        "%s card sync open=%s new=%s deferred=%s kept=%s",
        prefix,
        len(open_ids),
        len(accepted_new),
        len(ordered_new) - len(accepted_new),
        len(kept),
    )
