"""Timeline test: a run that stops suggesting a sensor clears its open card, keeps an ignored one, across a restart."""

from __future__ import annotations

import json
from pathlib import Path
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
from custom_components.gutcheck.recipes.device_class_describe import candidate_classes

from .conftest import (
    api_response,
    area_answer,
    device_class_sensor_entity_id,
    posted_bodies,
    register_jev_responses,
    register_unit_sensor,
)

FIXTURES = Path(__file__).parent / "fixtures" / "captured"


def _load(name: str) -> dict[str, Any]:
    """The captured fixture JSON at `name`, parsed."""
    return json.loads((FIXTURES / name).read_text())


def _captured(payload: dict[str, Any], response: dict[str, Any], sensor_name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """The captured sensor fields and the model's own real answer for the sensor named sensor_name."""
    sensors = payload["state"]["sensors"]
    index = next(i for i, sensor in enumerate(sensors) if sensor["name"] == sensor_name)
    question_id = list(payload["questions"])[index]
    return sensors[index], response["answers"][question_id]


def _register(hass: HomeAssistant, unique: str, fields: dict[str, Any]) -> er.RegistryEntry:
    """Register a sensor carrying one captured sensor's own unit, name and device fields."""
    entity_category = er.EntityCategory(fields["entity_category"]) if fields["entity_category"] else None
    return register_unit_sensor(
        hass,
        unique,
        unit=fields["unit"],
        name=fields["name"],
        platform=fields["integration"],
        entity_category=entity_category,
        device_name=fields["device_name"],
        manufacturer=fields["manufacturer"],
        model=fields["model"],
    )


async def _restart(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Unload, reload the issue registry from storage the way HA startup does, then set up again."""
    assert await hass.config_entries.async_unload(entry.entry_id)
    registry = ir.async_get(hass)
    await flush_store(registry._store)
    await ir.async_load(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_a_run_that_stops_suggesting_clears_the_open_card_keeps_the_ignored_one_and_both_survive_a_restart(
    hass: HomeAssistant, freezer: Any, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """Real captured answers clear the water filter card, keep the ignored filter lifespan card, both surviving a restart."""
    freezer.move_to("2026-01-01T00:00:00-08:00")
    payload = _load("device_class_payload.json")
    response = _load("device_class_response.json")
    water_fields, water_answer = _captured(payload, response, "Water filter used")
    lifespan_fields, lifespan_answer = _captured(payload, response, "Filter lifespan")
    co2_fields, co2_answer = _captured(payload, response, "Foobot HappyBot CO2")

    water = _register(hass, "water", water_fields)
    lifespan = _register(hass, "lifespan", lifespan_fields)
    co2 = _register(hass, "co2", co2_fields)
    ordered = sorted((water, lifespan, co2), key=lambda entry: entry.entity_id)

    # Run 1: synthetic confident answers stand in for the earlier live run that raised all three cards.
    run1_choice_and_candidates = {
        water.id: ("distance", candidate_classes(water_fields["unit"])),
        lifespan.id: ("humidity", candidate_classes(lifespan_fields["unit"])),
        co2.id: ("carbon_dioxide", candidate_classes(co2_fields["unit"])),
    }
    run1_answers = {
        f"s{index}": area_answer(run1_choice_and_candidates[entry.id][0], 0.9, run1_choice_and_candidates[entry.id][1])
        for index, entry in enumerate(ordered)
    }
    # Run 2: the three real captured answers, remapped onto this run's own question ids.
    captured_answer_by_id = {water.id: water_answer, lifespan.id: lifespan_answer, co2.id: co2_answer}
    run2_answers = {f"s{index}": captured_answer_by_id[entry.id] for index, entry in enumerate(ordered)}
    register_jev_responses(aioclient_mock, [api_response(run1_answers), api_response(run2_answers)])

    device_class_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(device_class_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    water_issue_id = f"{DEVICE_CLASS_ISSUE_PREFIX}{water.id}"
    lifespan_issue_id = f"{DEVICE_CLASS_ISSUE_PREFIX}{lifespan.id}"
    co2_issue_id = f"{DEVICE_CLASS_ISSUE_PREFIX}{co2.id}"
    registry = ir.async_get(hass)
    for issue_id in (water_issue_id, lifespan_issue_id, co2_issue_id):
        issue = registry.async_get_issue(DOMAIN, issue_id)
        assert issue is not None
        assert issue.dismissed_version is None

    ir.async_ignore_issue(hass, DOMAIN, lifespan_issue_id, True)
    before_lifespan = ir.async_get(hass).async_get_issue(DOMAIN, lifespan_issue_id)
    assert before_lifespan is not None

    await device_class_entry.runtime_data.coordinators[RECIPE_DEVICE_CLASS].async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)

    assert ir.async_get(hass).async_get_issue(DOMAIN, water_issue_id) is None

    after_run2_lifespan = ir.async_get(hass).async_get_issue(DOMAIN, lifespan_issue_id)
    assert after_run2_lifespan is not None
    assert after_run2_lifespan.dismissed_version is not None
    assert after_run2_lifespan.translation_placeholders == before_lifespan.translation_placeholders
    assert after_run2_lifespan.data == before_lifespan.data

    after_run2_co2 = ir.async_get(hass).async_get_issue(DOMAIN, co2_issue_id)
    assert after_run2_co2 is not None
    assert after_run2_co2.active
    assert after_run2_co2.dismissed_version is None
    assert after_run2_co2.data is not None
    assert after_run2_co2.data["device_class"] == "carbon_dioxide"

    sensor_entity_id = device_class_sensor_entity_id(hass, device_class_entry)
    state = hass.states.get(sensor_entity_id)
    assert state is not None
    assert state.state == "1"
    assert {item["registry_id"] for item in state.attributes["unsure"]} == {water.id, lifespan.id}

    before_restart_calls = len(posted_bodies(aioclient_mock))

    freezer.move_to("2026-01-04T00:00:00-08:00")
    await _restart(hass, device_class_entry)

    assert len(posted_bodies(aioclient_mock)) == before_restart_calls
    assert ir.async_get(hass).async_get_issue(DOMAIN, water_issue_id) is None

    after_restart_lifespan = ir.async_get(hass).async_get_issue(DOMAIN, lifespan_issue_id)
    assert after_restart_lifespan is not None
    assert after_restart_lifespan.dismissed_version is not None
    assert after_restart_lifespan.translation_placeholders == before_lifespan.translation_placeholders
    assert after_restart_lifespan.data == before_lifespan.data

    after_restart_co2 = ir.async_get(hass).async_get_issue(DOMAIN, co2_issue_id)
    assert after_restart_co2 is not None
    assert after_restart_co2.active
    assert after_restart_co2.dismissed_version is None
    assert after_restart_co2.translation_placeholders == after_run2_co2.translation_placeholders
    assert after_restart_co2.data == after_run2_co2.data

    state_after_restart = hass.states.get(sensor_entity_id)
    assert state_after_restart is not None
    assert state_after_restart.state == "1"

    assert await async_setup_component(hass, "repairs", {})
    manager = repairs_flow_manager(hass)
    assert manager is not None
    result = await manager.async_init(DOMAIN, data={"issue_id": co2_issue_id})
    assert result["type"] is FlowResultType.MENU
    result = await manager.async_configure(result["flow_id"], {"next_step_id": "confirm"})
    assert result["type"] is FlowResultType.CREATE_ENTRY

    updated = er.async_get(hass).async_get(co2.entity_id)
    assert updated is not None
    assert updated.device_class == "carbon_dioxide"
