"""The confirm-only fix flow for a suggested area, and the platform hook Home Assistant loads.

This module must not import the integration's own repairs module or any
recipe module that does: repairs.py re-exports async_create_fix_flow from
here, and an import back the other way would be a cycle. safety.py imports
only const, so it is safe.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir

from ..const import CONF_CRITICAL_LABEL, DOMAIN
from .safety import SafetyRules

_LOGGER = logging.getLogger(__name__)

IssueData = Mapping[str, str | int | float | None]


class AreaSuggestionRepairFlow(RepairsFlow):
    """Confirm only: submit assigns the suggested area. Ignore on the card is the rejection (D-07)."""

    def __init__(self, data: IssueData | None) -> None:
        """Hold the device and area ids the card was raised for, each kept only if it is a string."""
        payload = data or {}
        device_id = payload.get("device_id")
        area_id = payload.get("area_id")
        self._device_id = device_id if isinstance(device_id, str) else None
        self._area_id = area_id if isinstance(area_id, str) else None

    async def async_step_init(self, user_input: dict[str, str] | None = None) -> RepairsFlowResult:
        """Go straight to the confirm step; this flow has no earlier step."""
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, str] | None = None) -> RepairsFlowResult:
        """Show the confirm form, then assign the area on submit."""
        if user_input is not None:
            if not self._assign():
                _LOGGER.debug("area suggestion outdated")
                return self.async_abort(reason="suggestion_outdated")
            _LOGGER.debug("area suggestion confirmed")
            return self.async_create_entry(data={})

        issue = ir.async_get(self.hass).async_get_issue(self.handler, self.issue_id)
        placeholders = issue.translation_placeholders if issue is not None else None
        return self.async_show_form(step_id="confirm", data_schema=vol.Schema({}), description_placeholders=placeholders)

    def _assign(self) -> bool:
        """Re-check the device still exists and still qualifies, critical label included, and the area still exists."""
        if self._device_id is None or self._area_id is None:
            return False
        device_registry = dr.async_get(self.hass)
        device = device_registry.async_get(self._device_id)
        if not isinstance(device, dr.DeviceEntry) or self._safety().excludes_device(self.hass, device):
            return False
        if ar.async_get(self.hass).async_get_area(self._area_id) is None:
            return False
        device_registry.async_update_device(self._device_id, area_id=self._area_id)
        return True

    def _safety(self) -> SafetyRules:
        """The safety rules under the critical label configured now, not when the card was raised."""
        entries = self.hass.config_entries.async_entries(DOMAIN)
        return SafetyRules(entries[0].options.get(CONF_CRITICAL_LABEL) if entries else None)


async def async_create_fix_flow(hass: HomeAssistant, issue_id: str, data: IssueData | None) -> RepairsFlow:
    """Build the confirm-only flow for a suggested-area card.

    Every other Gut Check issue is is_fixable=False and never reaches here.
    """
    return AreaSuggestionRepairFlow(data)
