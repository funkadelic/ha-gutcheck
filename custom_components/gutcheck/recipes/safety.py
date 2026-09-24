"""The exclusions every recipe applies, whatever else it selects on."""

from __future__ import annotations

from homeassistant.const import Platform
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

    def excludes_device(self, hass: HomeAssistant, device: dr.DeviceEntry) -> bool:
        """Whether this device is out of the area recipe's reach.

        Service devices, disabled devices, devices already in an area, and
        anything carrying the critical label on the device itself are
        excluded first. A device with no entities, a device_tracker entity,
        an entity already placed in its own area, or an entity carrying the
        critical label (disabled ones included) is excluded too. Lock, alarm
        panel and cover entities do not exclude a device: an area is
        registry metadata the user confirms by hand, not control of it.
        """
        if device.entry_type is not None:
            return True
        if device.disabled_by is not None:
            return True
        if device.area_id is not None:
            return True
        if self._critical_label and self._critical_label in device.labels:
            return True
        entries = er.async_entries_for_device(er.async_get(hass), device.id, include_disabled_entities=True)
        if not entries:
            return True
        return any(
            entry.domain == Platform.DEVICE_TRACKER
            or entry.area_id is not None
            or (self._critical_label is not None and self._critical_label in entry.labels)
            for entry in entries
        )

    def excludes_entity_id(self, hass: HomeAssistant, entity_id_or_uuid: str) -> bool:
        """The same rules for a stored finding, which may no longer be registered.

        Takes either form the registry accepts. Prefer the registry id, which
        outlives a rename.
        """
        entry = er.async_get(hass).async_get(entity_id_or_uuid)
        if entry is None:
            # Unknown now (removed). Left alone, so an ignore survives it.
            return False
        return self.excludes(hass, entry)
