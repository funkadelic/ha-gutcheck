"""The confirm/ignore fix flow shared by every suggestion recipe, and the platform hook Home Assistant loads.

This module, and both apply modules it wires together (area_repairs and
device_class_repairs), must not import the integration's own repairs module
or any card-sync module: repairs.py re-exports async_create_fix_flow from
here, and an import back the other way would be a cycle.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from ..const import CONF_CRITICAL_LABEL, DEVICE_CLASS_ISSUE_PREFIX, DOMAIN
from .area_repairs import assign_area
from .device_class_repairs import set_device_class
from .safety import SafetyRules
from .shapes import IssueData

_LOGGER = logging.getLogger(__name__)

Apply = Callable[[HomeAssistant, SafetyRules, IssueData], bool]


class SuggestionRepairFlow(RepairsFlow):
    """A menu of two choices: confirm the suggestion, or ignore the card for good."""

    def __init__(self, data: IssueData | None, apply: Apply) -> None:
        """Hold the card's own data and the callable that acts on a confirm."""
        self._data: IssueData = data or {}
        self._apply = apply

    async def async_step_init(self, user_input: dict[str, str] | None = None) -> RepairsFlowResult:
        """Show the confirm/ignore menu, with the card's own placeholders."""
        issue = ir.async_get(self.hass).async_get_issue(self.handler, self.issue_id)
        placeholders = issue.translation_placeholders if issue is not None else None
        return self.async_show_menu(step_id="init", menu_options=["confirm", "ignore"], description_placeholders=placeholders)

    async def async_step_confirm(self, user_input: dict[str, str] | None = None) -> RepairsFlowResult:
        """Apply the suggestion, or remove the card and abort as outdated if the re-check fails."""
        if not self._apply(self.hass, self._safety(), self._data):
            _LOGGER.debug("suggestion outdated")
            ir.async_delete_issue(self.hass, DOMAIN, self.issue_id)
            return self.async_abort(reason="suggestion_outdated")
        _LOGGER.debug("suggestion confirmed")
        return self.async_create_entry(data={})

    async def async_step_ignore(self, user_input: dict[str, str] | None = None) -> RepairsFlowResult:
        """Ignore the card, or abort as outdated if a run already swept it out from under the open dialog."""
        if ir.async_get(self.hass).async_get_issue(DOMAIN, self.issue_id) is None:
            _LOGGER.debug("suggestion outdated")
            return self.async_abort(reason="suggestion_outdated")
        ir.async_ignore_issue(self.hass, DOMAIN, self.issue_id, True)
        _LOGGER.debug("suggestion ignored")
        return self.async_abort(reason="suggestion_ignored")

    def _safety(self) -> SafetyRules:
        """The safety rules under the critical label configured now, not when the card was raised."""
        entries = self.hass.config_entries.async_entries(DOMAIN)
        return SafetyRules(entries[0].options.get(CONF_CRITICAL_LABEL) if entries else None)


async def async_create_fix_flow(hass: HomeAssistant, issue_id: str, data: IssueData | None) -> RepairsFlow:
    """Build the confirm/ignore flow for a suggestion card, routed by its issue id prefix.

    Every other Gut Check issue is is_fixable=False and never reaches here.
    """
    apply = set_device_class if issue_id.startswith(DEVICE_CLASS_ISSUE_PREFIX) else assign_area
    return SuggestionRepairFlow(data, apply)
