"""Timeline tests: rejecting a device class suggestion through Home Assistant's own repairs flow manager."""

from __future__ import annotations

from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import DEVICE_CLASS_ISSUE_PREFIX, DOMAIN, RECIPE_DEVICE_CLASS

from .conftest import (
    api_response,
    area_answer,
    device_class_sensor_entity_id,
    posted_bodies,
    register_jev_responses,
    register_unit_sensor,
)

BATTERY_CANDIDATES = ["battery", "humidity", "moisture", "power_factor"]


def _button_entity_id(hass: HomeAssistant, entry: MockConfigEntry) -> str | None:
    """The device class recipe's Run button entity id, or None if it was not created."""
    return er.async_get(hass).async_get_entity_id("button", DOMAIN, f"{entry.entry_id}_{RECIPE_DEVICE_CLASS}_run")


async def _press(hass: HomeAssistant, entity_id: str) -> None:
    """Press the button and let its background run finish."""
    await hass.services.async_call("button", "press", {"entity_id": entity_id}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_ignoring_a_card_through_the_flow_manager_survives_a_rerun(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """Choosing don't-suggest through the flow manager leaves the card ignored and the sensor unset across a rerun."""
    sensor = register_unit_sensor(hass, "battery_pct", unit="%", name="Battery")
    register_jev_responses(aioclient_mock, [api_response({"s0": area_answer("battery", 0.9, BATTERY_CANDIDATES)})])
    device_class_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(device_class_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    issue_id = f"{DEVICE_CLASS_ISSUE_PREFIX}{sensor.id}"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.is_fixable is True

    assert await async_setup_component(hass, "repairs", {})
    manager = repairs_flow_manager(hass)
    assert manager is not None
    result = await manager.async_init(DOMAIN, data={"issue_id": issue_id})
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "init"
    assert result["menu_options"] == ["confirm", "choose", "ignore"]
    assert result["description_placeholders"] == issue.translation_placeholders

    result = await manager.async_configure(result["flow_id"], {"next_step_id": "ignore"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "suggestion_ignored"

    ignored_issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert ignored_issue is not None
    assert ignored_issue.dismissed_version is not None
    updated = er.async_get(hass).async_get(sensor.entity_id)
    assert updated is not None
    assert updated.device_class is None
    state = hass.states.get(device_class_sensor_entity_id(hass, device_class_entry))
    assert state is not None
    assert state.state == "0"

    aioclient_mock.clear_requests()
    register_jev_responses(aioclient_mock, [api_response({"s0": area_answer("battery", 0.9, BATTERY_CANDIDATES)})])
    button_entity_id = _button_entity_id(hass, device_class_entry)
    assert button_entity_id is not None
    await _press(hass, button_entity_id)

    bodies = posted_bodies(aioclient_mock)
    assert len(bodies) == 1
    reran_issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert reran_issue is not None
    assert reran_issue.dismissed_version is not None
    updated = er.async_get(hass).async_get(sensor.entity_id)
    assert updated is not None
    assert updated.device_class is None
    state = hass.states.get(device_class_sensor_entity_id(hass, device_class_entry))
    assert state is not None
    assert state.state == "0"
