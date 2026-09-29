"""Timeline test: a classless phone sensor is asked about, gets a card, and confirming hides it for good."""

from __future__ import annotations

import json

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import (
    ATTR_COUNTS,
    CONF_AREAS_ENABLED,
    CONF_CONFIG_ENTRIES_ENABLED,
    CONF_CRITICAL_LABEL_ENABLED,
    CONF_DAILY_BUDGET,
    CONF_DEVICE_CLASS_ENABLED,
    CONF_HEALTH_ENABLED,
    CONF_HIDE_DIAGNOSTIC_ENABLED,
    CONF_UPDATES_ENABLED,
    DEFAULT_DAILY_BUDGET,
    DEVICE_CLASS_APPLIED_STORE_KEY,
    DOMAIN,
    HIDE_DIAGNOSTIC_APPLIED_STORE_KEY,
    HIDE_DIAGNOSTIC_ISSUE_PREFIX,
    ISSUE_HIDE_DIAGNOSTIC_SUGGESTION,
    OPTION_NONE,
    RECIPE_HIDE_DIAGNOSTIC,
)
from custom_components.gutcheck.recipes.hide_diagnostic_const import HIDE_DIAGNOSTIC_INSTRUCTIONS

from .conftest import (
    api_response,
    confirm_suggestion_card,
    find_recipe_sensor,
    hide_diagnostic_answer,
    posted_bodies,
    press_recipe_run,
    register_jev_responses_by_question,
    register_unit_sensor,
    restart_config_entry,
)

_OFF_OPTIONS = {
    CONF_HEALTH_ENABLED: False,
    CONF_UPDATES_ENABLED: False,
    CONF_AREAS_ENABLED: False,
    CONF_DEVICE_CLASS_ENABLED: False,
    CONF_CONFIG_ENTRIES_ENABLED: False,
    CONF_CRITICAL_LABEL_ENABLED: False,
    CONF_HIDE_DIAGNOSTIC_ENABLED: False,
    CONF_DAILY_BUDGET: DEFAULT_DAILY_BUDGET,
}


def _card_count(hass: HomeAssistant, issue_id: str) -> int:
    """How many hide cards exist under this id (0 or 1)."""
    return int(ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None)


async def test_asked_sensor_gets_a_card_that_hides_it_for_good(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hass_storage: dict
) -> None:
    """Switch on, ask, card, restart, confirm hides and records, re-add keeps it hidden, a rerun asks nothing."""
    sensor = register_unit_sensor(
        hass, "phone_wifi", unit=None, name="Wi-Fi connection", device_name="Pixel 9", manufacturer="Google", model="Pixel 9"
    )
    hass.states.async_set(sensor.entity_id, "HomeNet-5G")
    entry = MockConfigEntry(domain=DOMAIN, data={"api_key": "test-key"}, options=_OFF_OPTIONS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert find_recipe_sensor(hass, entry, RECIPE_HIDE_DIAGNOSTIC) is None

    register_jev_responses_by_question(aioclient_mock, {"h0": api_response({"h0": hide_diagnostic_answer("diagnostic", 0.9)})})
    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(result["flow_id"], {**_OFF_OPTIONS, CONF_HIDE_DIAGNOSTIC_ENABLED: True})
    await hass.async_block_till_done(wait_background_tasks=True)

    bodies = posted_bodies(aioclient_mock)
    assert len(bodies) == 1
    assert bodies[0]["state"]["sensors"] == [
        {
            "name": "Wi-Fi connection",
            "device_name": "Pixel 9",
            "manufacturer": "Google",
            "model": "Pixel 9",
            "integration": "test",
            "unit": None,
            "state_class": None,
        }
    ]
    serialized = json.dumps(bodies[0])
    assert "HomeNet-5G" not in serialized
    assert sensor.entity_id not in serialized
    question = bodies[0]["questions"]["h0"]
    assert question["type"] == "choice"
    assert question["instructions"] == HIDE_DIAGNOSTIC_INSTRUCTIONS.format(index=0)
    assert set(question["criteria"]) == {"diagnostic", "primary", OPTION_NONE}

    sensor_id = find_recipe_sensor(hass, entry, RECIPE_HIDE_DIAGNOSTIC)
    assert sensor_id is not None
    state = hass.states.get(sensor_id)
    assert state is not None
    assert state.state == "1"
    assert state.attributes[ATTR_COUNTS]["suggested"] == 1
    issue_id = f"{HIDE_DIAGNOSTIC_ISSUE_PREFIX}{sensor.id}"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.is_fixable
    assert issue.translation_key == ISSUE_HIDE_DIAGNOSTIC_SUGGESTION
    assert issue.translation_placeholders == {"entity_id": sensor.entity_id}
    assert issue.data == {"registry_id": sensor.id}

    await restart_config_entry(hass, entry)
    assert len(posted_bodies(aioclient_mock)) == 1
    assert _card_count(hass, issue_id) == 1

    await confirm_suggestion_card(hass, issue_id)
    registry = er.async_get(hass)
    hidden = registry.async_get(sensor.entity_id)
    assert hidden is not None
    assert hidden.hidden_by is er.RegistryEntryHider.USER
    assert (hidden.entity_category, hidden.device_class, hidden.labels, hidden.original_name) == (
        sensor.entity_category,
        sensor.device_class,
        sensor.labels,
        sensor.original_name,
    )
    assert hidden.name == sensor.name
    assert _card_count(hass, issue_id) == 0
    state = hass.states.get(sensor_id)
    assert state is not None
    assert state.state == "0"
    assert entry.runtime_data.applied_hidden.get(sensor.id) == "user"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass_storage[HIDE_DIAGNOSTIC_APPLIED_STORE_KEY]["data"] == {sensor.id: "user"}
    assert hass_storage[DEVICE_CLASS_APPLIED_STORE_KEY]["data"] == {}

    # The owning integration re-adding the entity, as on every reload, leaves the hide in place.
    registry.async_get_or_create("sensor", "test", "phone_wifi", device_id=sensor.device_id, original_name="Wi-Fi connection")
    reloaded = registry.async_get(sensor.entity_id)
    assert reloaded is not None
    assert reloaded.hidden_by is er.RegistryEntryHider.USER

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.runtime_data.applied_hidden.get(sensor.id) == "user"
    await press_recipe_run(hass, entry, RECIPE_HIDE_DIAGNOSTIC)
    assert len(posted_bodies(aioclient_mock)) == 1
    assert _card_count(hass, issue_id) == 0
