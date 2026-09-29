"""The Configure change-back for sensors Gut Check hid."""

from __future__ import annotations

import logging
from collections.abc import Collection
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .device_class_undo import async_undo_records
from .hide_diagnostic_cards import reject_suggestion
from .safety import SafetyRules

if TYPE_CHECKING:
    from .. import GutCheckConfigEntry

_LOGGER = logging.getLogger(__name__)


async def async_change_back_hidden(
    hass: HomeAssistant, entry: GutCheckConfigEntry, registry_ids: Collection[str], critical_label: str | None
) -> None:
    """Unhide each picked sensor the user still has hidden, record a rejection, then forget it.

    A sensor hidden by its integration is never touched.
    """
    registry = er.async_get(hass)
    safety = SafetyRules(critical_label)
    cleared, left = await async_undo_records(
        entry.runtime_data.applied_hidden,
        registry,
        registry_ids,
        still_ours=lambda registry_entry, _recorded: registry_entry.hidden_by == er.RegistryEntryHider.USER,
        clear=lambda registry_entry: registry.async_update_entity(registry_entry.entity_id, hidden_by=None),
        reject=lambda registry_id, _recorded: reject_suggestion(hass, safety, registry_id),
    )
    _LOGGER.debug("hidden sensor change-back cleared=%s left=%s", cleared, left)
