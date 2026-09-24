"""Area options from the area registry, and the model-visible shape of one device."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from ..const import AREA_NAME_MAX_CHARS, AREA_NONE_DESCRIPTION, DEVICE_TEXT_MAX_CHARS, OPTION_NONE
from ..describe import clean_text
from .shapes import Item


def area_options(hass: HomeAssistant) -> dict[str, str]:
    """Every area's cleaned, capped name mapped to its area id, in area id order.

    Walking areas lowest id first and skipping an already-taken name is what
    keeps the lowest area id holding a contested name: a plain dict
    comprehension would let a later, higher id silently overwrite it. A
    cleaned name that is empty or equals the none-of-these key is left out.
    """
    areas = sorted(ar.async_get(hass).async_list_areas(), key=lambda area: area.id)
    options: dict[str, str] = {}
    for area in areas:
        name = clean_text(area.name, AREA_NAME_MAX_CHARS)
        if not name or name == OPTION_NONE or name in options:
            continue
        options[name] = area.id
    return options


def area_criteria(options: dict[str, str]) -> dict[str, str | None]:
    """The choice criteria for an area question: every area name, plus none of these."""
    criteria: dict[str, str | None] = dict.fromkeys(options, None)
    criteria[OPTION_NONE] = AREA_NONE_DESCRIPTION
    return criteria


def describe(hass: HomeAssistant, device: dr.DeviceEntry) -> tuple[dict[str, Any], Item]:
    """One device's model-visible state and its code-only subject.

    Built from this device and its own entities only: never the linking
    parent device, another device, or the deprecated suggested-area property.
    """
    name = clean_text(device.name_by_user or device.name, DEVICE_TEXT_MAX_CHARS) or None
    manufacturer = clean_text(device.manufacturer, DEVICE_TEXT_MAX_CHARS) or None
    model = clean_text(device.model, DEVICE_TEXT_MAX_CHARS) or None
    entries = er.async_entries_for_device(er.async_get(hass), device.id, include_disabled_entities=True)
    entity_domains = sorted({entry.domain for entry in entries})
    classes = {entry.device_class or entry.original_device_class for entry in entries}
    device_classes = sorted({device_class for device_class in classes if device_class})
    # A device belongs to exactly one config entry, and the registry refuses
    # to create one whose config_entry_id does not resolve, so this is
    # always found while the device itself is (device_registry.py at 2026.9.2).
    owning_entry = hass.config_entries.async_get_entry(device.config_entry_id)
    assert owning_entry is not None
    integration = owning_entry.domain
    state_item: dict[str, Any] = {
        "name": name,
        "manufacturer": manufacturer,
        "model": model,
        "integration": integration,
        "entity_domains": entity_domains,
        "device_classes": device_classes,
    }
    subject: Item = {"registry_id": device.id, "device_name": name}
    return state_item, subject
