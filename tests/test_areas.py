"""Tracer test: a device with no area gets a suggested-area card, and confirming it assigns the area."""

from __future__ import annotations

import logging

import pytest
from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import AREA_ISSUE_PREFIX, DOMAIN, OPTION_NONE
from custom_components.gutcheck.recipes.area_cards import sync_area_cards
from custom_components.gutcheck.recipes.safety import SafetyRules

from .conftest import (
    api_response,
    area_answer,
    areas_sensor_entity_id,
    create_areas,
    posted_bodies,
    register_area_device,
    register_jev_responses,
)


async def test_confident_answer_raises_a_card_and_confirm_assigns_the_area(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One device, one confident answer: one card, and confirming it through Home Assistant's own flow manager writes the area."""
    areas = create_areas(hass, "Kitchen", "Garage")
    device = register_area_device(hass, "plug", name="Kitchen Plug", entities=["sensor"])
    register_jev_responses(aioclient_mock, [api_response({"d0": area_answer("Kitchen", 0.9, list(areas))})])
    mock_config_entry.add_to_hass(hass)

    with caplog.at_level(logging.DEBUG):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)

    bodies = posted_bodies(aioclient_mock)
    assert len(bodies) == 1
    body = bodies[0]
    assert len(body["state"]["devices"]) == 1
    question = body["questions"]["d0"]
    assert set(question["criteria"].keys()) == {"Kitchen", "Garage", OPTION_NONE}

    state = hass.states.get(areas_sensor_entity_id(hass, mock_config_entry))
    assert state is not None
    assert state.state == "1"
    suggested = state.attributes["items"]["suggested"]
    assert len(suggested) == 1
    item = suggested[0]
    assert item["registry_id"] == device.id
    assert item["device_name"] == "Kitchen Plug"
    assert item["confidence"] == 0.9
    assert item["choice"] == "Kitchen"

    issue_id = f"{AREA_ISSUE_PREFIX}{device.id}"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.is_fixable is True
    assert issue.data == {"device_id": device.id, "area_id": areas["Kitchen"]}
    assert issue.translation_placeholders is not None
    assert issue.translation_placeholders["device_name"] == "Kitchen Plug"
    assert issue.translation_placeholders["area_name"] == "Kitchen"

    assert await async_setup_component(hass, "repairs", {})
    manager = repairs_flow_manager(hass)
    assert manager is not None
    result = await manager.async_init(DOMAIN, data={"issue_id": issue_id})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"
    assert result["description_placeholders"] == issue.translation_placeholders

    result = await manager.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY

    updated_device = dr.async_get(hass).async_get(device.id)
    assert updated_device is not None
    assert updated_device.area_id == areas["Kitchen"]
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    assert "Error processing repairs platform" not in caplog.text


async def test_a_confident_none_of_these_leaves_the_device_unsure_with_no_card(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """A confident "none of these" lands in unsure, never in the suggested bucket."""
    areas = create_areas(hass, "Kitchen", "Garage")
    register_area_device(hass, "plug", name="Kitchen Plug", entities=["sensor"])
    register_jev_responses(aioclient_mock, [api_response({"d0": area_answer(OPTION_NONE, 0.9, list(areas))})])
    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    state = hass.states.get(areas_sensor_entity_id(hass, mock_config_entry))
    assert state is not None
    assert state.attributes["items"]["suggested"] == []
    assert len(state.attributes["unsure"]) == 1
    assert not any(issue_id.startswith(AREA_ISSUE_PREFIX) for _domain, issue_id in ir.async_get(hass).issues)


async def test_a_low_confidence_kitchen_answer_leaves_the_device_unsure_with_no_card(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """A Kitchen answer below the confidence threshold lands in unsure, never in the suggested bucket."""
    areas = create_areas(hass, "Kitchen", "Garage")
    register_area_device(hass, "plug", name="Kitchen Plug", entities=["sensor"])
    register_jev_responses(aioclient_mock, [api_response({"d0": area_answer("Kitchen", 0.3, list(areas))})])
    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    state = hass.states.get(areas_sensor_entity_id(hass, mock_config_entry))
    assert state is not None
    assert state.attributes["items"]["suggested"] == []
    assert len(state.attributes["unsure"]) == 1
    assert not any(issue_id.startswith(AREA_ISSUE_PREFIX) for _domain, issue_id in ir.async_get(hass).issues)


async def test_sync_skips_an_item_whose_device_no_longer_resolves(hass: HomeAssistant) -> None:
    """An item naming a registry id that no longer resolves to a device raises no card."""
    create_areas(hass, "Kitchen")

    sync_area_cards(hass, SafetyRules(None), [{"registry_id": "gone", "choice": "Kitchen"}])

    assert not any(issue_id.startswith(AREA_ISSUE_PREFIX) for _domain, issue_id in ir.async_get(hass).issues)


async def test_sync_skips_an_item_whose_choice_no_longer_resolves_to_a_live_area(hass: HomeAssistant) -> None:
    """An item naming a choice that is no longer a live area name raises no card."""
    create_areas(hass, "Kitchen")
    device = register_area_device(hass, "plug", entities=["sensor"])

    sync_area_cards(hass, SafetyRules(None), [{"registry_id": device.id, "choice": "Attic"}])

    assert not any(issue_id.startswith(AREA_ISSUE_PREFIX) for _domain, issue_id in ir.async_get(hass).issues)


async def test_with_no_areas_at_all_setup_sends_no_request(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """With no areas at all, there is nothing to suggest and no request is sent."""
    register_area_device(hass, "plug", name="Kitchen Plug", entities=["sensor"])
    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert posted_bodies(aioclient_mock) == []
    state = hass.states.get(areas_sensor_entity_id(hass, mock_config_entry))
    assert state is not None
    assert state.state == "0"
