"""A hidden sensor record is forgotten once the user unhides the sensor, so a later hand-hide is never Gut Check's."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import CONF_UNDO_DEVICE_CLASS, HIDE_DIAGNOSTIC_APPLIED_STORE_KEY
from custom_components.gutcheck.recipes.hide_diagnostic_undo import async_change_back_hidden

from .conftest import register_unit_sensor, setup_and_confirm_hide


def _hidden_by(hass: HomeAssistant, sensor: er.RegistryEntry) -> er.RegistryEntryHider | None:
    """The sensor's live hidden_by."""
    entry = er.async_get(hass).async_get(sensor.entity_id)
    assert entry is not None
    return entry.hidden_by


async def _offers_change_back(hass: HomeAssistant, entry: MockConfigEntry) -> bool:
    """Whether Configure's first form shows the change-back checkbox."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    hass.config_entries.options.async_abort(result["flow_id"])
    return CONF_UNDO_DEVICE_CLASS in {str(key) for key in result["data_schema"].schema}


async def test_a_sensor_unhidden_then_hidden_again_by_hand_is_never_changed_back(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hide_diagnostic_entry: MockConfigEntry
) -> None:
    """Confirm, unhide by hand, hide by hand: Configure no longer lists it and the change-back leaves it hidden."""
    sensor = register_unit_sensor(hass, "phone_wifi", unit=None, name="Wi-Fi connection")
    await setup_and_confirm_hide(hass, aioclient_mock, hide_diagnostic_entry, sensor)
    applied = hide_diagnostic_entry.runtime_data.applied_hidden
    assert applied.ids() == (sensor.id,)
    assert await _offers_change_back(hass, hide_diagnostic_entry)
    registry = er.async_get(hass)

    registry.async_update_entity(sensor.entity_id, hidden_by=None)
    await hass.async_block_till_done()
    assert applied.ids() == ()

    registry.async_update_entity(sensor.entity_id, hidden_by=er.RegistryEntryHider.USER)
    await hass.async_block_till_done()
    assert applied.ids() == ()
    assert not await _offers_change_back(hass, hide_diagnostic_entry)

    await async_change_back_hidden(hass, hide_diagnostic_entry, [sensor.id], None)

    assert _hidden_by(hass, sensor) is er.RegistryEntryHider.USER


async def test_a_sensor_since_hidden_by_its_integration_is_forgotten_and_left_hidden(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hide_diagnostic_entry: MockConfigEntry
) -> None:
    """An integration hide drops the record at once, so the change-back is not offered and never touches it."""
    sensor = register_unit_sensor(hass, "phone_wifi", unit=None, name="Wi-Fi connection")
    await setup_and_confirm_hide(hass, aioclient_mock, hide_diagnostic_entry, sensor)

    er.async_get(hass).async_update_entity(sensor.entity_id, hidden_by=er.RegistryEntryHider.INTEGRATION)
    await hass.async_block_till_done()

    assert hide_diagnostic_entry.runtime_data.applied_hidden.ids() == ()
    assert not await _offers_change_back(hass, hide_diagnostic_entry)
    assert _hidden_by(hass, sensor) is er.RegistryEntryHider.INTEGRATION


async def test_an_unrelated_registry_change_keeps_the_record(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hide_diagnostic_entry: MockConfigEntry
) -> None:
    """Renaming the hidden sensor leaves it recorded."""
    sensor = register_unit_sensor(hass, "phone_wifi", unit=None, name="Wi-Fi connection")
    await setup_and_confirm_hide(hass, aioclient_mock, hide_diagnostic_entry, sensor)

    er.async_get(hass).async_update_entity(sensor.entity_id, name="Phone Wi-Fi")
    await hass.async_block_till_done()

    assert hide_diagnostic_entry.runtime_data.applied_hidden.ids() == (sensor.id,)


def _unhide(hass: HomeAssistant, sensor: er.RegistryEntry) -> None:
    """Unhide the sensor by hand."""
    er.async_get(hass).async_update_entity(sensor.entity_id, hidden_by=None)


def _remove(hass: HomeAssistant, sensor: er.RegistryEntry) -> None:
    """Remove the sensor from the registry."""
    er.async_get(hass).async_remove(sensor.entity_id)


@pytest.mark.parametrize("change", [_unhide, _remove])
async def test_a_change_made_while_unloaded_is_pruned_at_setup(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hide_diagnostic_entry: MockConfigEntry,
    hass_storage: dict,
    change: Callable[[HomeAssistant, er.RegistryEntry], None],
) -> None:
    """A sensor unhidden or removed while Gut Check was unloaded is dropped from the record, and the drop is saved."""
    sensor = register_unit_sensor(hass, "phone_wifi", unit=None, name="Wi-Fi connection")
    await setup_and_confirm_hide(hass, aioclient_mock, hide_diagnostic_entry, sensor)
    assert await hass.config_entries.async_unload(hide_diagnostic_entry.entry_id)
    assert hass_storage[HIDE_DIAGNOSTIC_APPLIED_STORE_KEY]["data"] == {sensor.id: "user"}

    change(hass, sensor)
    await hass.async_block_till_done()
    assert hass_storage[HIDE_DIAGNOSTIC_APPLIED_STORE_KEY]["data"] == {sensor.id: "user"}
    assert await hass.config_entries.async_setup(hide_diagnostic_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert hide_diagnostic_entry.runtime_data.applied_hidden.ids() == ()
    assert await hass.config_entries.async_unload(hide_diagnostic_entry.entry_id)
    assert hass_storage[HIDE_DIAGNOSTIC_APPLIED_STORE_KEY]["data"] == {}
