"""A restart restores diagnostic sensor results with no request, dropping any sensor that changed since."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import label_registry as lr
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import (
    ATTR_COUNTS,
    ATTR_ITEMS,
    ATTR_UNSURE,
    CONF_CRITICAL_LABEL,
    CONF_DAILY_BUDGET,
    CONF_HIDE_DIAGNOSTIC_ENABLED,
    DEFAULT_DAILY_BUDGET,
    DEVICE_CLASS_ISSUE_PREFIX,
    DOMAIN,
    HIDE_DIAGNOSTIC_ISSUE_PREFIX,
    OPTION_SUGGESTED,
    RECIPE_HIDE_DIAGNOSTIC,
)
from custom_components.gutcheck.recipes.hide_diagnostic_const import OPTION_DIAGNOSTIC, OPTION_PRIMARY

from .conftest import (
    KEPT_DEVICE_CLASS_OPTIONS,
    api_response,
    area_answer,
    hide_diagnostic_answer,
    posted_bodies,
    recipe_sensor_entity_id,
    register_jev_responses_by_question,
    register_unit_sensor,
    restart_config_entry,
)

Change = Callable[[HomeAssistant, er.RegistryEntry, MockConfigEntry], None]


def _hide(hass: HomeAssistant, sensor: er.RegistryEntry, entry: MockConfigEntry) -> None:
    """Hide the sensor as the user."""
    er.async_get(hass).async_update_entity(sensor.entity_id, hidden_by=er.RegistryEntryHider.USER)


def _categorise(hass: HomeAssistant, sensor: er.RegistryEntry, entry: MockConfigEntry) -> None:
    """Give the sensor the diagnostic entity category."""
    er.async_get(hass).async_update_entity(sensor.entity_id, entity_category=er.EntityCategory.DIAGNOSTIC)


def _classify(hass: HomeAssistant, sensor: er.RegistryEntry, entry: MockConfigEntry) -> None:
    """Give the sensor a temperature device class override."""
    er.async_get(hass).async_update_entity(sensor.entity_id, device_class="temperature")


def _disable(hass: HomeAssistant, sensor: er.RegistryEntry, entry: MockConfigEntry) -> None:
    """Disable the sensor."""
    er.async_get(hass).async_update_entity(sensor.entity_id, disabled_by=er.RegistryEntryDisabler.USER)


def _remove(hass: HomeAssistant, sensor: er.RegistryEntry, entry: MockConfigEntry) -> None:
    """Remove the sensor from the registry."""
    er.async_get(hass).async_remove(sensor.entity_id)


def _label_critical(hass: HomeAssistant, sensor: er.RegistryEntry, entry: MockConfigEntry) -> None:
    """Label the sensor with the configured critical label."""
    registry = lr.async_get(hass)
    label = registry.async_get_label_by_name("Critical") or registry.async_create("Critical")
    er.async_get(hass).async_update_entity(sensor.entity_id, labels={label.label_id})
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_CRITICAL_LABEL: label.label_id})


async def _setup_three_buckets(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> list[er.RegistryEntry]:
    """Set up with one suggested, one primary and one unsure sensor (all with a % unit); returns them in that order."""
    sensors = [register_unit_sensor(hass, f"s{i}", unit="%", name=f"Sensor {i}") for i in range(3)]
    answers = {
        "h0": hide_diagnostic_answer(OPTION_DIAGNOSTIC, 0.9),
        "h1": hide_diagnostic_answer(OPTION_PRIMARY, 0.9),
        "h2": hide_diagnostic_answer(OPTION_DIAGNOSTIC, 0.2),
    }
    register_jev_responses_by_question(aioclient_mock, {"h0": api_response(answers)})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return sorted(sensors, key=lambda sensor: sensor.entity_id)


def _attributes(hass: HomeAssistant, entry: MockConfigEntry) -> tuple[str, dict]:
    """The recipe sensor's state and attributes."""
    state = hass.states.get(recipe_sensor_entity_id(hass, entry, RECIPE_HIDE_DIAGNOSTIC))
    assert state is not None
    return state.state, dict(state.attributes)


