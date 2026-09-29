"""Selection and one sensor's model-visible shape for diagnostic sensor suggestions."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from ..const import DEVICE_CLASS_ISSUE_PREFIX, DEVICE_TEXT_MAX_CHARS, DOMAIN
from ..describe import clean_text
from .entity_text import entity_text
from .hide_diagnostic_const import KNOWN_STATE_CLASSES
from .safety import SafetyRules
from .shapes import Item


def _effective_device_class(entry: er.RegistryEntry) -> str | None:
    """The user's own device class override, or the integration's original one when unset."""
    return entry.device_class or entry.original_device_class


def decided_in_code(entry: er.RegistryEntry) -> bool:
    """Whether this sensor's effective device class, the user's override first, settles it as signal strength."""
    return _effective_device_class(entry) == SensorDeviceClass.SIGNAL_STRENGTH


def _has_open_device_class_card(hass: HomeAssistant, entry: er.RegistryEntry) -> bool:
    """Whether a device class card for this sensor exists and has not been ignored.

    Reads the issue registry directly: importing repairs.py from here would
    close an import cycle through the fix flow.
    """
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"{DEVICE_CLASS_ISSUE_PREFIX}{entry.id}")
    return issue is not None and issue.dismissed_version is None


def qualifies(hass: HomeAssistant, safety: SafetyRules, entry: er.RegistryEntry, *, ignore_overlap: bool = False) -> bool:
    """Whether this registry entry is a candidate this recipe may consider or re-check.

    Selection reads only domain, effective device class, entity_category and
    hidden_by, never name or brand. The cheap tests run before the shared
    exclusions, which walk the registries. An open device class card holds a
    sensor back from new suggestions; ignore_overlap=True skips that test for
    the two callers that must never delete a rejection the user already made
    (keeping an ignored card, and the change-back).
    """
    if (
        entry.domain != Platform.SENSOR
        or entry.entity_category is not None
        or entry.hidden_by is not None
        or _effective_device_class(entry) not in (None, SensorDeviceClass.SIGNAL_STRENGTH)
    ):
        return False
    if safety.excludes(hass, entry):
        return False
    return ignore_overlap or not _has_open_device_class_card(hass, entry)


def qualifying_entry(
    hass: HomeAssistant, safety: SafetyRules, registry_id: str, *, ignore_overlap: bool = False
) -> er.RegistryEntry | None:
    """The registry entry for registry_id, only when it still exists and still qualifies."""
    entry = er.async_get(hass).async_get(registry_id)
    if entry is None or not qualifies(hass, safety, entry, ignore_overlap=ignore_overlap):
        return None
    return entry


def describe(hass: HomeAssistant, entry: er.RegistryEntry) -> tuple[dict[str, Any], Item]:
    """One sensor's model-visible state and its code-only subject.

    Built from this entry and its device only: never the entity's live
    state, its entity id, its area, or its labels.
    """
    state_class = (entry.capabilities or {}).get("state_class")
    state_item: dict[str, Any] = {
        **entity_text(hass, entry),
        "integration": entry.platform,
        "unit": clean_text(entry.unit_of_measurement, DEVICE_TEXT_MAX_CHARS) or None,
        "state_class": state_class if state_class in KNOWN_STATE_CLASSES else None,
    }
    subject: Item = {"registry_id": entry.id, "entity_id": entry.entity_id}
    return state_item, subject
