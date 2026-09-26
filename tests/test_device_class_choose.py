"""Tests for the device class card's choose step: a fitting class, its labels, and every stale-state guard."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.selector import SelectSelector
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import (
    CONF_AREAS_ENABLED,
    CONF_DAILY_BUDGET,
    CONF_DEVICE_CLASS_ENABLED,
    CONF_HEALTH_ENABLED,
    CONF_UNDO_DEVICE_CLASS,
    CONF_UNDO_SENSORS,
    CONF_UPDATES_ENABLED,
    DEFAULT_DAILY_BUDGET,
    DEVICE_CLASS_ISSUE_PREFIX,
    DOMAIN,
)
from custom_components.gutcheck.recipes.device_class_cards import sync_device_class_cards
from custom_components.gutcheck.recipes.device_class_describe import class_names
from custom_components.gutcheck.recipes.safety import SafetyRules

from .conftest import register_unit_sensor, seed_device_class_card

_KEPT_OPTIONS = {
    CONF_HEALTH_ENABLED: False,
    CONF_UPDATES_ENABLED: False,
    CONF_AREAS_ENABLED: False,
    CONF_DEVICE_CLASS_ENABLED: True,
    CONF_DAILY_BUDGET: DEFAULT_DAILY_BUDGET,
}


async def _setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Add and set up the entry, with nothing registered yet, so the first run posts nothing."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)


async def _init_flow(hass: HomeAssistant, issue_id: str) -> dict[str, Any]:
    """Start the fix flow for issue_id and return its initial result."""
    assert await async_setup_component(hass, "repairs", {})
    manager = repairs_flow_manager(hass)
    assert manager is not None
    return await manager.async_init(DOMAIN, data={"issue_id": issue_id})


def _flow_id(result: dict[str, Any]) -> str:
    """The flow id out of a repairs flow result."""
    return str(result["flow_id"])


async def _configure(hass: HomeAssistant, flow_id: str, user_input: dict[str, Any]) -> dict[str, Any]:
    """Submit user_input to flow_id through the repairs flow manager."""
    manager = repairs_flow_manager(hass)
    assert manager is not None
    return await manager.async_configure(flow_id, user_input)


async def _open_choose(hass: HomeAssistant, issue_id: str) -> dict[str, Any]:
    """Open the fix flow and select choose from its menu."""
    result = await _init_flow(hass, issue_id)
    return await _configure(hass, _flow_id(result), {"next_step_id": "choose"})


@pytest.mark.parametrize(
    ("unit", "choice", "expected_menu"),
    [
        pytest.param("%", "battery", ["confirm", "choose", "ignore"], id="percent_offers_choose"),
        pytest.param("m", "distance", ["confirm", "ignore"], id="single_fit_has_no_choose"),
    ],
)
async def test_menu_offers_choose_only_when_another_class_fits(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    device_class_entry: MockConfigEntry,
    unit: str,
    choice: str,
    expected_menu: list[str],
) -> None:
    """The menu adds choose only when the sensor's live unit fits more than the suggested class."""
    await _setup_entry(hass, device_class_entry)
    sensor = register_unit_sensor(hass, f"sensor_{unit}", unit=unit, name="Sensor")
    issue_id = f"{DEVICE_CLASS_ISSUE_PREFIX}{sensor.id}"
    sync_device_class_cards(hass, SafetyRules(None), [{"registry_id": sensor.id, "choice": choice}], {choice: choice.title()})

    result = await _init_flow(hass, issue_id)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == expected_menu
    assert result["description_placeholders"] == issue.translation_placeholders


