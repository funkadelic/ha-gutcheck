"""Selection, the configured label lookup, and one entity's model-visible shape."""

from __future__ import annotations

from typing import Any

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr

from ..const import DEVICE_TEXT_MAX_CHARS
from ..describe import clean_text
from .critical_label_const import ASKED_DOMAINS, CODE_DECIDED_DEVICE_CLASSES
from .safety import SafetyRules
from .shapes import Item


def decided_in_code(entry: er.RegistryEntry) -> bool:
    """Whether this binary sensor's effective device class settles it with no question.

    Effective means the user's own override wins over the integration's
    original device class, so a sensor changed away from smoke, carbon
    monoxide, gas or moisture by hand is not decided in code.
    """
    if entry.domain != Platform.BINARY_SENSOR:
        return False
    return (entry.device_class or entry.original_device_class) in CODE_DECIDED_DEVICE_CLASSES


def qualifies(hass: HomeAssistant, safety: SafetyRules, entry: er.RegistryEntry) -> bool:
    """Whether this registry entry is a candidate this recipe may consider or re-check.

    Selection reads only domain and effective device class, never name or
    brand. The cheap domain test runs before the shared exclusions, which
    already drop disabled entities, Gut Check's own, locks, alarm panels,
    covers, and anything carrying the critical label on the entity or its
    device.
    """
    if entry.domain == Platform.BINARY_SENSOR:
        if not decided_in_code(entry):
            return False
    elif entry.domain not in ASKED_DOMAINS:
        return False
    return not safety.excludes(hass, entry)


def qualifying_entry(hass: HomeAssistant, safety: SafetyRules, registry_id: str) -> er.RegistryEntry | None:
    """The registry entry for registry_id, only when it still exists and still qualifies."""
    entry = er.async_get(hass).async_get(registry_id)
    if entry is None or not qualifies(hass, safety, entry):
        return None
    return entry


def configured_label(hass: HomeAssistant, safety: SafetyRules) -> lr.LabelEntry | None:
    """The configured critical label's registry entry, or None when unset or no longer registered."""
    if not safety.critical_label:
        return None
    return lr.async_get(hass).async_get_label(safety.critical_label)


def describe(hass: HomeAssistant, entry: er.RegistryEntry) -> tuple[dict[str, Any], Item]:
    """One entity's model-visible state and its code-only subject.

    Built from this entry and its device only: never the entity's live
    state, its entity id, its area, or its labels.
    """
    name = clean_text(entry.name or entry.original_name, DEVICE_TEXT_MAX_CHARS) or None
    device = dr.async_get(hass).async_get(entry.device_id) if entry.device_id else None
    device = device if isinstance(device, dr.DeviceEntry) else None
    device_name = clean_text(device.name_by_user or device.name, DEVICE_TEXT_MAX_CHARS) or None if device else None
    manufacturer = clean_text(device.manufacturer, DEVICE_TEXT_MAX_CHARS) or None if device else None
    model = clean_text(device.model, DEVICE_TEXT_MAX_CHARS) or None if device else None
    state_item: dict[str, Any] = {
        "domain": entry.domain,
        "device_class": entry.device_class or entry.original_device_class,
        "name": name,
        "device_name": device_name,
        "manufacturer": manufacturer,
        "model": model,
        "integration": entry.platform,
        "entity_category": entry.entity_category.value if entry.entity_category else None,
    }
    subject: Item = {"registry_id": entry.id, "entity_id": entry.entity_id}
    return state_item, subject
