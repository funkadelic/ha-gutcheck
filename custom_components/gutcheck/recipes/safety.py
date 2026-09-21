"""The exclusions every recipe applies, whatever else it selects on."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from ..const import BLOCKED_DOMAINS, DOMAIN


class SafetyRules:
    """The entities no recipe may select, held by each recipe rather than inherited."""

    def __init__(self, critical_label: str | None) -> None:
        """Store the label that marks an entity or device as critical."""
        self._critical_label = critical_label

    def is_critical(self, hass: HomeAssistant, entry: er.RegistryEntry) -> bool:
        """Whether the configured label sits on the entity or on the device behind it."""
        if not self._critical_label:
            return False
        if self._critical_label in entry.labels:
            return True
        if entry.device_id:
            device = dr.async_get(hass).async_get(entry.device_id)
            if device and self._critical_label in device.labels:
                return True
        return False

    def excludes(self, hass: HomeAssistant, entry: er.RegistryEntry) -> bool:
        """Whether this registry entry is out of every recipe's reach."""
        if entry.disabled or entry.platform == DOMAIN or entry.domain in BLOCKED_DOMAINS:
            return True
        return self.is_critical(hass, entry)

    def excludes_entity_id(self, hass: HomeAssistant, entity_id: str) -> bool:
        """The same rules for a stored entity id, which may no longer be registered."""
        entry = er.async_get(hass).async_get(entity_id)
        if entry is None:
            # Unknown now (removed or renamed). Left alone, so an ignore survives it.
            return False
        return self.excludes(hass, entry)
