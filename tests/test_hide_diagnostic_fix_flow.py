"""The confirm and ignore steps of a diagnostic sensor card, through Home Assistant's repairs flow manager."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import label_registry as lr
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import (
    CONF_CRITICAL_LABEL,
    DOMAIN,
    HIDE_DIAGNOSTIC_ISSUE_PREFIX,
    ISSUE_HIDE_DIAGNOSTIC_SUGGESTION,
    RECIPE_HIDE_DIAGNOSTIC,
)
from custom_components.gutcheck.recipes.device_class_cards import sync_device_class_cards
from custom_components.gutcheck.recipes.safety import SafetyRules

from .conftest import (
    api_response,
    hide_diagnostic_answer,
    recipe_sensor_entity_id,
    register_jev_responses_by_question,
    register_unit_sensor,
)


async def _raise_card(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry, unit: str | None = None
) -> tuple[er.RegistryEntry, str]:
    """Set up entry with a confident diagnostic answer for one sensor and return it with its card's issue id."""
    sensor = register_unit_sensor(hass, "phone_wifi", unit=unit, name="Wi-Fi connection")
    register_jev_responses_by_question(aioclient_mock, {"h0": api_response({"h0": hide_diagnostic_answer("diagnostic", 0.9)})})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    issue_id = f"{HIDE_DIAGNOSTIC_ISSUE_PREFIX}{sensor.id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    return sensor, issue_id


async def _choose(hass: HomeAssistant, issue_id: str, step: str) -> dict[str, Any]:
    """Open the card's fix flow, check its menu, and choose the given step."""
    assert await async_setup_component(hass, "repairs", {})
    manager = repairs_flow_manager(hass)
    assert manager is not None
    result = await manager.async_init(DOMAIN, data={"issue_id": issue_id})
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["confirm", "ignore"]
    return await manager.async_configure(result["flow_id"], {"next_step_id": step})


def _hidden_by(hass: HomeAssistant, sensor: er.RegistryEntry) -> er.RegistryEntryHider | None:
    """The sensor's live hidden_by, None when it is gone."""
    entry = er.async_get(hass).async_get(sensor.entity_id)
    return entry.hidden_by if entry is not None else None


def _remove(hass: HomeAssistant, sensor: er.RegistryEntry, entry: MockConfigEntry) -> None:
    """Remove the sensor from the registry."""
    er.async_get(hass).async_remove(sensor.entity_id)


def _disable(hass: HomeAssistant, sensor: er.RegistryEntry, entry: MockConfigEntry) -> None:
    """Disable the sensor."""
    er.async_get(hass).async_update_entity(sensor.entity_id, disabled_by=er.RegistryEntryDisabler.USER)


def _hide_by_integration(hass: HomeAssistant, sensor: er.RegistryEntry, entry: MockConfigEntry) -> None:
    """Have the owning integration hide the sensor."""
    er.async_get(hass).async_update_entity(sensor.entity_id, hidden_by=er.RegistryEntryHider.INTEGRATION)


def _categorise(hass: HomeAssistant, sensor: er.RegistryEntry, entry: MockConfigEntry) -> None:
    """Give the sensor the diagnostic entity category."""
    er.async_get(hass).async_update_entity(sensor.entity_id, entity_category=er.EntityCategory.DIAGNOSTIC)


def _give_temperature_class(hass: HomeAssistant, sensor: er.RegistryEntry, entry: MockConfigEntry) -> None:
    """Give the sensor a temperature device class override."""
    er.async_get(hass).async_update_entity(sensor.entity_id, device_class="temperature")


def _label_critical(hass: HomeAssistant, sensor: er.RegistryEntry, entry: MockConfigEntry) -> None:
    """Label the sensor with the configured critical label."""
    label = lr.async_get(hass).async_create("Critical")
    er.async_get(hass).async_update_entity(sensor.entity_id, labels={label.label_id})
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_CRITICAL_LABEL: label.label_id})


