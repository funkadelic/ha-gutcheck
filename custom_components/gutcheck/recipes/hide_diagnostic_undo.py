"""The Configure change-back for sensors Gut Check hid."""

from __future__ import annotations

import logging
from collections.abc import Collection
from typing import TYPE_CHECKING

from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .applied_records import AppliedRecords, async_undo_records
from .hide_diagnostic_cards import reject_suggestion
from .safety import SafetyRules

if TYPE_CHECKING:
    from .. import GutCheckConfigEntry

_LOGGER = logging.getLogger(__name__)


@callback
def async_track_hidden(hass: HomeAssistant, applied: AppliedRecords) -> CALLBACK_TYPE:
    """Forget each recorded sensor that is gone or no longer hidden by the user, now and on every later change.

    A sensor the user unhides and later hides again by hand is then never
    treated as Gut Check's. Returns the unsubscribe callback.
    """
    registry = er.async_get(hass)

    @callback
    def _prune() -> None:
        """Drop every record whose sensor is gone or not hidden by the user."""
        for registry_id in applied.ids():
            registry_entry = registry.async_get(registry_id)
            if registry_entry is None or registry_entry.hidden_by != er.RegistryEntryHider.USER:
                applied.discard(registry_id)

    @callback
    def _changed(event: Event[er.EventEntityRegistryUpdatedData]) -> None:
        """Re-check the records when a sensor is removed or its hidden_by changes."""
        data = event.data
        if data["action"] == "remove" or (data["action"] == "update" and "hidden_by" in data["changes"]):
            _prune()

    _prune()
    unsubscribe: CALLBACK_TYPE = hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, _changed)
    return unsubscribe


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
