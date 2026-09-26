"""The one device class write: on confirm or the user's pick, and nowhere else."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import SelectOptionDict

from ..const import DOMAIN
from .device_class_describe import candidate_classes, class_names, qualifying_entry
from .safety import SafetyRules
from .shapes import IssueData


async def other_class_choices(hass: HomeAssistant, safety: SafetyRules, data: IssueData) -> list[SelectOptionDict]:
    """The live unit's other fitting classes as select options, labelled by Home Assistant's own class names.

    Empty when either id is missing or malformed, or the sensor no longer
    qualifies; the suggested class itself is never offered.
    """
    registry_id = data.get("registry_id")
    device_class = data.get("device_class")
    if not isinstance(registry_id, str) or not isinstance(device_class, str):
        return []
    entry = qualifying_entry(hass, safety, registry_id)
    if entry is None:
        return []
    names = await class_names(hass)
    return [
        SelectOptionDict(value=cls, label=names.get(cls, cls))
        for cls in candidate_classes(entry.unit_of_measurement)
        if cls != device_class
    ]


def set_device_class(hass: HomeAssistant, safety: SafetyRules, data: IssueData) -> bool:
    """Re-check the sensor still qualifies and the class still fits its live unit, then set it and record it.

    The class comes from data: the card's suggestion, or the class the user
    picked instead. Returns False and writes nothing when either id is
    missing or malformed, the sensor no longer qualifies (already classed,
    disabled, critical, or removed), the class no longer accepts the
    sensor's live unit, or no Gut Check entry is loaded to record the write
    against.
    """
    registry_id = data.get("registry_id")
    device_class = data.get("device_class")
    if not isinstance(registry_id, str) or not isinstance(device_class, str):
        return False
    entry = qualifying_entry(hass, safety, registry_id)
    loaded_entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if entry is None or device_class not in candidate_classes(entry.unit_of_measurement) or not loaded_entries:
        return False
    er.async_get(hass).async_update_entity(entry.entity_id, device_class=device_class)
    loaded_entries[0].runtime_data.applied.record(registry_id, device_class)
    return True
