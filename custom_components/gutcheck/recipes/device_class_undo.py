"""The record of every device class Gut Check sets, and the Configure change-back."""

from __future__ import annotations

import logging
from collections.abc import Collection
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .applied_records import async_undo_records
from .device_class_cards import reject_suggestion
from .device_class_describe import class_names
from .safety import SafetyRules

if TYPE_CHECKING:
    from .. import GutCheckConfigEntry

_LOGGER = logging.getLogger(__name__)


async def async_change_back(
    hass: HomeAssistant, entry: GutCheckConfigEntry, registry_ids: Collection[str], critical_label: str | None
) -> None:
    """Clear a class Gut Check set for each picked sensor still holding it, record it as a rejection, then forget it."""
    registry = er.async_get(hass)
    safety = SafetyRules(critical_label)
    names = await class_names(hass)
    cleared, left = await async_undo_records(
        entry.runtime_data.applied,
        registry,
        registry_ids,
        still_ours=lambda registry_entry, recorded: registry_entry.device_class == recorded,
        clear=lambda registry_entry: registry.async_update_entity(registry_entry.entity_id, device_class=None),
        reject=lambda registry_id, recorded: reject_suggestion(hass, safety, names, registry_id, recorded),
    )
    _LOGGER.debug("device class change-back cleared=%s left=%s", cleared, left)
