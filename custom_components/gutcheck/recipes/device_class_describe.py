"""Unit narrowing from Home Assistant's own map, the qualifying check, and one sensor's model-visible shape."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor.const import DEVICE_CLASS_UNITS
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.translation import async_get_translations

from ..const import (
    DEVICE_CLASS_NAME_KEY,
    DEVICE_CLASS_NAMES_LANGUAGE,
    DEVICE_CLASS_NONE_DESCRIPTION,
    DEVICE_TEXT_MAX_CHARS,
    OPTION_NONE,
)
from ..describe import clean_text
from .safety import SafetyRules
from .shapes import Item

# Every SensorDeviceClass value Home Assistant itself accepts a unit for.
KNOWN_CLASSES: tuple[str, ...] = tuple(sorted(device_class.value for device_class in DEVICE_CLASS_UNITS))


def candidate_classes(unit: str | None) -> tuple[str, ...]:
    """Every device class whose allowed-unit set contains this exact unit string.

    A None unit always narrows to nothing: DEVICE_CLASS_UNITS lists None as a
    valid unit for aqi, ph and power_factor, and a unitless sensor must never
    match them.
    """
    if unit is None:
        return ()
    return tuple(sorted(device_class.value for device_class, units in DEVICE_CLASS_UNITS.items() if unit in units))


def qualifies(hass: HomeAssistant, safety: SafetyRules, entry: er.RegistryEntry) -> bool:
    """Whether this registry entry is a sensor this recipe may consider.

    A sensor, not excluded by the shared safety rules, with a unit and no
    effective device class (its own override or the integration's own).
    """
    return (
        entry.domain == Platform.SENSOR
        and not safety.excludes(hass, entry)
        and entry.unit_of_measurement is not None
        and (entry.device_class or entry.original_device_class) is None
    )


def qualifying_entry(hass: HomeAssistant, safety: SafetyRules, registry_id: str) -> er.RegistryEntry | None:
    """The registry entry for registry_id, only when it still exists and still qualifies."""
    entry = er.async_get(hass).async_get(registry_id)
    if entry is None or not qualifies(hass, safety, entry):
        return None
    return entry


async def class_names(hass: HomeAssistant) -> dict[str, str]:
    """Every known device class mapped to Home Assistant's own translated name for it."""
    translations = await async_get_translations(hass, DEVICE_CLASS_NAMES_LANGUAGE, "entity_component", ["sensor"])
    return {
        device_class: translations.get(DEVICE_CLASS_NAME_KEY.format(device_class=device_class), device_class)
        for device_class in KNOWN_CLASSES
    }


def criteria(candidates: tuple[str, ...], names: dict[str, str]) -> dict[str, str | None]:
    """The choice criteria for a device class question: each candidate's name, plus none of these."""
    criteria_map: dict[str, str | None] = {candidate: names.get(candidate, candidate) for candidate in candidates}
    criteria_map[OPTION_NONE] = DEVICE_CLASS_NONE_DESCRIPTION
    return criteria_map


def describe(hass: HomeAssistant, entry: er.RegistryEntry) -> tuple[dict[str, Any], Item]:
    """One sensor's model-visible state and its code-only subject.

    Built from this entry and its own device only: never the sensor's live
    state, its entity id, its area, or another entity.
    """
    name = clean_text(entry.name or entry.original_name, DEVICE_TEXT_MAX_CHARS) or None
    device = dr.async_get(hass).async_get(entry.device_id) if entry.device_id else None
    device = device if isinstance(device, dr.DeviceEntry) else None
    device_name = clean_text(device.name_by_user or device.name, DEVICE_TEXT_MAX_CHARS) or None if device else None
    manufacturer = clean_text(device.manufacturer, DEVICE_TEXT_MAX_CHARS) or None if device else None
    model = clean_text(device.model, DEVICE_TEXT_MAX_CHARS) or None if device else None
    state_item: dict[str, Any] = {
        "name": name,
        "device_name": device_name,
        "manufacturer": manufacturer,
        "model": model,
        "integration": entry.platform,
        "unit": entry.unit_of_measurement,
        "entity_category": entry.entity_category.value if entry.entity_category else None,
    }
    subject: Item = {"registry_id": entry.id, "entity_id": entry.entity_id}
    return state_item, subject
