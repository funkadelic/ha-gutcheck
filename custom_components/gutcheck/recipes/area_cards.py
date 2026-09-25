"""Device-suggestion resolution for area cards, on top of the shared suggestion card sync."""

from __future__ import annotations

import functools

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from ..const import AREA_ISSUE_PREFIX, ISSUE_AREA_SUGGESTION, MAX_NEW_AREA_CARDS_PER_RUN
from .area_describe import area_options
from .safety import SafetyRules
from .shapes import Item
from .suggestion_cards import Resolved, confidence_of, sync_suggestion_cards


def _resolve(hass: HomeAssistant, options: dict[str, str], suggested: list[Item]) -> dict[str, Resolved]:
    """Every suggestion whose device and choice still resolve, keyed by its card's issue id.

    options is read once for this call, so a matched name is already the
    live area's own name; there is no second registry read to go stale.
    """
    device_registry = dr.async_get(hass)
    resolved: dict[str, Resolved] = {}
    for item in suggested:
        device = device_registry.async_get(str(item["registry_id"]))
        if not isinstance(device, dr.DeviceEntry):
            continue
        area_name = str(item.get("choice"))
        area_id = options.get(area_name)
        if area_id is None:
            continue
        device_name = device.name_by_user or device.name or device.model or device.manufacturer or device.id
        resolved[f"{AREA_ISSUE_PREFIX}{device.id}"] = Resolved(
            subject_id=device.id,
            confidence=confidence_of(item),
            placeholders={"device_name": device_name, "area_name": area_name},
            data={"device_id": device.id, "area_id": area_id},
        )
    return resolved


def _still_qualifies(hass: HomeAssistant, safety: SafetyRules, issue_id: str) -> bool:
    """Whether the device behind an existing card (the issue id, prefix stripped) still qualifies.

    The device id is the issue id with the prefix removed: the pinned shape
    every area card's id follows.
    """
    device = dr.async_get(hass).async_get(issue_id.removeprefix(AREA_ISSUE_PREFIX))
    return isinstance(device, dr.DeviceEntry) and not safety.excludes_device(hass, device)


def sync_area_cards(hass: HomeAssistant, safety: SafetyRules, suggested: list[Item], *, restoring: bool = False) -> None:
    """Create or update a capped, rejection-preserving set of area suggestion cards."""
    options = area_options(hass)
    resolved = _resolve(hass, options, suggested)
    still_qualifies = functools.partial(_still_qualifies, hass, safety)
    sync_suggestion_cards(
        hass,
        AREA_ISSUE_PREFIX,
        ISSUE_AREA_SUGGESTION,
        MAX_NEW_AREA_CARDS_PER_RUN,
        resolved,
        suggested,
        still_qualifies,
        restoring=restoring,
    )
