"""Timeline test: confident and unsure-kept device class cards survive a real restart."""

from __future__ import annotations

from typing import Any

from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry, flush_store
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import DEVICE_CLASS_ISSUE_PREFIX, DOMAIN, RECIPE_DEVICE_CLASS

from .conftest import (
    api_response,
    area_answer,
    device_class_sensor_entity_id,
    register_jev_responses,
    register_unit_sensor,
)

BATTERY_CANDIDATES = ["battery", "humidity", "moisture", "power_factor"]


async def _restart(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Unload, reload the issue registry from storage the way HA startup does, then set up again."""
    assert await hass.config_entries.async_unload(entry.entry_id)
    registry = ir.async_get(hass)
    await flush_store(registry._store)
    await ir.async_load(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_confident_and_kept_cards_survive_a_restart_and_the_confident_one_still_confirms(
    hass: HomeAssistant, freezer: Any, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """A confident answer and a card kept through an unsure run both survive a restart."""
    freezer.move_to("2026-01-01T00:00:00-08:00")
    confident_sensor = register_unit_sensor(hass, "battery_pct", unit="%", name="Battery")
    kept_sensor = register_unit_sensor(hass, "kept_pct", unit="%", name="Kept")
    run1_answer = area_answer("battery", 0.9, BATTERY_CANDIDATES)
    register_jev_responses(aioclient_mock, [api_response({"s0": run1_answer, "s1": run1_answer})])
    device_class_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(device_class_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    confident_issue_id = f"{DEVICE_CLASS_ISSUE_PREFIX}{confident_sensor.id}"
    kept_issue_id = f"{DEVICE_CLASS_ISSUE_PREFIX}{kept_sensor.id}"
    for issue_id in (confident_issue_id, kept_issue_id):
        assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    # Run 2: kept_sensor now answers unsure. Its card is attempted-but-unsure, not resolved, so it is
    # kept unchanged rather than swept; confident_sensor stays confident.
    ordered = sorted((confident_sensor, kept_sensor), key=lambda entry: entry.entity_id)
    run2_answers = {
        f"s{index}": (
            area_answer("battery", 0.9, BATTERY_CANDIDATES)
            if entry.id == confident_sensor.id
            else area_answer("battery", 0.2, BATTERY_CANDIDATES)
        )
        for index, entry in enumerate(ordered)
    }
    aioclient_mock.clear_requests()
    register_jev_responses(aioclient_mock, [api_response(run2_answers)])
    await device_class_entry.runtime_data.coordinators[RECIPE_DEVICE_CLASS].async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert ir.async_get(hass).async_get_issue(DOMAIN, kept_issue_id) is not None

    before_confident = ir.async_get(hass).async_get_issue(DOMAIN, confident_issue_id)
    before_kept = ir.async_get(hass).async_get_issue(DOMAIN, kept_issue_id)
    assert before_confident is not None
    assert before_kept is not None

    freezer.move_to("2026-01-04T00:00:00-08:00")
    await _restart(hass, device_class_entry)

    after_confident = ir.async_get(hass).async_get_issue(DOMAIN, confident_issue_id)
    assert after_confident is not None
    assert after_confident.active
    assert after_confident.dismissed_version is None
    assert after_confident.translation_placeholders == before_confident.translation_placeholders
    assert after_confident.data == before_confident.data

    after_kept = ir.async_get(hass).async_get_issue(DOMAIN, kept_issue_id)
    assert after_kept is not None
    assert after_kept.active
    assert after_kept.dismissed_version is None
    assert after_kept.translation_placeholders == before_kept.translation_placeholders
    assert after_kept.data == before_kept.data

    state = hass.states.get(device_class_sensor_entity_id(hass, device_class_entry))
    assert state is not None
    assert state.state == "2"

    assert await async_setup_component(hass, "repairs", {})
    manager = repairs_flow_manager(hass)
    assert manager is not None
    result = await manager.async_init(DOMAIN, data={"issue_id": confident_issue_id})
    assert result["type"] is FlowResultType.MENU
    result = await manager.async_configure(result["flow_id"], {"next_step_id": "confirm"})
    assert result["type"] is FlowResultType.CREATE_ENTRY

    updated = er.async_get(hass).async_get(confident_sensor.entity_id)
    assert updated is not None
    assert updated.device_class == "battery"
