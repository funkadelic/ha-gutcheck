"""Timeline test: rejecting an area suggestion through Home Assistant's own repairs flow manager."""

from __future__ import annotations

from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import AREA_ISSUE_PREFIX, DOMAIN, RECIPE_AREAS

from .conftest import api_response, area_answer, create_areas, posted_bodies, register_area_device, register_jev_responses


def _area_button_entity_id(hass: HomeAssistant, entry: MockConfigEntry) -> str | None:
    """The area recipe's Run button entity id, or None if it was not created."""
    return er.async_get(hass).async_get_entity_id("button", DOMAIN, f"{entry.entry_id}_{RECIPE_AREAS}_run")


async def _press(hass: HomeAssistant, entity_id: str) -> None:
    """Press the button and let its background run finish."""
    await hass.services.async_call("button", "press", {"entity_id": entity_id}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_ignoring_a_card_through_the_flow_manager_survives_a_rerun(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """Choosing don't-suggest through the flow manager leaves the card ignored and the device unplaced across a rerun."""
    areas = create_areas(hass, "Kitchen", "Garage")
    device = register_area_device(hass, "plug", name="Kitchen Plug", entities=["sensor"])
    register_jev_responses(aioclient_mock, [api_response({"d0": area_answer("Kitchen", 0.9, list(areas))})])
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    issue_id = f"{AREA_ISSUE_PREFIX}{device.id}"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.is_fixable is True

    assert await async_setup_component(hass, "repairs", {})
    manager = repairs_flow_manager(hass)
    assert manager is not None
    result = await manager.async_init(DOMAIN, data={"issue_id": issue_id})
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "init"
    assert result["menu_options"] == ["confirm", "ignore"]
    assert result["description_placeholders"] == issue.translation_placeholders

    result = await manager.async_configure(result["flow_id"], {"next_step_id": "ignore"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "suggestion_ignored"

    ignored_issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert ignored_issue is not None
    assert ignored_issue.dismissed_version is not None
    updated_device = dr.async_get(hass).async_get(device.id)
    assert updated_device is not None
    assert updated_device.area_id is None

    aioclient_mock.clear_requests()
    register_jev_responses(aioclient_mock, [api_response({"d0": area_answer("Kitchen", 0.9, list(areas))})])
    button_entity_id = _area_button_entity_id(hass, mock_config_entry)
    assert button_entity_id is not None
    await _press(hass, button_entity_id)

    bodies = posted_bodies(aioclient_mock)
    assert len(bodies) == 1

    reran_issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert reran_issue is not None
    assert reran_issue.dismissed_version is not None
    updated_device = dr.async_get(hass).async_get(device.id)
    assert updated_device is not None
    assert updated_device.area_id is None
