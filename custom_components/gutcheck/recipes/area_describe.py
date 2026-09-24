"""Area options from the area registry, and the model-visible shape of one device."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from ..const import AREA_NONE_DESCRIPTION, OPTION_NONE
from .shapes import Item


def area_options(hass: HomeAssistant) -> dict[str, str]:
    """Every area's name mapped to its area id, in area id order."""
    areas = sorted(ar.async_get(hass).async_list_areas(), key=lambda area: area.id)
    return {area.name: area.id for area in areas}


def area_criteria(options: dict[str, str]) -> dict[str, str | None]:
    """The choice criteria for an area question: every area name, plus none of these."""
    criteria: dict[str, str | None] = dict.fromkeys(options, None)
    criteria[OPTION_NONE] = AREA_NONE_DESCRIPTION
    return criteria


def describe(hass: HomeAssistant, device: dr.DeviceEntry) -> tuple[dict[str, Any], Item]:
    """One device's model-visible state and its code-only subject.

    Built from this device and its own entities only: never the parent
    (via_device) device, another device, or the deprecated suggested-area
    property.
    """
    name = device.name_by_user or device.name
    entries = er.async_entries_for_device(er.async_get(hass), device.id, include_disabled_entities=True)
    entity_domains = sorted({entry.domain for entry in entries})
    classes = {entry.device_class or entry.original_device_class for entry in entries}
    device_classes = sorted({device_class for device_class in classes if device_class})
    integration = None
    if device.config_entry_id:
        entry = hass.config_entries.async_get_entry(device.config_entry_id)
        integration = entry.domain if entry is not None else None
    state_item: dict[str, Any] = {
        "name": name,
        "manufacturer": device.manufacturer,
        "model": device.model,
        "integration": integration,
        "entity_domains": entity_domains,
        "device_classes": device_classes,
    }
    subject: Item = {"registry_id": device.id, "device_name": name}
    return state_item, subject
