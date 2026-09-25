"""Timeline test: a confirmed device class is recorded, survives a restart, and Configure changes it back."""

from __future__ import annotations

from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
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
    DEFAULT_DAILY_BUDGET,
    DEVICE_CLASS_APPLIED_STORE_KEY,
    DEVICE_CLASS_ISSUE_PREFIX,
    DOMAIN,
    STORE_VERSION,
)
from custom_components.gutcheck.recipes.device_class_cards import sync_device_class_cards
from custom_components.gutcheck.recipes.safety import SafetyRules

from .conftest import (
    api_response,
    area_answer,
    register_jev_responses,
    register_unit_sensor,
)

BATTERY_CANDIDATES = ["battery", "humidity", "moisture", "power_factor"]

_KEPT_OPTIONS = {
    CONF_HEALTH_ENABLED: False,
    CONF_AREAS_ENABLED: False,
    CONF_DEVICE_CLASS_ENABLED: True,
    CONF_DAILY_BUDGET: DEFAULT_DAILY_BUDGET,
}


async def _confirm(hass: HomeAssistant, issue_id: str) -> None:
    """Confirm a device class card through Home Assistant's own repairs flow manager."""
    assert await async_setup_component(hass, "repairs", {})
    manager = repairs_flow_manager(hass)
    assert manager is not None
    result = await manager.async_init(DOMAIN, data={"issue_id": issue_id})
    assert result["type"] is FlowResultType.MENU
    result = await manager.async_configure(result["flow_id"], {"next_step_id": "confirm"})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def _open_undo_step(hass: HomeAssistant, entry: MockConfigEntry) -> dict:
    """Tick the change-back checkbox on the init form and return the change-back step's result."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {**_KEPT_OPTIONS, CONF_UNDO_DEVICE_CLASS: True})
    assert result["step_id"] == "undo_device_class"
    return result


async def test_confirm_records_the_class_and_it_survives_a_restart(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    device_class_entry: MockConfigEntry,
    hass_storage: dict,
) -> None:
    """A confirm records the sensor under its class; the record and the Store both survive an unload and a new setup."""
    sensor = register_unit_sensor(hass, "battery_pct", unit="%", name="Battery")
    register_jev_responses(aioclient_mock, [api_response({"s0": area_answer("battery", 0.9, BATTERY_CANDIDATES)})])
    device_class_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(device_class_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    issue_id = f"{DEVICE_CLASS_ISSUE_PREFIX}{sensor.id}"
    await _confirm(hass, issue_id)

    assert device_class_entry.runtime_data.applied.get(sensor.id) == "battery"

    assert await hass.config_entries.async_unload(device_class_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass_storage[DEVICE_CLASS_APPLIED_STORE_KEY]["data"] == {sensor.id: "battery"}

    assert await hass.config_entries.async_setup(device_class_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert device_class_entry.runtime_data.applied.get(sensor.id) == "battery"


async def test_record_still_loads_and_offers_change_back_with_the_recipe_switched_off(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """Switching device class suggestions off keeps the record loaded and Configure still offers the change-back."""
    sensor = register_unit_sensor(hass, "battery_pct", unit="%", name="Battery")
    register_jev_responses(aioclient_mock, [api_response({"s0": area_answer("battery", 0.9, BATTERY_CANDIDATES)})])
    device_class_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(device_class_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    issue_id = f"{DEVICE_CLASS_ISSUE_PREFIX}{sensor.id}"
    await _confirm(hass, issue_id)

    result = await hass.config_entries.options.async_init(device_class_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**_KEPT_OPTIONS, CONF_DEVICE_CLASS_ENABLED: False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done(wait_background_tasks=True)
    assert device_class_entry.runtime_data.applied.get(sensor.id) == "battery"

    result = await hass.config_entries.options.async_init(device_class_entry.entry_id)
    assert CONF_UNDO_DEVICE_CLASS in {str(key) for key in result["data_schema"].schema}


async def test_init_form_has_no_change_back_option_when_nothing_is_recorded(
    hass: HomeAssistant, device_class_entry: MockConfigEntry
) -> None:
    """No sensor confirmed yet: the init form has no change-back checkbox."""
    device_class_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(device_class_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    result = await hass.config_entries.options.async_init(device_class_entry.entry_id)
    assert CONF_UNDO_DEVICE_CLASS not in {str(key) for key in result["data_schema"].schema}


async def test_init_form_has_no_change_back_option_when_the_entry_is_not_loaded(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """A record exists, but the entry is unloaded: the init form has no change-back checkbox."""
    sensor = register_unit_sensor(hass, "battery_pct", unit="%", name="Battery")
    register_jev_responses(aioclient_mock, [api_response({"s0": area_answer("battery", 0.9, BATTERY_CANDIDATES)})])
    device_class_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(device_class_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    issue_id = f"{DEVICE_CLASS_ISSUE_PREFIX}{sensor.id}"
    await _confirm(hass, issue_id)
    assert await hass.config_entries.async_unload(device_class_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    result = await hass.config_entries.options.async_init(device_class_entry.entry_id)
    assert CONF_UNDO_DEVICE_CLASS not in {str(key) for key in result["data_schema"].schema}


async def test_ticking_change_back_opens_the_step_and_saves_the_rest_unchanged(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """Ticking the checkbox opens the change-back step; the entry's saved options never carry the checkbox itself."""
    sensor = register_unit_sensor(hass, "battery_pct", unit="%", name="Battery")
    register_jev_responses(aioclient_mock, [api_response({"s0": area_answer("battery", 0.9, BATTERY_CANDIDATES)})])
    device_class_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(device_class_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    issue_id = f"{DEVICE_CLASS_ISSUE_PREFIX}{sensor.id}"
    await _confirm(hass, issue_id)

    result = await _open_undo_step(hass, device_class_entry)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_UNDO_SENSORS: [sensor.id]})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == _KEPT_OPTIONS
    await hass.async_block_till_done(wait_background_tasks=True)
    assert CONF_UNDO_DEVICE_CLASS not in device_class_entry.options


