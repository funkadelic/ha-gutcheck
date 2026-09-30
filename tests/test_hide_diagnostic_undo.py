"""Timeline test: a hidden sensor is unhidden from Configure, and its suggestion never comes back."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import (
    CONF_CHANGE_BACK,
    CONF_DAILY_BUDGET,
    CONF_UNDO_HIDDEN_SENSORS,
    CONF_UNDO_SENSORS,
    DEFAULT_DAILY_BUDGET,
    DOMAIN,
    HIDE_DIAGNOSTIC_APPLIED_STORE_KEY,
    RECIPE_HIDE_DIAGNOSTIC,
)

from .conftest import (
    api_response,
    find_recipe_sensor,
    hide_diagnostic_answer,
    press_recipe_run,
    register_jev_responses_by_question,
    register_unit_sensor,
    setup_and_confirm_hide,
)


def _fields(result: dict) -> set[str]:
    """The field names of a form result's schema."""
    return {str(key) for key in result["data_schema"].schema}


async def test_a_hidden_sensor_is_unhidden_from_configure_and_not_suggested_again(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hide_diagnostic_entry: MockConfigEntry, hass_storage: dict
) -> None:
    """Hide, change back from Configure: unhidden, card ignored, record gone, and a rerun leaves it alone."""
    sensor = register_unit_sensor(hass, "phone_wifi", unit=None, name="Wi-Fi connection")
    [issue_id] = await setup_and_confirm_hide(hass, aioclient_mock, hide_diagnostic_entry, sensor)
    registry = er.async_get(hass)
    assert registry.async_get(sensor.entity_id).hidden_by is er.RegistryEntryHider.USER  # type: ignore[union-attr]
    options = {**hide_diagnostic_entry.options, CONF_DAILY_BUDGET: DEFAULT_DAILY_BUDGET}

    result = await hass.config_entries.options.async_init(hide_diagnostic_entry.entry_id)
    assert CONF_CHANGE_BACK in _fields(result)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {**options, CONF_CHANGE_BACK: True})
    assert result["step_id"] == "change_back"
    assert _fields(result) == {CONF_UNDO_HIDDEN_SENSORS}
    selector = next(iter(result["data_schema"].schema.values()))
    assert selector.config["options"] == [{"value": sensor.id, "label": "Wi-Fi connection"}]
    assert CONF_UNDO_SENSORS not in _fields(result)

    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_UNDO_HIDDEN_SENSORS: [sensor.id]})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == options
    await hass.async_block_till_done(wait_background_tasks=True)

    assert registry.async_get(sensor.entity_id).hidden_by is None  # type: ignore[union-attr]
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.dismissed_version is not None
    assert hide_diagnostic_entry.runtime_data.applied_hidden.get(sensor.id) is None
    assert sensor.id not in hass_storage[HIDE_DIAGNOSTIC_APPLIED_STORE_KEY]["data"]
    result = await hass.config_entries.options.async_init(hide_diagnostic_entry.entry_id)
    assert CONF_CHANGE_BACK not in _fields(result)

    aioclient_mock.clear_requests()
    register_jev_responses_by_question(aioclient_mock, {"h0": api_response({"h0": hide_diagnostic_answer("diagnostic", 0.9)})})
    await press_recipe_run(hass, hide_diagnostic_entry, RECIPE_HIDE_DIAGNOSTIC)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.dismissed_version is not None
    sensor_id = find_recipe_sensor(hass, hide_diagnostic_entry, RECIPE_HIDE_DIAGNOSTIC)
    assert sensor_id is not None
    state = hass.states.get(sensor_id)
    assert state is not None
    assert state.state == "0"
