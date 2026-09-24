"""Tests for the fix flow's stale-state guards: each aborts, and none of them changes a device."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, UnknownStep
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component

from custom_components.gutcheck.const import AREA_ISSUE_PREFIX, DOMAIN, ISSUE_AREA_SUGGESTION
from custom_components.gutcheck.recipes.area_cards import sync_area_cards
from custom_components.gutcheck.recipes.safety import SafetyRules

from .conftest import create_areas, register_area_device


async def _open_and_confirm(hass: HomeAssistant, issue_id: str) -> dict[str, Any]:
    """Load gutcheck and repairs, open the fix flow for issue_id, and choose confirm from its menu.

    The repairs platform loader only finds gutcheck's fix flow for a loaded
    integration; without this, every case here would silently pass through
    Home Assistant's own no-op ConfirmRepairFlow instead of the one under test.
    """
    assert await async_setup_component(hass, DOMAIN, {})
    assert await async_setup_component(hass, "repairs", {})
    manager = repairs_flow_manager(hass)
    assert manager is not None
    result = await manager.async_init(DOMAIN, data={"issue_id": issue_id})
    assert result["type"] is FlowResultType.MENU
    return await manager.async_configure(result["flow_id"], {"next_step_id": "confirm"})


async def test_a_removed_device_aborts_and_changes_nothing(hass: HomeAssistant) -> None:
    """Confirming a card for a device removed since it was raised aborts as outdated."""
    create_areas(hass, "Kitchen")
    device = register_area_device(hass, "plug", entities=["sensor"])
    issue_id = f"{AREA_ISSUE_PREFIX}{device.id}"
    sync_area_cards(hass, SafetyRules(None), [{"registry_id": device.id, "choice": "Kitchen"}])
    dr.async_get(hass).async_remove_device(device.id)

    result = await _open_and_confirm(hass, issue_id)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "suggestion_outdated"


async def test_a_device_given_an_area_by_hand_aborts_and_keeps_that_area(hass: HomeAssistant) -> None:
    """Confirming a card for a device manually placed since it was raised aborts without overwriting the manual area."""
    areas = create_areas(hass, "Kitchen", "Garage")
    device = register_area_device(hass, "plug", entities=["sensor"])
    issue_id = f"{AREA_ISSUE_PREFIX}{device.id}"
    sync_area_cards(hass, SafetyRules(None), [{"registry_id": device.id, "choice": "Kitchen"}])
    dr.async_get(hass).async_update_device(device.id, area_id=areas["Garage"])

    result = await _open_and_confirm(hass, issue_id)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "suggestion_outdated"
    updated = dr.async_get(hass).async_get(device.id)
    assert updated is not None
    assert updated.area_id == areas["Garage"]


async def test_a_deleted_suggested_area_aborts_and_changes_nothing(hass: HomeAssistant) -> None:
    """Confirming a card whose suggested area was deleted since it was raised aborts."""
    areas = create_areas(hass, "Kitchen")
    device = register_area_device(hass, "plug", entities=["sensor"])
    issue_id = f"{AREA_ISSUE_PREFIX}{device.id}"
    sync_area_cards(hass, SafetyRules(None), [{"registry_id": device.id, "choice": "Kitchen"}])
    ar.async_get(hass).async_delete(areas["Kitchen"])

    result = await _open_and_confirm(hass, issue_id)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "suggestion_outdated"
    updated = dr.async_get(hass).async_get(device.id)
    assert updated is not None
    assert updated.area_id is None


@pytest.mark.parametrize("missing_key", ["device_id", "area_id"])
async def test_issue_data_missing_either_id_aborts_and_changes_nothing(hass: HomeAssistant, missing_key: str) -> None:
    """A card whose data is missing the device id or the area id aborts rather than raising."""
    areas = create_areas(hass, "Kitchen")
    device = register_area_device(hass, "plug", entities=["sensor"])
    data: dict[str, Any] = {"device_id": device.id, "area_id": areas["Kitchen"]}
    del data[missing_key]
    issue_id = f"{AREA_ISSUE_PREFIX}malformed"
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_AREA_SUGGESTION,
        translation_placeholders={"device_name": "x", "area_name": "y"},
        data=data,
    )

    result = await _open_and_confirm(hass, issue_id)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "suggestion_outdated"
    updated = dr.async_get(hass).async_get(device.id)
    assert updated is not None
    assert updated.area_id is None


async def test_a_successful_confirm_makes_a_second_flow_for_the_same_issue_raise_unknown_step(hass: HomeAssistant) -> None:
    """Once a confirm succeeds the card is gone, and a second flow for the same issue id cannot start."""
    create_areas(hass, "Kitchen")
    device = register_area_device(hass, "plug", entities=["sensor"])
    issue_id = f"{AREA_ISSUE_PREFIX}{device.id}"
    sync_area_cards(hass, SafetyRules(None), [{"registry_id": device.id, "choice": "Kitchen"}])

    result = await _open_and_confirm(hass, issue_id)
    assert result["type"] is FlowResultType.CREATE_ENTRY

    manager = repairs_flow_manager(hass)
    assert manager is not None
    with pytest.raises(UnknownStep):
        await manager.async_init(DOMAIN, data={"issue_id": issue_id})
