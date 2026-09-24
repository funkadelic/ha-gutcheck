"""Fixable Repairs card sync for suggested area items: rejection memory and a per-run cap."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir

from ..const import AREA_ISSUE_PREFIX, DOMAIN, ISSUE_AREA_SUGGESTION, MAX_NEW_AREA_CARDS_PER_RUN
from ..repairs import async_sync_issues
from .area_describe import area_options
from .safety import SafetyRules
from .shapes import Item

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Resolved:
    """One suggestion whose device and choice both still resolve, ready to become a card."""

    device_id: str
    confidence: float
    placeholders: dict[str, str]
    data: dict[str, str | int | float | None]


def _confidence_of(item: Item) -> float:
    """An item's confidence as a plain float, or 0.0 when it carries none."""
    confidence = item.get("confidence")
    if isinstance(confidence, int | float) and not isinstance(confidence, bool):
        return float(confidence)
    return 0.0


def _resolve(hass: HomeAssistant, options: dict[str, str], suggested: list[Item]) -> dict[str, _Resolved]:
    """Every suggestion whose device and choice still resolve, keyed by its card's issue id.

    options is read once for this call, so a matched name is already the
    live area's own name; there is no second registry read to go stale.
    """
    device_registry = dr.async_get(hass)
    resolved: dict[str, _Resolved] = {}
    for item in suggested:
        device = device_registry.async_get(str(item["registry_id"]))
        if not isinstance(device, dr.DeviceEntry):
            continue
        area_name = str(item.get("choice"))
        area_id = options.get(area_name)
        if area_id is None:
            continue
        device_name = device.name_by_user or device.name or device.model or device.manufacturer or device.id
        resolved[f"{AREA_ISSUE_PREFIX}{device.id}"] = _Resolved(
            device_id=device.id,
            confidence=_confidence_of(item),
            placeholders={"device_name": device_name, "area_name": area_name},
            data={"device_id": device.id, "area_id": area_id},
        )
    return resolved


def _split_new(existing_ids: set[str], resolved: dict[str, _Resolved]) -> tuple[set[str], list[str]]:
    """Resolved ids that already carry a card, and the rest ordered most confident first, device id ascending.

    The device-id tie-break keeps the cut deterministic when two suggestions
    share a confidence value.
    """
    open_ids = existing_ids & resolved.keys()
    new_ids = resolved.keys() - existing_ids
    ordered_new = sorted(new_ids, key=lambda issue_id: (-resolved[issue_id].confidence, resolved[issue_id].device_id))
    return open_ids, ordered_new


def _still_qualifies(hass: HomeAssistant, safety: SafetyRules, issue_id: str) -> bool:
    """Whether the device behind an existing card (the issue id, prefix stripped) still qualifies.

    The device id is the issue id with the prefix removed: the pinned shape
    every area card's id follows.
    """
    device = dr.async_get(hass).async_get(issue_id.removeprefix(AREA_ISSUE_PREFIX))
    return isinstance(device, dr.DeviceEntry) and not safety.excludes_device(hass, device)


def sync_area_cards(hass: HomeAssistant, safety: SafetyRules, suggested: list[Item]) -> None:
    """Create or update a capped, rejection-preserving set of area suggestion cards.

    An open card (its id already exists) is always re-created with this
    run's own suggestion, which is what lets an ignored card's content
    follow the model while HA's own re-create keeps dismissed_version
    untouched (D-05, D-06). A new card arrives only up to
    MAX_NEW_AREA_CARDS_PER_RUN per run, most confident first (D-11). Every
    other existing card is passed straight through untouched as long as its
    device still qualifies, which is what lets a rejection outlive a run
    answering unsure or none of these; once the device stops qualifying,
    its card is swept like any other stale one.
    """
    options = area_options(hass)
    resolved = _resolve(hass, options, suggested)

    registry = ir.async_get(hass)
    existing_ids = {
        issue_id for domain, issue_id in list(registry.issues) if domain == DOMAIN and issue_id.startswith(AREA_ISSUE_PREFIX)
    }

    open_ids, ordered_new = _split_new(existing_ids, resolved)
    accepted_new = ordered_new[:MAX_NEW_AREA_CARDS_PER_RUN]
    deferred_new = ordered_new[MAX_NEW_AREA_CARDS_PER_RUN:]
    wanted_ids = open_ids | set(accepted_new)

    kept = {issue_id for issue_id in existing_ids - wanted_ids if _still_qualifies(hass, safety, issue_id)}

    async_sync_issues(
        hass,
        AREA_ISSUE_PREFIX,
        ISSUE_AREA_SUGGESTION,
        {issue_id: resolved[issue_id].placeholders for issue_id in wanted_ids},
        is_fixable=True,
        issue_data={issue_id: resolved[issue_id].data for issue_id in wanted_ids},
        keep=kept,
    )
    _LOGGER.debug(
        "area card sync open=%s new=%s deferred=%s kept=%s",
        len(open_ids),
        len(accepted_new),
        len(deferred_new),
        len(kept),
    )
