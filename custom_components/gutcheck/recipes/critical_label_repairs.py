"""The one critical label write: adding it to an entity on confirm, and nowhere else."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .critical_label_describe import configured_label, qualifying_entry
from .safety import SafetyRules
from .shapes import IssueData


def add_critical_label(hass: HomeAssistant, safety: SafetyRules, data: IssueData) -> bool:
    """Re-check the entity still qualifies and the label is still the one configured, then add it.

    Returns False and writes nothing when either id is missing or malformed,
    no label is configured or the configured label no longer exists, the
    configured label's id differs from the card's own (the configured label
    changed since the card was raised), or the entity no longer qualifies
    (removed, disabled, now blocked, or already carrying the label on itself
    or its device). Otherwise the entity's labels are updated to the union of
    its current labels and the label id: this only ever adds this one label
    to this one entity, never to its device, and never takes another label
    out of the set.
    """
    registry_id = data.get("registry_id")
    label_id = data.get("label_id")
    if not isinstance(registry_id, str) or not isinstance(label_id, str):
        return False
    label = configured_label(hass, safety)
    if label is None or label.label_id != label_id:
        return False
    entry = qualifying_entry(hass, safety, registry_id)
    if entry is None:
        return False
    er.async_get(hass).async_update_entity(entry.entity_id, labels=entry.labels | {label_id})
    return True
