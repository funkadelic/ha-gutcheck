"""The cleaned name and device fields an entity-level recipe sends to the model."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from ..describe import clean_name


def entity_text(hass: HomeAssistant, entry: er.RegistryEntry) -> dict[str, str | None]:
    """Name, device name, manufacturer and model, each cleaned and None when empty or without a device."""
    device = dr.async_get(hass).async_get(entry.device_id) if entry.device_id else None
    device = device if isinstance(device, dr.DeviceEntry) else None
    return {
        "name": clean_name(entry.name or entry.original_name),
        "device_name": clean_name(device.name_by_user or device.name) if device else None,
        "manufacturer": clean_name(device.manufacturer) if device else None,
        "model": clean_name(device.model) if device else None,
    }
