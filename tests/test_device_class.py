"""Tracer test: a sensor with a unit gets a device class card, and confirming it sets the class."""

from __future__ import annotations

import logging

import pytest
from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import (
    DEVICE_CLASS_ISSUE_PREFIX,
    DEVICE_CLASS_NONE_DESCRIPTION,
    DOMAIN,
    OPTION_NONE,
    RECIPE_DEVICE_CLASS,
)
from custom_components.gutcheck.recipes.device_class_cards import sync_device_class_cards
from custom_components.gutcheck.recipes.safety import SafetyRules

from .conftest import (
    api_response,
    area_answer,
    posted_bodies,
    recipe_sensor_entity_id,
    register_jev_responses,
    register_unit_sensor,
)

BATTERY_CANDIDATES = ["battery", "humidity", "moisture", "power_factor"]


async def test_confident_answer_raises_a_card_and_confirm_sets_the_class(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    device_class_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One sensor, one confident answer: one card, and confirming it through Home Assistant's own flow manager sets the class."""
    sensor = register_unit_sensor(hass, "battery_pct", unit="%", name="Battery", device_name="Kitchen Sensor")
    register_jev_responses(aioclient_mock, [api_response({"s0": area_answer("battery", 0.9, BATTERY_CANDIDATES)})])
    device_class_entry.add_to_hass(hass)

    with caplog.at_level(logging.DEBUG):
        assert await hass.config_entries.async_setup(device_class_entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)

    bodies = posted_bodies(aioclient_mock)
    assert len(bodies) == 1
    body = bodies[0]
    assert len(body["state"]["sensors"]) == 1
    question = body["questions"]["s0"]
    assert set(question["criteria"].keys()) == {*BATTERY_CANDIDATES, OPTION_NONE}
    assert question["criteria"]["battery"] == "Battery"
    assert question["criteria"][OPTION_NONE] == DEVICE_CLASS_NONE_DESCRIPTION

    state = hass.states.get(recipe_sensor_entity_id(hass, device_class_entry, RECIPE_DEVICE_CLASS))
    assert state is not None
    assert state.state == "1"
    suggested = state.attributes["items"]["suggested"]
    assert len(suggested) == 1
    item = suggested[0]
    assert item["registry_id"] == sensor.id
    assert item["entity_id"] == sensor.entity_id
    assert item["choice"] == "battery"
    assert item["confidence"] == 0.9

    issue_id = f"{DEVICE_CLASS_ISSUE_PREFIX}{sensor.id}"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.is_fixable is True
    assert issue.is_persistent is True
    assert issue.data == {"registry_id": sensor.id, "device_class": "battery"}
    assert issue.translation_placeholders is not None
    assert issue.translation_placeholders["entity_id"] == sensor.entity_id
    assert issue.translation_placeholders["class_name"] == "Battery"
    assert issue.translation_placeholders["unit"] == "%"

    assert await async_setup_component(hass, "repairs", {})
    manager = repairs_flow_manager(hass)
    assert manager is not None
    result = await manager.async_init(DOMAIN, data={"issue_id": issue_id})
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "init"
    assert result["description_placeholders"] == issue.translation_placeholders

    result = await manager.async_configure(result["flow_id"], {"next_step_id": "confirm"})
    assert result["type"] is FlowResultType.CREATE_ENTRY

    updated = er.async_get(hass).async_get(sensor.entity_id)
    assert updated is not None
    assert updated.device_class == "battery"
    assert updated.original_device_class is None
    assert updated.unit_of_measurement == "%"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None

    state = hass.states.get(recipe_sensor_entity_id(hass, device_class_entry, RECIPE_DEVICE_CLASS))
    assert state is not None
    assert state.state == "0"
    assert "Error processing repairs platform" not in caplog.text


async def test_a_confident_none_of_these_leaves_the_sensor_unsure_with_no_card(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """A confident "none of these" lands in unsure, never in the suggested bucket."""
    register_unit_sensor(hass, "battery_pct", unit="%", name="Battery")
    register_jev_responses(aioclient_mock, [api_response({"s0": area_answer(OPTION_NONE, 0.9, BATTERY_CANDIDATES)})])
    device_class_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(device_class_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    state = hass.states.get(recipe_sensor_entity_id(hass, device_class_entry, RECIPE_DEVICE_CLASS))
    assert state is not None
    assert state.attributes["items"]["suggested"] == []
    assert len(state.attributes["unsure"]) == 1
    assert not any(issue_id.startswith(DEVICE_CLASS_ISSUE_PREFIX) for _domain, issue_id in ir.async_get(hass).issues)


async def test_a_low_confidence_battery_answer_leaves_the_sensor_unsure_with_no_card(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """A battery answer below the confidence threshold lands in unsure, never in the suggested bucket."""
    register_unit_sensor(hass, "battery_pct", unit="%", name="Battery")
    register_jev_responses(aioclient_mock, [api_response({"s0": area_answer("battery", 0.3, BATTERY_CANDIDATES)})])
    device_class_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(device_class_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    state = hass.states.get(recipe_sensor_entity_id(hass, device_class_entry, RECIPE_DEVICE_CLASS))
    assert state is not None
    assert state.attributes["items"]["suggested"] == []
    assert len(state.attributes["unsure"]) == 1
    assert not any(issue_id.startswith(DEVICE_CLASS_ISSUE_PREFIX) for _domain, issue_id in ir.async_get(hass).issues)


async def test_sync_skips_an_item_whose_sensor_no_longer_resolves(hass: HomeAssistant) -> None:
    """An item naming a registry id that no longer resolves to a qualifying sensor raises no card."""
    sync_device_class_cards(hass, SafetyRules(None), [{"registry_id": "gone", "choice": "battery"}], {"battery": "Battery"})

    assert not any(issue_id.startswith(DEVICE_CLASS_ISSUE_PREFIX) for _domain, issue_id in ir.async_get(hass).issues)


async def test_sync_skips_an_item_whose_choice_no_longer_fits_the_live_unit(hass: HomeAssistant) -> None:
    """An item naming a class that no longer accepts the sensor's live unit raises no card."""
    sensor = register_unit_sensor(hass, "battery_pct", unit="%", name="Battery")

    sync_device_class_cards(
        hass, SafetyRules(None), [{"registry_id": sensor.id, "choice": "temperature"}], {"temperature": "Temperature"}
    )

    assert not any(issue_id.startswith(DEVICE_CLASS_ISSUE_PREFIX) for _domain, issue_id in ir.async_get(hass).issues)


async def test_a_sensor_given_a_class_by_hand_before_confirm_aborts_and_keeps_that_class(hass: HomeAssistant) -> None:
    """Confirming a card for a sensor manually classed since it was raised aborts without overwriting the manual class."""
    sensor = register_unit_sensor(hass, "battery_pct", unit="%", name="Battery")
    issue_id = f"{DEVICE_CLASS_ISSUE_PREFIX}{sensor.id}"
    sync_device_class_cards(hass, SafetyRules(None), [{"registry_id": sensor.id, "choice": "battery"}], {"battery": "Battery"})
    er.async_get(hass).async_update_entity(sensor.entity_id, device_class="humidity")

    assert await async_setup_component(hass, DOMAIN, {})
    assert await async_setup_component(hass, "repairs", {})
    manager = repairs_flow_manager(hass)
    assert manager is not None
    result = await manager.async_init(DOMAIN, data={"issue_id": issue_id})
    assert result["type"] is FlowResultType.MENU
    result = await manager.async_configure(result["flow_id"], {"next_step_id": "confirm"})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "suggestion_outdated"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    updated = er.async_get(hass).async_get(sensor.entity_id)
    assert updated is not None
    assert updated.device_class == "humidity"
