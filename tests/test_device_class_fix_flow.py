"""Tests for the device class fix flow's stale-state guards: each aborts, and none of them writes a class."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, UnknownStep
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import label_registry as lr
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import CONF_CRITICAL_LABEL, DEVICE_CLASS_ISSUE_PREFIX, DOMAIN, ISSUE_DEVICE_CLASS_SUGGESTION
from custom_components.gutcheck.recipes.device_class_cards import sync_device_class_cards
from custom_components.gutcheck.recipes.safety import SafetyRules

from .conftest import register_unit_sensor, seed_device_class_card


async def _setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Add and set up the entry, with nothing registered yet, so the first run posts nothing."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)


async def _open_menu(hass: HomeAssistant, issue_id: str) -> dict[str, Any]:
    """Start the fix flow for issue_id and return its menu result."""
    assert await async_setup_component(hass, "repairs", {})
    manager = repairs_flow_manager(hass)
    assert manager is not None
    result = await manager.async_init(DOMAIN, data={"issue_id": issue_id})
    assert result["type"] is FlowResultType.MENU
    return result


async def _open_and_confirm(hass: HomeAssistant, issue_id: str) -> dict[str, Any]:
    """Open the fix flow for issue_id and choose confirm from its menu."""
    result = await _open_menu(hass, issue_id)
    manager = repairs_flow_manager(hass)
    assert manager is not None
    return await manager.async_configure(result["flow_id"], {"next_step_id": "confirm"})


async def test_confirming_an_unchanged_sensor_sets_the_class(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """The control case: nothing changed since the card was raised, so confirm sets the class."""
    sensor, issue_id = await seed_device_class_card(hass, device_class_entry)

    result = await _open_and_confirm(hass, issue_id)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    updated = er.async_get(hass).async_get(sensor.entity_id)
    assert updated is not None
    assert updated.device_class == "battery"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_a_removed_sensor_aborts_and_changes_nothing(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """Confirming a card for a sensor removed since it was raised aborts as outdated."""
    sensor, issue_id = await seed_device_class_card(hass, device_class_entry)
    er.async_get(hass).async_remove(sensor.entity_id)

    result = await _open_and_confirm(hass, issue_id)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "suggestion_outdated"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_a_disabled_sensor_aborts_and_changes_nothing(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """Confirming a card for a sensor disabled since it was raised aborts as outdated."""
    sensor, issue_id = await seed_device_class_card(hass, device_class_entry)
    er.async_get(hass).async_update_entity(sensor.entity_id, disabled_by=er.RegistryEntryDisabler.USER)

    result = await _open_and_confirm(hass, issue_id)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "suggestion_outdated"
    updated = er.async_get(hass).async_get(sensor.entity_id)
    assert updated is not None
    assert updated.device_class is None


async def test_a_sensor_given_a_class_by_hand_aborts_and_keeps_that_class(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """Confirming a card for a sensor manually classed since it was raised aborts without overwriting it."""
    sensor, issue_id = await seed_device_class_card(hass, device_class_entry)
    er.async_get(hass).async_update_entity(sensor.entity_id, device_class="humidity")

    result = await _open_and_confirm(hass, issue_id)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "suggestion_outdated"
    updated = er.async_get(hass).async_get(sensor.entity_id)
    assert updated is not None
    assert updated.device_class == "humidity"


async def test_a_sensor_given_an_original_class_by_its_integration_aborts_and_keeps_it(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """Confirming a card for a sensor whose integration set original_device_class since it was raised aborts."""
    sensor, issue_id = await seed_device_class_card(hass, device_class_entry)
    er.async_get(hass).async_update_entity(sensor.entity_id, original_device_class="moisture")

    result = await _open_and_confirm(hass, issue_id)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "suggestion_outdated"
    updated = er.async_get(hass).async_get(sensor.entity_id)
    assert updated is not None
    assert updated.device_class is None
    assert updated.original_device_class == "moisture"


async def test_a_sensor_moved_to_a_unit_the_suggested_class_does_not_accept_aborts(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """Confirming a card for a sensor moved to a unit that no longer accepts the suggested class aborts."""
    sensor, issue_id = await seed_device_class_card(hass, device_class_entry)
    er.async_get(hass).async_update_entity(sensor.entity_id, unit_of_measurement="°C")

    result = await _open_and_confirm(hass, issue_id)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "suggestion_outdated"
    updated = er.async_get(hass).async_get(sensor.entity_id)
    assert updated is not None
    assert updated.device_class is None


async def test_a_sensor_labelled_critical_aborts_and_changes_nothing(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """Confirming a card for a sensor labelled critical since it was raised aborts; the flow reads the label configured now."""
    await _setup_entry(hass, device_class_entry)
    label = lr.async_get(hass).async_create("Critical")
    sensor = register_unit_sensor(hass, "battery_pct", unit="%", name="Battery")
    issue_id = f"{DEVICE_CLASS_ISSUE_PREFIX}{sensor.id}"
    sync_device_class_cards(hass, SafetyRules(None), [{"registry_id": sensor.id, "choice": "battery"}], {"battery": "Battery"})
    er.async_get(hass).async_update_entity(sensor.entity_id, labels={label.label_id})
    hass.config_entries.async_update_entry(
        device_class_entry, options={**device_class_entry.options, CONF_CRITICAL_LABEL: label.label_id}
    )

    result = await _open_and_confirm(hass, issue_id)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "suggestion_outdated"
    updated = er.async_get(hass).async_get(sensor.entity_id)
    assert updated is not None
    assert updated.device_class is None


async def test_a_sensors_device_labelled_critical_aborts_and_changes_nothing(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """Confirming a card for a sensor whose device was labelled critical since it was raised aborts."""
    await _setup_entry(hass, device_class_entry)
    label = lr.async_get(hass).async_create("Critical")
    sensor = register_unit_sensor(hass, "battery_pct", unit="%", name="Battery", device_name="Kitchen Sensor")
    issue_id = f"{DEVICE_CLASS_ISSUE_PREFIX}{sensor.id}"
    sync_device_class_cards(hass, SafetyRules(None), [{"registry_id": sensor.id, "choice": "battery"}], {"battery": "Battery"})
    assert sensor.device_id is not None
    dr.async_get(hass).async_update_device(sensor.device_id, labels={label.label_id})
    hass.config_entries.async_update_entry(
        device_class_entry, options={**device_class_entry.options, CONF_CRITICAL_LABEL: label.label_id}
    )

    result = await _open_and_confirm(hass, issue_id)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "suggestion_outdated"
    updated = er.async_get(hass).async_get(sensor.entity_id)
    assert updated is not None
    assert updated.device_class is None


@pytest.mark.parametrize("missing_key", ["registry_id", "device_class"])
async def test_issue_data_missing_either_key_aborts_and_changes_nothing(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry, missing_key: str
) -> None:
    """A card whose data is missing the registry id or the device class aborts rather than raising."""
    await _setup_entry(hass, device_class_entry)
    sensor = register_unit_sensor(hass, "battery_pct", unit="%", name="Battery")
    data: dict[str, Any] = {"registry_id": sensor.id, "device_class": "battery"}
    del data[missing_key]
    issue_id = f"{DEVICE_CLASS_ISSUE_PREFIX}malformed_{missing_key}"
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_DEVICE_CLASS_SUGGESTION,
        translation_placeholders={"entity_id": sensor.entity_id, "class_name": "Battery", "unit": "%"},
        data=data,
    )

    result = await _open_and_confirm(hass, issue_id)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "suggestion_outdated"
    updated = er.async_get(hass).async_get(sensor.entity_id)
    assert updated is not None
    assert updated.device_class is None


async def test_issue_data_with_a_non_str_device_class_aborts(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """A card whose data holds a non-str device class aborts rather than raising."""
    await _setup_entry(hass, device_class_entry)
    sensor = register_unit_sensor(hass, "battery_pct", unit="%", name="Battery")
    issue_id = f"{DEVICE_CLASS_ISSUE_PREFIX}malformed_type"
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_DEVICE_CLASS_SUGGESTION,
        translation_placeholders={"entity_id": sensor.entity_id, "class_name": "Battery", "unit": "%"},
        data={"registry_id": sensor.id, "device_class": 5},
    )

    result = await _open_and_confirm(hass, issue_id)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "suggestion_outdated"
    updated = er.async_get(hass).async_get(sensor.entity_id)
    assert updated is not None
    assert updated.device_class is None


async def test_issue_data_naming_a_class_the_live_unit_does_not_accept_aborts(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """A card naming a class the sensor's live unit does not accept aborts rather than writing it."""
    await _setup_entry(hass, device_class_entry)
    sensor = register_unit_sensor(hass, "battery_pct", unit="%", name="Battery")
    issue_id = f"{DEVICE_CLASS_ISSUE_PREFIX}malformed_class"
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_DEVICE_CLASS_SUGGESTION,
        translation_placeholders={"entity_id": sensor.entity_id, "class_name": "Temperature", "unit": "%"},
        data={"registry_id": sensor.id, "device_class": "temperature"},
    )

    result = await _open_and_confirm(hass, issue_id)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "suggestion_outdated"
    updated = er.async_get(hass).async_get(sensor.entity_id)
    assert updated is not None
    assert updated.device_class is None