async def test_a_removed_sensors_card_menu_has_no_choose(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """A card whose sensor was removed before opening falls back to confirm, ignore."""
    sensor, issue_id = await seed_device_class_card(hass, device_class_entry)
    er.async_get(hass).async_remove(sensor.entity_id)

    result = await _init_flow(hass, issue_id)

    assert result["menu_options"] == ["confirm", "ignore"]


async def test_choose_form_offers_every_other_fitting_class_labelled_by_name(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """The choose form lists humidity, moisture and power_factor for a % sensor suggested battery, not battery itself."""
    _sensor, issue_id = await seed_device_class_card(hass, device_class_entry)
    names = await class_names(hass)

    result = await _open_choose(hass, issue_id)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "choose"
    selector = next(value for key, value in result["data_schema"].schema.items() if str(key) == "device_class")
    assert isinstance(selector, SelectSelector)
    options = selector.config["options"]
    assert {option["value"] for option in options} == {"humidity", "moisture", "power_factor"}
    assert {option["value"]: option["label"] for option in options} == {
        cls: names[cls] for cls in ("humidity", "moisture", "power_factor")
    }


async def test_submitting_a_fitting_pick_sets_it_records_it_and_the_change_back_clears_it(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """Picking humidity for a % sensor sets it, records it for change-back, removes the card; change-back then clears it."""
    sensor, issue_id = await seed_device_class_card(hass, device_class_entry)
    result = await _open_choose(hass, issue_id)

    result = await _configure(hass, _flow_id(result), {"device_class": "humidity"})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    updated = er.async_get(hass).async_get(sensor.entity_id)
    assert updated is not None
    assert updated.device_class == "humidity"
    assert device_class_entry.runtime_data.applied.get(sensor.id) == "humidity"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None

    options_result = await hass.config_entries.options.async_init(device_class_entry.entry_id)
    options_result = await hass.config_entries.options.async_configure(
        options_result["flow_id"], {**_KEPT_OPTIONS, CONF_UNDO_DEVICE_CLASS: True}
    )
    options_result = await hass.config_entries.options.async_configure(
        options_result["flow_id"], {CONF_UNDO_SENSORS: [sensor.id]}
    )
    assert options_result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done(wait_background_tasks=True)
    cleared = er.async_get(hass).async_get(sensor.entity_id)
    assert cleared is not None
    assert cleared.device_class is None


@pytest.mark.parametrize("bad_pick", ["distance", "battery"], ids=["unfitting_class", "suggested_class_not_offered"])
async def test_submitting_a_value_the_form_does_not_offer_raises_and_writes_nothing(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry, bad_pick: str
) -> None:
    """A pick the select never offered is rejected by the form schema before the flow runs, and writes nothing."""
    sensor, issue_id = await seed_device_class_card(hass, device_class_entry)
    result = await _open_choose(hass, issue_id)
    flow_id = _flow_id(result)

    with pytest.raises(InvalidData):
        await _configure(hass, flow_id, {"device_class": bad_pick})

    updated = er.async_get(hass).async_get(sensor.entity_id)
    assert updated is not None
    assert updated.device_class is None
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None


async def test_a_pick_submitted_after_the_sensor_was_hand_classed_aborts_and_keeps_that_class(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """A pick submitted after the sensor was hand-classed since choose opened aborts, leaving the hand-set class."""
    sensor, issue_id = await seed_device_class_card(hass, device_class_entry)
    result = await _open_choose(hass, issue_id)
    er.async_get(hass).async_update_entity(sensor.entity_id, device_class="moisture")

    result = await _configure(hass, _flow_id(result), {"device_class": "humidity"})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "suggestion_outdated"
    updated = er.async_get(hass).async_get(sensor.entity_id)
    assert updated is not None
    assert updated.device_class == "moisture"


async def test_choosing_after_the_sensor_was_removed_aborts_and_removes_the_card(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """Selecting choose after the menu's sensor was removed aborts as outdated and removes the card."""
    sensor, issue_id = await seed_device_class_card(hass, device_class_entry)
    result = await _init_flow(hass, issue_id)
    er.async_get(hass).async_remove(sensor.entity_id)

    result = await _configure(hass, _flow_id(result), {"next_step_id": "choose"})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "suggestion_outdated"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


def test_translations_label_every_menu_option_and_the_choose_step() -> None:
    """en.json labels confirm, choose and ignore for device class, and confirm/ignore only for area, with a choose step."""
    path = Path(__file__).parent.parent / "custom_components" / "gutcheck" / "translations" / "en.json"
    translations = json.loads(path.read_text(encoding="utf-8"))
    device_class_flow = translations["issues"]["device_class_suggestion"]["fix_flow"]
    area_flow = translations["issues"]["area_suggestion"]["fix_flow"]

    assert set(device_class_flow["step"]["init"]["menu_options"]) == {"confirm", "choose", "ignore"}
    assert "device_class" in device_class_flow["step"]["choose"]["data"]
    assert set(area_flow["step"]["init"]["menu_options"]) == {"confirm", "ignore"}
    assert "choose" not in area_flow["step"]
