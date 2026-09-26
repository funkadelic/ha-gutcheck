"""The one area write: assigning a suggested area to a device, on confirm only."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr

from .safety import SafetyRules
from .shapes import IssueData


def assign_area(hass: HomeAssistant, safety: SafetyRules, data: IssueData) -> bool:
    """Re-check the device still exists and still qualifies, the area still exists, then assign it.

    Returns False and writes nothing when either id is missing or malformed,
    the device no longer qualifies (critical label included), or the area no
    longer exists.
    """
    device_id = data.get("device_id")
    area_id = data.get("area_id")
    if not isinstance(device_id, str) or not isinstance(area_id, str):
        return False
    device_registry = dr.async_get(hass)
    device = device_registry.async_get(device_id)
    if not isinstance(device, dr.DeviceEntry) or safety.excludes_device(hass, device):
        return False
    if ar.async_get(hass).async_get_area(area_id) is None:
        return False
    device_registry.async_update_device(device_id, area_id=area_id)
    return True
