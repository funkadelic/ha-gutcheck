"""The one hidden_by write: hiding a sensor on confirm, and nowhere else."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from ..const import DOMAIN
from .hide_diagnostic_describe import qualifying_entry
from .safety import SafetyRules
from .shapes import IssueData


def hide_entity(hass: HomeAssistant, safety: SafetyRules, data: IssueData) -> bool:
    """Re-check the sensor still qualifies, then hide it and record it.

    Returns False and writes nothing when the registry id is missing or
    malformed, the sensor no longer qualifies (removed, disabled, categorised,
    already hidden, given a device class, or now critical), or no Gut Check
    entry is loaded to record the write against. An open device class card
    does not block it, because the user already chose to hide. The write sets
    hidden_by to the user and touches no other registry field.
    """
    registry_id = data.get("registry_id")
    if not isinstance(registry_id, str):
        return False
    entry = qualifying_entry(hass, safety, registry_id, ignore_overlap=True)
    loaded_entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if entry is None or not loaded_entries:
        return False
    er.async_get(hass).async_update_entity(entry.entity_id, hidden_by=er.RegistryEntryHider.USER)
    loaded_entries[0].runtime_data.applied_hidden.record(registry_id, er.RegistryEntryHider.USER.value)
    return True