def _hide_card_count(hass: HomeAssistant) -> int:
    """How many hide cards exist."""
    return sum(
        1
        for domain, issue_id in ir.async_get(hass).issues
        if domain == DOMAIN and issue_id.startswith(HIDE_DIAGNOSTIC_ISSUE_PREFIX)
    )


async def test_a_plain_restart_restores_every_bucket_and_card_with_no_request(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hide_diagnostic_entry: MockConfigEntry
) -> None:
    """Nothing changed: the same counts, unsure list and card come back and no second request is sent."""
    await _setup_three_buckets(hass, aioclient_mock, hide_diagnostic_entry)
    before = _attributes(hass, hide_diagnostic_entry)

    await restart_config_entry(hass, hide_diagnostic_entry)

    assert len(posted_bodies(aioclient_mock)) == 1
    after = _attributes(hass, hide_diagnostic_entry)
    assert after[0] == before[0] == "1"
    assert after[1][ATTR_COUNTS] == before[1][ATTR_COUNTS]
    assert len(after[1][ATTR_UNSURE]) == 1
    assert _hide_card_count(hass) == 1


@pytest.mark.parametrize("change", [_hide, _categorise, _classify, _disable, _remove, _label_critical])
async def test_restore_drops_a_sensor_that_changed_since_from_every_bucket_and_unsure(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hide_diagnostic_entry: MockConfigEntry, change: Change
) -> None:
    """Whatever made it stop qualifying, a restart drops it from suggested, primary and unsure, recounts and removes its card."""
    sensors = await _setup_three_buckets(hass, aioclient_mock, hide_diagnostic_entry)
    assert _hide_card_count(hass) == 1
    for sensor in sensors:
        change(hass, sensor, hide_diagnostic_entry)

    await restart_config_entry(hass, hide_diagnostic_entry)

    assert len(posted_bodies(aioclient_mock)) == 1
    state, attributes = _attributes(hass, hide_diagnostic_entry)
    assert state == "0"
    assert all(count == 0 for count in attributes[ATTR_COUNTS].values())
    assert all(items == [] for items in attributes[ATTR_ITEMS].values())
    assert attributes[ATTR_UNSURE] == []
    assert _hide_card_count(hass) == 0


async def test_restore_drops_a_sensor_that_gained_an_open_device_class_card(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hide_diagnostic_entry: MockConfigEntry
) -> None:
    """Switching device class suggestions on raises cards for the sensors; the next restart drops them from every bucket."""
    await _setup_three_buckets(hass, aioclient_mock, hide_diagnostic_entry)
    both_on = {**KEPT_DEVICE_CLASS_OPTIONS, CONF_HIDE_DIAGNOSTIC_ENABLED: True, CONF_DAILY_BUDGET: DEFAULT_DAILY_BUDGET}
    battery = area_answer("battery", 0.9, ["battery", "humidity", "moisture", "power_factor"])
    aioclient_mock.clear_requests()
    register_jev_responses_by_question(aioclient_mock, {"s0": api_response({"s0": battery, "s1": battery, "s2": battery})})
    result = await hass.config_entries.options.async_init(hide_diagnostic_entry.entry_id)
    await hass.config_entries.options.async_configure(result["flow_id"], both_on)
    await hass.async_block_till_done(wait_background_tasks=True)
    open_device_class = [
        issue_id
        for domain, issue_id in ir.async_get(hass).issues
        if domain == DOMAIN and issue_id.startswith(DEVICE_CLASS_ISSUE_PREFIX)
    ]
    assert len(open_device_class) == 3

    await restart_config_entry(hass, hide_diagnostic_entry)

    assert len(posted_bodies(aioclient_mock)) == 1
    state, attributes = _attributes(hass, hide_diagnostic_entry)
    assert state == "0"
    assert attributes[ATTR_COUNTS][OPTION_SUGGESTED] == 0
    assert attributes[ATTR_UNSURE] == []
    assert _hide_card_count(hass) == 0
