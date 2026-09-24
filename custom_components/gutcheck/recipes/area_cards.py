"""Fixable Repairs card sync for suggested area items."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from ..const import AREA_ISSUE_PREFIX, ISSUE_AREA_SUGGESTION
from ..repairs import async_sync_issues
from .area_describe import area_options
from .shapes import Item

_LOGGER = logging.getLogger(__name__)


def sync_area_cards(hass: HomeAssistant, suggested: list[Item]) -> None:
    """Create or update one fixable card per still-resolvable suggestion, and clear the rest.

    An item whose device or whose choice no longer resolves to a live area is
    skipped rather than raised: it is stale and the next run replaces it.
    options is read once for this call, so a matched name is already the
    live area's own name; there is no second registry read to go stale.
    """
    device_registry = dr.async_get(hass)
    options = area_options(hass)

    wanted: dict[str, dict[str, str]] = {}
    data: dict[str, dict[str, str | int | float | None]] = {}
    for item in suggested:
        device = device_registry.async_get(str(item["registry_id"]))
        if not isinstance(device, dr.DeviceEntry):
            continue
        area_name = str(item.get("choice"))
        area_id = options.get(area_name)
        if area_id is None:
            continue
        issue_id = f"{AREA_ISSUE_PREFIX}{device.id}"
        device_name = device.name_by_user or device.name or device.model or device.manufacturer or device.id
        wanted[issue_id] = {"device_name": device_name, "area_name": area_name}
        data[issue_id] = {"device_id": device.id, "area_id": area_id}

    async_sync_issues(hass, AREA_ISSUE_PREFIX, ISSUE_AREA_SUGGESTION, wanted, is_fixable=True, issue_data=data)
    _LOGGER.debug("area card sync suggested=%s cards=%s", len(suggested), len(wanted))
