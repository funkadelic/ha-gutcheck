"""Tracer test: a water valve is asked about, gets a card, and confirming it adds the critical label."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import label_registry as lr
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import CRITICAL_LABEL_ISSUE_PREFIX, DOMAIN, OPTION_NONE, RECIPE_CRITICAL_LABEL
from custom_components.gutcheck.recipes.critical_label_const import (
    CRITICAL_LABEL_INSTRUCTIONS,
    OPTION_CRITICAL,
    OPTION_NOT_CRITICAL,
)

from .conftest import (
    api_response,
    confirm_suggestion_card,
    critical_label_answer,
    posted_bodies,
    press_recipe_run,
    recipe_sensor_entity_id,
    register_jev_responses_by_question,
    register_unit_sensor,
    restart_config_entry,
)


async def test_water_valve_gets_a_card_and_confirm_adds_the_label_keeping_other_labels(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    critical_label_entry: MockConfigEntry,
) -> None:
    """One valve, one confident critical answer: one card, surviving a restart, confirming adds the label."""
    lr.async_get(hass).async_create("Critical")
    lr.async_get(hass).async_create("Plumbing")
    valve = register_unit_sensor(
        hass,
        "water_valve",
        domain="valve",
        unit=None,
        name="Main water",
        original_device_class="water",
        device_name="Water main",
        manufacturer="Acme",
        model="V1",
        labels=frozenset({"plumbing"}),
    )

    register_jev_responses_by_question(aioclient_mock, {"k0": api_response({"k0": critical_label_answer(OPTION_CRITICAL, 0.9)})})
    critical_label_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(critical_label_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    bodies = posted_bodies(aioclient_mock)
    assert len(bodies) == 1
    body = bodies[0]
    assert body["state"]["entities"] == [
        {
            "domain": "valve",
            "device_class": "water",
            "name": "Main water",
            "device_name": "Water main",
            "manufacturer": "Acme",
            "model": "V1",
            "integration": "test",
            "entity_category": None,
        }
    ]
    question = body["questions"]["k0"]
    assert question["type"] == "choice"
    assert question["instructions"] == CRITICAL_LABEL_INSTRUCTIONS.format(index=0)
    assert set(question["criteria"].keys()) == {OPTION_CRITICAL, OPTION_NOT_CRITICAL, OPTION_NONE}

    state = hass.states.get(recipe_sensor_entity_id(hass, critical_label_entry, RECIPE_CRITICAL_LABEL))
    assert state is not None
    assert state.state == "1"
    assert state.attributes["counts"]["suggested"] == 1

    issue_id = f"{CRITICAL_LABEL_ISSUE_PREFIX}{valve.id}"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.is_fixable is True
    assert issue.translation_key == "critical_label_suggestion"
    assert issue.translation_placeholders == {"entity_id": valve.entity_id, "label_name": "Critical"}
    assert issue.data == {"registry_id": valve.id, "label_id": "critical"}

    await restart_config_entry(hass, critical_label_entry)
    assert len(posted_bodies(aioclient_mock)) == 1
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    await confirm_suggestion_card(hass, issue_id)

    updated = er.async_get(hass).async_get(valve.entity_id)
    assert updated is not None
    assert updated.labels == {"plumbing", "critical"}
    assert updated.device_id is not None
    device_entry = dr.async_get(hass).async_get(updated.device_id)
    assert device_entry is not None
    assert device_entry.labels == set()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None

    state = hass.states.get(recipe_sensor_entity_id(hass, critical_label_entry, RECIPE_CRITICAL_LABEL))
    assert state is not None
    assert state.state == "0"

    await press_recipe_run(hass, critical_label_entry, RECIPE_CRITICAL_LABEL)
    assert len(posted_bodies(aioclient_mock)) == 1
    assert not any(issue_id.startswith(CRITICAL_LABEL_ISSUE_PREFIX) for _domain, issue_id in ir.async_get(hass).issues)
