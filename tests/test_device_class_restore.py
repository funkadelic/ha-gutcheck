"""Timeline tests: a free restore reproduces device class cards, and drops sensors that stopped qualifying."""

from __future__ import annotations

from typing import Any

from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import label_registry as lr
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import (
    CONF_AREAS_ENABLED,
    CONF_CRITICAL_LABEL,
    CONF_DAILY_BUDGET,
    CONF_DEVICE_CLASS_ENABLED,
    CONF_HEALTH_ENABLED,
    CONF_UPDATES_ENABLED,
    DEFAULT_DAILY_BUDGET,
    DEVICE_CLASS_ISSUE_PREFIX,
    DOMAIN,
)

from .conftest import (
    api_response,
    area_answer,
    device_class_sensor_entity_id,
    find_device_class_sensor,
    posted_bodies,
    register_jev_responses,
    register_unit_sensor,
)

PERCENT_CANDIDATES = ["battery", "humidity", "moisture", "power_factor"]


async def test_restart_within_a_week_restores_the_sensor_and_cards_with_no_request(
    hass: HomeAssistant, freezer: Any, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """A restart 3 days after the last run restores the sensor and every card for free, ignore included."""
    freezer.move_to("2026-01-01T00:00:00-08:00")
    sensor = register_unit_sensor(hass, "a", unit="%", name="A")
    register_jev_responses(aioclient_mock, [api_response({"s0": area_answer("battery", 0.9, PERCENT_CANDIDATES)})])
    device_class_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(device_class_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(posted_bodies(aioclient_mock)) == 1

    issue_id = f"{DEVICE_CLASS_ISSUE_PREFIX}{sensor.id}"
    ir.async_ignore_issue(hass, DOMAIN, issue_id, True)

    freezer.move_to("2026-01-04T00:00:00-08:00")
    assert await hass.config_entries.async_unload(device_class_entry.entry_id)
    assert await hass.config_entries.async_setup(device_class_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(posted_bodies(aioclient_mock)) == 1
    state = hass.states.get(device_class_sensor_entity_id(hass, device_class_entry))
    assert state is not None
    assert state.state == "0"
    assert len(state.attributes["items"]["suggested"]) == 1
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.dismissed_version is not None


async def test_restore_drops_a_sensor_classed_disabled_or_labelled_critical_from_every_bucket(
    hass: HomeAssistant, freezer: Any, aioclient_mock: AiohttpClientMocker
) -> None:
    """restore filters both suggested and unsure, rewrites their counts, and deletes each dropped card."""
    freezer.move_to("2026-01-01T00:00:00-08:00")
    label = lr.async_get(hass).async_create("Critical")
    classed_by_hand = register_unit_sensor(hass, "x", unit="%", name="X")
    turned_disabled = register_unit_sensor(hass, "y", unit="%", name="Y")
    labelled = register_unit_sensor(hass, "w", unit="%", name="W")
    untouched = register_unit_sensor(hass, "z", unit="%", name="Z")
    confidence_by_id = {
        classed_by_hand.id: 0.9,
        turned_disabled.id: 0.9,
        labelled.id: 0.2,
        untouched.id: 0.9,
    }
    ordered = sorted((classed_by_hand, turned_disabled, labelled, untouched), key=lambda entry: entry.entity_id)
    answers = {
        f"s{index}": area_answer("battery", confidence_by_id[entry.id], PERCENT_CANDIDATES) for index, entry in enumerate(ordered)
    }
    register_jev_responses(aioclient_mock, [api_response(answers)])
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_API_KEY: "test-key"},
        options={
            CONF_HEALTH_ENABLED: False,
            CONF_UPDATES_ENABLED: False,
            CONF_AREAS_ENABLED: False,
            CONF_DEVICE_CLASS_ENABLED: True,
            CONF_CRITICAL_LABEL: label.label_id,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    dropped_hand_id = f"{DEVICE_CLASS_ISSUE_PREFIX}{classed_by_hand.id}"
    dropped_disabled_id = f"{DEVICE_CLASS_ISSUE_PREFIX}{turned_disabled.id}"
    kept_issue_id = f"{DEVICE_CLASS_ISSUE_PREFIX}{untouched.id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, dropped_hand_id) is not None
    assert ir.async_get(hass).async_get_issue(DOMAIN, dropped_disabled_id) is not None
    assert ir.async_get(hass).async_get_issue(DOMAIN, kept_issue_id) is not None

    registry = er.async_get(hass)
    registry.async_update_entity(classed_by_hand.entity_id, device_class="battery")
    registry.async_update_entity(turned_disabled.entity_id, disabled_by=er.RegistryEntryDisabler.USER)
    registry.async_update_entity(labelled.entity_id, labels={label.label_id})

    posted_before = len(posted_bodies(aioclient_mock))
    freezer.move_to("2026-01-04T00:00:00-08:00")
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(posted_bodies(aioclient_mock)) == posted_before
    state = hass.states.get(device_class_sensor_entity_id(hass, entry))
    assert state is not None
    suggested = state.attributes["items"]["suggested"]
    unsure = state.attributes["unsure"]
    assert {item["registry_id"] for item in suggested} == {untouched.id}
    assert {item["registry_id"] for item in unsure} == set()
    assert state.state == str(len(suggested))
    assert ir.async_get(hass).async_get_issue(DOMAIN, dropped_hand_id) is None
    assert ir.async_get(hass).async_get_issue(DOMAIN, dropped_disabled_id) is None
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{DEVICE_CLASS_ISSUE_PREFIX}{labelled.id}") is None
    assert ir.async_get(hass).async_get_issue(DOMAIN, kept_issue_id) is not None


async def test_restore_drops_a_removed_sensor_and_deletes_its_card(
    hass: HomeAssistant, freezer: Any, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """A sensor removed from the registry since the run drops out of the stored result on restore too."""
    freezer.move_to("2026-01-01T00:00:00-08:00")
    sensor = register_unit_sensor(hass, "a", unit="%", name="A")
    register_jev_responses(aioclient_mock, [api_response({"s0": area_answer("battery", 0.9, PERCENT_CANDIDATES)})])
    device_class_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(device_class_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    issue_id = f"{DEVICE_CLASS_ISSUE_PREFIX}{sensor.id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    er.async_get(hass).async_remove(sensor.entity_id)

    freezer.move_to("2026-01-04T00:00:00-08:00")
    assert await hass.config_entries.async_unload(device_class_entry.entry_id)
    assert await hass.config_entries.async_setup(device_class_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    state = hass.states.get(device_class_sensor_entity_id(hass, device_class_entry))
    assert state is not None
    assert state.attributes["items"]["suggested"] == []
    assert state.state == "0"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_disabling_and_reenabling_within_the_week_restores_the_open_card_and_keeps_the_ignored_one(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """Re-enabling within the cadence window restores for free and brings the open card back, within the cap."""
    open_sensor = register_unit_sensor(hass, "open", unit="%", name="Open")
    ignored_sensor = register_unit_sensor(hass, "ignored", unit="%", name="Ignored")
    run1_answer = area_answer("battery", 0.9, PERCENT_CANDIDATES)
    register_jev_responses(aioclient_mock, [api_response({"s0": run1_answer, "s1": run1_answer})])
    device_class_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(device_class_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    open_issue_id = f"{DEVICE_CLASS_ISSUE_PREFIX}{open_sensor.id}"
    ignored_issue_id = f"{DEVICE_CLASS_ISSUE_PREFIX}{ignored_sensor.id}"
    ir.async_ignore_issue(hass, DOMAIN, ignored_issue_id, True)

    off_options = {
        CONF_HEALTH_ENABLED: False,
        CONF_UPDATES_ENABLED: False,
        CONF_AREAS_ENABLED: False,
        CONF_DEVICE_CLASS_ENABLED: False,
        CONF_DAILY_BUDGET: DEFAULT_DAILY_BUDGET,
    }
    on_options = {**off_options, CONF_DEVICE_CLASS_ENABLED: True}

    result = await hass.config_entries.options.async_init(device_class_entry.entry_id)
    await hass.config_entries.options.async_configure(result["flow_id"], off_options)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert find_device_class_sensor(hass, device_class_entry) is None
    assert ir.async_get(hass).async_get_issue(DOMAIN, open_issue_id) is None

    posted_before = len(posted_bodies(aioclient_mock))
    result = await hass.config_entries.options.async_init(device_class_entry.entry_id)
    await hass.config_entries.options.async_configure(result["flow_id"], on_options)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(posted_bodies(aioclient_mock)) == posted_before
    assert ir.async_get(hass).async_get_issue(DOMAIN, open_issue_id) is not None
    ignored_issue = ir.async_get(hass).async_get_issue(DOMAIN, ignored_issue_id)
    assert ignored_issue is not None
    assert ignored_issue.dismissed_version is not None
