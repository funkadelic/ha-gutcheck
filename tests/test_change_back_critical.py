"""A critical label saved with the change-back stops a rejection card being made for either kind."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import label_registry as lr
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import (
    CONF_CRITICAL_LABEL,
    CONF_UNDO_HIDDEN_SENSORS,
    CONF_UNDO_SENSORS,
    DEVICE_CLASS_ISSUE_PREFIX,
    HIDE_DIAGNOSTIC_ISSUE_PREFIX,
)

from .test_hide_diagnostic_undo_rules import BOTH_ON, _both_kinds, _open_step


async def test_no_rejection_card_is_ever_created_for_a_sensor_labelled_critical_in_the_same_save(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """The reload afterwards would sweep such a card, so watch the registry for a create instead of the end state."""
    classed, hidden = await _both_kinds(hass, aioclient_mock, device_class_entry)
    label = lr.async_get(hass).async_create("Critical")
    registry = er.async_get(hass)
    for sensor in (classed, hidden):
        registry.async_update_entity(sensor.entity_id, labels={label.label_id})
    events = async_capture_events(hass, ir.EVENT_REPAIRS_ISSUE_REGISTRY_UPDATED)

    result = await _open_step(hass, device_class_entry, {**BOTH_ON, CONF_CRITICAL_LABEL: label.label_id})
    await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_UNDO_SENSORS: [classed.id], CONF_UNDO_HIDDEN_SENSORS: [hidden.id]}
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    created = {event.data["issue_id"] for event in events if event.data["action"] == "create"}
    assert f"{DEVICE_CLASS_ISSUE_PREFIX}{classed.id}" not in created
    assert f"{HIDE_DIAGNOSTIC_ISSUE_PREFIX}{hidden.id}" not in created