def _open_device_class_card(hass: HomeAssistant, sensor: er.RegistryEntry, entry: MockConfigEntry) -> None:
    """Raise an open device class card for the sensor."""
    sync_device_class_cards(hass, SafetyRules(None), [{"registry_id": sensor.id, "choice": "battery"}], {"battery": "Battery"})


@pytest.mark.parametrize(
    "change",
    [_remove, _disable, _hide_by_integration, _categorise, _give_temperature_class, _label_critical, _open_device_class_card],
)
async def test_confirm_after_the_sensor_changed_aborts_as_outdated_and_writes_nothing(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hide_diagnostic_entry: MockConfigEntry,
    change: Callable[[HomeAssistant, er.RegistryEntry, MockConfigEntry], None],
) -> None:
    """Every way the sensor can stop qualifying aborts the confirm, removes the card and leaves hidden_by as it was."""
    unit = "%" if change is _open_device_class_card else None
    sensor, issue_id = await _raise_card(hass, aioclient_mock, hide_diagnostic_entry, unit)
    change(hass, sensor, hide_diagnostic_entry)

    result = await _choose(hass, issue_id, "confirm")

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "suggestion_outdated"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    untouched = er.RegistryEntryHider.INTEGRATION if change is _hide_by_integration else None
    assert _hidden_by(hass, sensor) is untouched


async def test_confirm_aborts_when_card_data_lacks_the_registry_id(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hide_diagnostic_entry: MockConfigEntry
) -> None:
    """A card whose data holds no registry id aborts as outdated rather than raising."""
    sensor, _issue_id = await _raise_card(hass, aioclient_mock, hide_diagnostic_entry)
    issue_id = f"{HIDE_DIAGNOSTIC_ISSUE_PREFIX}malformed"
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_HIDE_DIAGNOSTIC_SUGGESTION,
        translation_placeholders={"entity_id": sensor.entity_id},
        data={},
    )

    result = await _choose(hass, issue_id, "confirm")

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "suggestion_outdated"
    assert _hidden_by(hass, sensor) is None


async def test_a_good_confirm_changes_only_hidden_by(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hide_diagnostic_entry: MockConfigEntry
) -> None:
    """The control case: device class, category, labels, name, area and disabled_by are untouched."""
    area = ar.async_get(hass).async_create("Hall")
    sensor, issue_id = await _raise_card(hass, aioclient_mock, hide_diagnostic_entry)
    registry = er.async_get(hass)
    registry.async_update_entity(sensor.entity_id, area_id=area.id, labels={"lab"}, name="Phone Wi-Fi")
    fields = ("device_class", "entity_category", "labels", "name", "area_id", "disabled_by")
    before = registry.async_get(sensor.entity_id)
    assert before is not None

    result = await _choose(hass, issue_id, "confirm")

    assert result["type"] is FlowResultType.CREATE_ENTRY
    after = registry.async_get(sensor.entity_id)
    assert after is not None
    assert after.hidden_by is er.RegistryEntryHider.USER
    assert [getattr(after, field) for field in fields] == [getattr(before, field) for field in fields]


async def test_ignore_aborts_as_suggestion_ignored_and_drops_the_sensor_count(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hide_diagnostic_entry: MockConfigEntry
) -> None:
    """Choosing ignore leaves the sensor visible and the recipe sensor drops by one."""
    sensor, issue_id = await _raise_card(hass, aioclient_mock, hide_diagnostic_entry)
    sensor_id = recipe_sensor_entity_id(hass, hide_diagnostic_entry, RECIPE_HIDE_DIAGNOSTIC)
    state = hass.states.get(sensor_id)
    assert state is not None
    assert state.state == "1"

    result = await _choose(hass, issue_id, "ignore")

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "suggestion_ignored"
    assert _hidden_by(hass, sensor) is None
    state = hass.states.get(sensor_id)
    assert state is not None
    assert state.state == "0"
