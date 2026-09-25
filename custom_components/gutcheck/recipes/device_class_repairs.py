"""The one device class write: setting a sensor's registry override, on confirm only."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .device_class_describe import candidate_classes, qualifying_entry
from .safety import SafetyRules
from .shapes import IssueData


def set_device_class(hass: HomeAssistant, safety: SafetyRules, data: IssueData) -> bool:
    """Re-check the sensor still qualifies and the class still fits its live unit, then set it.

    Returns False and writes nothing when either id is missing or malformed,
    the sensor no longer qualifies (already classed, disabled, critical, or
    removed), or the class no longer accepts the sensor's live unit.
    """
    registry_id = data.get("registry_id")
    device_class = data.get("device_class")
    if not isinstance(registry_id, str) or not isinstance(device_class, str):
        return False
    entry = qualifying_entry(hass, safety, registry_id)
    if entry is None or device_class not in candidate_classes(entry.unit_of_measurement):
        return False
    er.async_get(hass).async_update_entity(entry.entity_id, device_class=device_class)
    return True
