"""The cleaned name and device fields an entity-level recipe sends to the model."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from ..const import DEVICE_TEXT_MAX_CHARS
from ..describe import clean_text


def _clean(text: str | None) -> str | None:
    """Cleaned text, or None when nothing is left."""
    return clean_text(text, DEVICE_TEXT_MAX_CHARS) or None


def entity_text(hass: HomeAssistant, entry: er.RegistryEntry) -> dict[str, str | None]:
    """Name, device name, manufacturer and model, each cleaned and None when empty or without a device."""
    device = dr.async_get(hass).async_get(entry.device_id) if entry.device_id else None
    device = device if isinstance(device, dr.DeviceEntry) else None
    return {
        "name": _clean(entry.name or entry.original_name),
        "device_name": _clean(device.name_by_user or device.name) if device else None,
        "manufacturer": _clean(device.manufacturer) if device else None,
        "model": _clean(device.model) if device else None,
    }