async def test_change_back_step_lists_recorded_sensors_sorted_by_label_with_no_registry_id(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """The change-back step lists one choice per recorded, still-registered sensor, sorted by its display name."""
    zeta = register_unit_sensor(hass, "battery_zeta", unit="%", name="Zeta Battery")
    alpha = register_unit_sensor(hass, "battery_alpha", unit="%", name="Alpha Battery")
    answer = area_answer("battery", 0.9, BATTERY_CANDIDATES)
    register_jev_responses(aioclient_mock, [api_response({"s0": answer, "s1": answer})])
    device_class_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(device_class_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    for sensor in (zeta, alpha):
        await _confirm(hass, f"{DEVICE_CLASS_ISSUE_PREFIX}{sensor.id}")

    result = await _open_undo_step(hass, device_class_entry)
    selector = next(value for key, value in result["data_schema"].schema.items() if str(key) == CONF_UNDO_SENSORS)
    options = selector.config["options"]

    assert [option["label"] for option in options] == ["Alpha Battery", "Zeta Battery"]
    assert {option["value"] for option in options} == {zeta.id, alpha.id}
    assert all(zeta.id not in option["label"] and alpha.id not in option["label"] for option in options)


async def test_picking_a_sensor_whose_override_still_matches_clears_it_and_drops_the_record(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """Picking a sensor whose live override still equals the recorded class clears it and drops the record."""
    sensor = register_unit_sensor(hass, "battery_pct", unit="%", name="Battery")
    register_jev_responses(aioclient_mock, [api_response({"s0": area_answer("battery", 0.9, BATTERY_CANDIDATES)})])
    device_class_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(device_class_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    await _confirm(hass, f"{DEVICE_CLASS_ISSUE_PREFIX}{sensor.id}")

    result = await _open_undo_step(hass, device_class_entry)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_UNDO_SENSORS: [sensor.id]})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done(wait_background_tasks=True)

    updated = er.async_get(hass).async_get(sensor.entity_id)
    assert updated is not None
    assert updated.device_class is None
    assert updated.original_device_class is None
    assert updated.unit_of_measurement == "%"
    assert device_class_entry.runtime_data.applied.get(sensor.id) is None


async def test_picking_a_sensor_hand_changed_since_leaves_it_and_drops_the_record(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """A sensor hand-classed to something else since the confirm keeps that class, and still drops from the record."""
    sensor = register_unit_sensor(hass, "battery_pct", unit="%", name="Battery")
    register_jev_responses(aioclient_mock, [api_response({"s0": area_answer("battery", 0.9, BATTERY_CANDIDATES)})])
    device_class_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(device_class_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    await _confirm(hass, f"{DEVICE_CLASS_ISSUE_PREFIX}{sensor.id}")
    er.async_get(hass).async_update_entity(sensor.entity_id, device_class="humidity")

    result = await _open_undo_step(hass, device_class_entry)
    await hass.config_entries.options.async_configure(result["flow_id"], {CONF_UNDO_SENSORS: [sensor.id]})
    await hass.async_block_till_done(wait_background_tasks=True)

    updated = er.async_get(hass).async_get(sensor.entity_id)
    assert updated is not None
    assert updated.device_class == "humidity"
    assert device_class_entry.runtime_data.applied.get(sensor.id) is None


async def test_saving_with_nothing_picked_changes_nothing_and_keeps_the_record(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """Saving the change-back step with nothing picked leaves the sensor and the record exactly as they were."""
    sensor = register_unit_sensor(hass, "battery_pct", unit="%", name="Battery")
    register_jev_responses(aioclient_mock, [api_response({"s0": area_answer("battery", 0.9, BATTERY_CANDIDATES)})])
    device_class_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(device_class_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    await _confirm(hass, f"{DEVICE_CLASS_ISSUE_PREFIX}{sensor.id}")

    result = await _open_undo_step(hass, device_class_entry)
    await hass.config_entries.options.async_configure(result["flow_id"], {})
    await hass.async_block_till_done(wait_background_tasks=True)

    updated = er.async_get(hass).async_get(sensor.entity_id)
    assert updated is not None
    assert updated.device_class == "battery"
    assert device_class_entry.runtime_data.applied.get(sensor.id) == "battery"


async def test_confirm_with_no_loaded_entry_aborts_and_writes_nothing(hass: HomeAssistant) -> None:
    """A confirm with the component loaded but no config entry set up aborts as outdated and writes nothing."""
    sensor = register_unit_sensor(hass, "battery_pct", unit="%", name="Battery")
    issue_id = f"{DEVICE_CLASS_ISSUE_PREFIX}{sensor.id}"
    sync_device_class_cards(hass, SafetyRules(None), [{"registry_id": sensor.id, "choice": "battery"}], {"battery": "Battery"})

    assert await async_setup_component(hass, DOMAIN, {})
    assert await async_setup_component(hass, "repairs", {})
    manager = repairs_flow_manager(hass)
    assert manager is not None
    result = await manager.async_init(DOMAIN, data={"issue_id": issue_id})
    assert result["type"] is FlowResultType.MENU
    result = await manager.async_configure(result["flow_id"], {"next_step_id": "confirm"})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "suggestion_outdated"
    updated = er.async_get(hass).async_get(sensor.entity_id)
    assert updated is not None
    assert updated.device_class is None


async def test_removing_the_entry_removes_the_store_and_leaves_the_class(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """Removing the entry deletes the applied-classes Store, but leaves the class Gut Check set on the sensor."""
    sensor = register_unit_sensor(hass, "battery_pct", unit="%", name="Battery")
    register_jev_responses(aioclient_mock, [api_response({"s0": area_answer("battery", 0.9, BATTERY_CANDIDATES)})])
    device_class_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(device_class_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    await _confirm(hass, f"{DEVICE_CLASS_ISSUE_PREFIX}{sensor.id}")

    await hass.config_entries.async_remove(device_class_entry.entry_id)
    await hass.async_block_till_done()

    stored = await Store(hass, STORE_VERSION, DEVICE_CLASS_APPLIED_STORE_KEY).async_load()
    assert stored is None
    updated = er.async_get(hass).async_get(sensor.entity_id)
    assert updated is not None
    assert updated.device_class == "battery"


async def test_confirm_restart_change_back_timeline_clears_the_class(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """Confirm through the repairs flow manager, restart, then change back through the options flow manager."""
    sensor = register_unit_sensor(hass, "battery_pct", unit="%", name="Battery")
    register_jev_responses(aioclient_mock, [api_response({"s0": area_answer("battery", 0.9, BATTERY_CANDIDATES)})])
    device_class_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(device_class_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    await _confirm(hass, f"{DEVICE_CLASS_ISSUE_PREFIX}{sensor.id}")

    assert await hass.config_entries.async_unload(device_class_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert await hass.config_entries.async_setup(device_class_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    result = await _open_undo_step(hass, device_class_entry)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_UNDO_SENSORS: [sensor.id]})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done(wait_background_tasks=True)

    updated = er.async_get(hass).async_get(sensor.entity_id)
    assert updated is not None
    assert updated.device_class is None
