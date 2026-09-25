"""Every change-back rule: per-sensor memory, restart and off/on survival, unit change, and orphan cleanup."""

from __future__ import annotations

from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry, flush_store
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
    RECIPE_DEVICE_CLASS,
)

from .conftest import (
    api_response,
    area_answer,
    posted_bodies,
    register_jev_responses,
    register_unit_sensor,
)

BATTERY_CANDIDATES = ["battery", "humidity", "moisture", "power_factor"]

_KEPT_OPTIONS = {
    CONF_HEALTH_ENABLED: False,
    CONF_UPDATES_ENABLED: True,
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


async def _change_back(hass: HomeAssistant, entry: MockConfigEntry, registry_ids: list[str]) -> None:
    """Tick the change-back checkbox, pick the given sensors, and save."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {**_KEPT_OPTIONS, CONF_UNDO_DEVICE_CLASS: True})
    assert result["step_id"] == "undo_device_class"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_UNDO_SENSORS: registry_ids})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_a_different_class_on_a_later_run_stays_hidden_the_memory_is_per_sensor(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """After a change-back, a later run answering a different class for the same sensor still raises no card."""
    sensor = register_unit_sensor(hass, "battery_pct", unit="%", name="Battery")
    register_jev_responses(aioclient_mock, [api_response({"s0": area_answer("battery", 0.9, BATTERY_CANDIDATES)})])
    device_class_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(device_class_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    issue_id = f"{DEVICE_CLASS_ISSUE_PREFIX}{sensor.id}"
    await _confirm(hass, issue_id)
    await _change_back(hass, device_class_entry, [sensor.id])

    aioclient_mock.clear_requests()
    register_jev_responses(aioclient_mock, [api_response({"s0": area_answer("humidity", 0.9, BATTERY_CANDIDATES)})])
    await device_class_entry.runtime_data.coordinators[RECIPE_DEVICE_CLASS].async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(posted_bodies(aioclient_mock)) == 1
    card = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert card is not None
    assert card.dismissed_version is not None
    updated = er.async_get(hass).async_get(sensor.entity_id)
    assert updated is not None
    assert updated.device_class is None


async def test_the_rejection_survives_a_restart_and_the_recipe_switched_off_and_on(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """The ignored card survives an unload/setup restart, and switching the recipe off then on keeps it."""
    sensor = register_unit_sensor(hass, "battery_pct", unit="%", name="Battery")
    register_jev_responses(aioclient_mock, [api_response({"s0": area_answer("battery", 0.9, BATTERY_CANDIDATES)})])
    device_class_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(device_class_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    issue_id = f"{DEVICE_CLASS_ISSUE_PREFIX}{sensor.id}"
    await _confirm(hass, issue_id)
    await _change_back(hass, device_class_entry, [sensor.id])

    assert await hass.config_entries.async_unload(device_class_entry.entry_id)
    registry = ir.async_get(hass)
    await flush_store(registry._store)
    await ir.async_load(hass)
    assert await hass.config_entries.async_setup(device_class_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    after_restart = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert after_restart is not None
    assert after_restart.dismissed_version is not None

    result = await hass.config_entries.options.async_init(device_class_entry.entry_id)
    await hass.config_entries.options.async_configure(result["flow_id"], {**_KEPT_OPTIONS, CONF_DEVICE_CLASS_ENABLED: False})
    await hass.async_block_till_done(wait_background_tasks=True)
    result = await hass.config_entries.options.async_init(device_class_entry.entry_id)
    await hass.config_entries.options.async_configure(result["flow_id"], _KEPT_OPTIONS)
    await hass.async_block_till_done(wait_background_tasks=True)

    after_toggle = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert after_toggle is not None
    assert after_toggle.dismissed_version is not None


async def test_a_sensor_whose_unit_changed_still_clears_but_raises_no_card(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """A sensor whose live unit no longer accepts the recorded class still has its override cleared, but no card."""
    sensor = register_unit_sensor(hass, "battery_pct", unit="%", name="Battery")
    register_jev_responses(aioclient_mock, [api_response({"s0": area_answer("battery", 0.9, BATTERY_CANDIDATES)})])
    device_class_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(device_class_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    issue_id = f"{DEVICE_CLASS_ISSUE_PREFIX}{sensor.id}"
    await _confirm(hass, issue_id)
    er.async_get(hass).async_update_entity(sensor.entity_id, unit_of_measurement="kg")

    await _change_back(hass, device_class_entry, [sensor.id])

    updated = er.async_get(hass).async_get(sensor.entity_id)
    assert updated is not None
    assert updated.device_class is None
    assert device_class_entry.runtime_data.applied.get(sensor.id) is None
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_a_recorded_sensor_removed_from_the_registry_is_dropped_on_the_next_save(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """A recorded sensor removed from the registry is dropped from the record by the next change-back save."""
    kept = register_unit_sensor(hass, "battery_kept", unit="%", name="Kept Battery")
    removed = register_unit_sensor(hass, "battery_removed", unit="%", name="Removed Battery")
    answer = area_answer("battery", 0.9, BATTERY_CANDIDATES)
    register_jev_responses(aioclient_mock, [api_response({"s0": answer, "s1": answer})])
    device_class_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(device_class_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    for sensor in (kept, removed):
        await _confirm(hass, f"{DEVICE_CLASS_ISSUE_PREFIX}{sensor.id}")
    er.async_get(hass).async_remove(removed.entity_id)

    await _change_back(hass, device_class_entry, [])

    assert device_class_entry.runtime_data.applied.get(removed.id) is None
    assert device_class_entry.runtime_data.applied.get(kept.id) == "battery"
