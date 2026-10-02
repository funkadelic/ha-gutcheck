"""What the options forms show: step names, suggested values, change-back defaults, and the unloaded save."""

from __future__ import annotations

from typing import Any

from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.selector import SelectSelectorMode
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import (
    CONF_CHANGE_BACK,
    CONF_DAILY_BUDGET,
    CONF_UNDO_HIDDEN_SENSORS,
    CONF_UNDO_SENSORS,
    DOMAIN,
)

from .conftest import KEPT_DEVICE_CLASS_OPTIONS, register_unit_sensor, setup_and_confirm_device_class
from .test_hide_diagnostic_undo_rules import BOTH_ON, _both_kinds, _open_step


def _key(result: dict, name: str) -> Any:
    """The schema key called name in a form result."""
    return next(key for key in result["data_schema"].schema if str(key) == name)


async def test_the_init_form_names_its_step_and_carries_the_saved_values(hass: HomeAssistant) -> None:
    """The form is the init step, and each field is pre-filled from the saved options."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: "test-key"}, options={CONF_DAILY_BUDGET: 777})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["step_id"] == "init"
    assert _key(result, CONF_DAILY_BUDGET).description == {"suggested_value": 777}


async def test_the_change_back_checkbox_starts_unticked(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """With something to change back, the checkbox defaults to False, not unset or None."""
    sensor = register_unit_sensor(hass, "battery_pct", unit="%", name="Battery")
    await setup_and_confirm_device_class(hass, aioclient_mock, device_class_entry, sensor)

    result = await hass.config_entries.options.async_init(device_class_entry.entry_id)

    default = _key(result, CONF_CHANGE_BACK).default
    assert callable(default)
    assert default() is False


async def test_the_change_back_step_lists_each_kind_empty_and_as_a_list(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """Each kind's field starts with nothing picked and renders as a list."""
    await _both_kinds(hass, aioclient_mock, device_class_entry)

    result = await _open_step(hass, device_class_entry, BOTH_ON)

    assert result["step_id"] == "change_back"
    for name in (CONF_UNDO_SENSORS, CONF_UNDO_HIDDEN_SENSORS):
        key = _key(result, name)
        assert key.default() == []
        assert result["data_schema"].schema[key].config["mode"] == SelectSelectorMode.LIST


async def test_saving_with_the_entry_unloaded_creates_the_entry_without_a_change_back_step(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """A form without the checkbox saves directly, even when a record exists."""
    sensor = register_unit_sensor(hass, "battery_pct", unit="%", name="Battery")
    await setup_and_confirm_device_class(hass, aioclient_mock, device_class_entry, sensor)
    assert await hass.config_entries.async_unload(device_class_entry.entry_id)
    result = await hass.config_entries.options.async_init(device_class_entry.entry_id)

    result = await hass.config_entries.options.async_configure(result["flow_id"], KEPT_DEVICE_CLASS_OPTIONS)

    assert result["type"] is FlowResultType.CREATE_ENTRY
