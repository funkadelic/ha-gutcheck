"""The confirm/ignore fix flow shared by every suggestion recipe, and the platform hook Home Assistant loads.

This module, and both apply modules it wires together (area_repairs and
device_class_repairs), must not import the integration's own repairs module
or any card-sync module: repairs.py re-exports async_create_fix_flow from
here, and an import back the other way would be a cycle.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.selector import SelectSelector, SelectSelectorConfig

from ..const import CONF_CRITICAL_LABEL, DEVICE_CLASS_ISSUE_PREFIX, DOMAIN
from .area_repairs import assign_area
from .device_class_repairs import other_class_choices, set_device_class
from .safety import SafetyRules
from .shapes import IssueData

_LOGGER = logging.getLogger(__name__)

Apply = Callable[[HomeAssistant, SafetyRules, IssueData], bool]


class SuggestionRepairFlow(RepairsFlow):
    """A menu built by _menu_options (confirm, ignore, and a subclass's own steps), acting through one apply callable."""

    def __init__(self, data: IssueData | None, apply: Apply) -> None:
        """Hold the card's data and the callable that acts on a confirm or a pick."""
        self._data: IssueData = data or {}
        self._apply = apply

    async def async_step_init(self, user_input: dict[str, str] | None = None) -> RepairsFlowResult:
        """Show this flow's menu, with the card's placeholders."""
        return self.async_show_menu(
            step_id="init", menu_options=await self._menu_options(), description_placeholders=self._placeholders()
        )

    async def async_step_confirm(self, user_input: dict[str, str] | None = None) -> RepairsFlowResult:
        """Apply the suggestion as raised."""
        return self._finish(self._data)

    async def async_step_ignore(self, user_input: dict[str, str] | None = None) -> RepairsFlowResult:
        """Ignore the card, or abort as outdated if a run already swept it out from under the open dialog."""
        if ir.async_get(self.hass).async_get_issue(DOMAIN, self.issue_id) is None:
            return self._outdated()
        ir.async_ignore_issue(self.hass, DOMAIN, self.issue_id, True)
        _LOGGER.debug("suggestion ignored")
        return self.async_abort(reason="suggestion_ignored")

    async def _menu_options(self) -> list[str]:
        """The init menu's options; a subclass adds steps to this list."""
        return ["confirm", "ignore"]

    def _finish(self, data: IssueData) -> RepairsFlowResult:
        """Apply this data, or remove the card and abort as outdated if the re-check fails."""
        if not self._apply(self.hass, self._safety(), data):
            return self._outdated()
        _LOGGER.debug("suggestion confirmed")
        return self.async_create_entry(data={})

    def _outdated(self) -> RepairsFlowResult:
        """Remove the card and abort as outdated."""
        _LOGGER.debug("suggestion outdated")
        ir.async_delete_issue(self.hass, DOMAIN, self.issue_id)
        return self.async_abort(reason="suggestion_outdated")

    def _placeholders(self) -> Mapping[str, str] | None:
        """The card's translation placeholders, read fresh from the issue registry."""
        issue = ir.async_get(self.hass).async_get_issue(self.handler, self.issue_id)
        return issue.translation_placeholders if issue is not None else None

    def _safety(self) -> SafetyRules:
        """The safety rules under the critical label configured now, not when the card was raised."""
        entries = self.hass.config_entries.async_entries(DOMAIN)
        return SafetyRules(entries[0].options.get(CONF_CRITICAL_LABEL) if entries else None)


class DeviceClassRepairFlow(SuggestionRepairFlow):
    """The device class flow's extra step: pick a different class the sensor's live unit still fits."""

    def __init__(self, data: IssueData | None) -> None:
        """Hold the card's data; set_device_class is the only apply this flow ever uses."""
        super().__init__(data, set_device_class)

    async def _menu_options(self) -> list[str]:
        """Confirm, choose, ignore when another class still fits the sensor's live unit; confirm, ignore otherwise."""
        choices = await other_class_choices(self.hass, self._safety(), self._data)
        return ["confirm", "choose", "ignore"] if choices else ["confirm", "ignore"]

    async def async_step_choose(self, user_input: dict[str, str] | None = None) -> RepairsFlowResult:
        """Apply a submitted pick, or show the picker for a class the unit still fits."""
        if user_input is not None:
            return self._finish({**self._data, "device_class": user_input["device_class"]})
        choices = await other_class_choices(self.hass, self._safety(), self._data)
        if not choices:
            return self._outdated()
        schema = vol.Schema({vol.Required("device_class"): SelectSelector(SelectSelectorConfig(options=choices))})
        return self.async_show_form(step_id="choose", data_schema=schema, description_placeholders=self._placeholders())


async def async_create_fix_flow(hass: HomeAssistant, issue_id: str, data: IssueData | None) -> RepairsFlow:
    """Build the fix flow for a suggestion card, routed by its issue id prefix.

    Every other Gut Check issue is is_fixable=False and never reaches here.
    """
    if issue_id.startswith(DEVICE_CLASS_ISSUE_PREFIX):
        return DeviceClassRepairFlow(data)
    return SuggestionRepairFlow(data, assign_area)