async def test_a_successful_confirm_makes_a_second_flow_for_the_same_issue_raise_unknown_step(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """Once a confirm succeeds the card is gone, and a second flow for the same issue id cannot start."""
    _sensor, issue_id = await seed_device_class_card(hass, device_class_entry)

    result = await _open_and_confirm(hass, issue_id)
    assert result["type"] is FlowResultType.CREATE_ENTRY

    manager = repairs_flow_manager(hass)
    assert manager is not None
    with pytest.raises(UnknownStep):
        await manager.async_init(DOMAIN, data={"issue_id": issue_id})


async def test_confirm_aborts_when_a_run_swept_the_card_while_the_dialog_was_open(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """A run deleting the card between opening the menu and submitting confirm aborts as outdated."""
    sensor, issue_id = await seed_device_class_card(hass, device_class_entry)
    menu = await _open_menu(hass, issue_id)
    ir.async_delete_issue(hass, DOMAIN, issue_id)

    manager = repairs_flow_manager(hass)
    assert manager is not None
    result = await manager.async_configure(menu["flow_id"], {"next_step_id": "confirm"})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "suggestion_outdated"
    updated = er.async_get(hass).async_get(sensor.entity_id)
    assert updated is not None
    assert updated.device_class is None


async def test_choose_aborts_when_a_run_swept_the_card_while_the_dialog_was_open(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """A run deleting the card between opening choose and submitting a pick aborts as outdated."""
    sensor, issue_id = await seed_device_class_card(hass, device_class_entry)
    menu = await _open_menu(hass, issue_id)
    manager = repairs_flow_manager(hass)
    assert manager is not None
    form = await manager.async_configure(menu["flow_id"], {"next_step_id": "choose"})
    assert form["type"] is FlowResultType.FORM
    ir.async_delete_issue(hass, DOMAIN, issue_id)

    result = await manager.async_configure(form["flow_id"], {"device_class": "humidity"})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "suggestion_outdated"
    updated = er.async_get(hass).async_get(sensor.entity_id)
    assert updated is not None
    assert updated.device_class is None
