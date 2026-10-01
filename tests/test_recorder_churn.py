"""Timeline and count tests for recorder suggestions, against a real recorder database."""

from __future__ import annotations

import json
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import (
    ATTR_COUNTS,
    ATTR_ITEMS,
    CONF_AREAS_ENABLED,
    CONF_CONFIG_ENTRIES_ENABLED,
    CONF_CRITICAL_LABEL_ENABLED,
    CONF_DAILY_BUDGET,
    CONF_DEVICE_CLASS_ENABLED,
    CONF_HEALTH_ENABLED,
    CONF_HIDE_DIAGNOSTIC_ENABLED,
    CONF_RECORDER_CHURN_ENABLED,
    CONF_UPDATES_ENABLED,
    DEFAULT_DAILY_BUDGET,
    DOMAIN,
    ISSUE_RECORDER_EXCLUDE_SUGGESTION,
    OPTION_NONE,
    RECIPE_RECORDER_CHURN,
    RECORDER_CHURN_ISSUE_PREFIX,
)
from custom_components.gutcheck.recipes.recorder_churn_const import (
    RECORDER_CHURN_INSTRUCTIONS,
    RECORDER_FILTER_DOCS_URL,
)

from .conftest import (
    api_response,
    find_recipe_sensor,
    posted_bodies,
    recorder_churn_answer,
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
    CONF_RECORDER_CHURN_ENABLED: False,
    CONF_DAILY_BUDGET: DEFAULT_DAILY_BUDGET,
}


def _write_states(hass: HomeAssistant, entity_id: str, count: int) -> None:
    """Write count distinct states for one entity, each a real state change the recorder keeps."""
    for value in range(count):
        hass.states.async_set(entity_id, str(value))


@pytest.mark.parametrize("recorder_config", [{"purge_keep_days": 1}])
async def test_heavy_writer_is_counted_asked_about_carded_and_restored_without_a_second_request(
    recorder_mock: Any,
    enable_custom_integrations: None,
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hass_storage: dict,
) -> None:
    """Switch on, count the real database, ask once, card the exclude answer, then restart with no new request."""
    heavy = register_unit_sensor(
        hass,
        "co2_living",
        unit="ppm",
        name="Living room CO2",
        original_device_class="carbon_dioxide",
        state_class="measurement",
        device_name="Air monitor",
        manufacturer="Govee",
        model="H5140",
    )
    quiet = register_unit_sensor(hass, "quiet_temp", unit="°C", name="Hall temperature")
    _write_states(hass, heavy.entity_id, 1_200)
    _write_states(hass, quiet.entity_id, 3)
    await async_wait_recording_done(hass)

    entry = MockConfigEntry(domain=DOMAIN, data={"api_key": "test-key"}, options=_OFF_OPTIONS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert find_recipe_sensor(hass, entry, RECIPE_RECORDER_CHURN) is None

    register_jev_responses_by_question(aioclient_mock, {"r0": api_response({"r0": recorder_churn_answer("exclude", 0.9)})})
    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(result["flow_id"], {**_OFF_OPTIONS, CONF_RECORDER_CHURN_ENABLED: True})
    await hass.async_block_till_done(wait_background_tasks=True)

    bodies = posted_bodies(aioclient_mock)
    assert len(bodies) == 1
    assert bodies[0]["state"]["entities"] == [
        {
            "name": "Living room CO2",
            "device_name": "Air monitor",
            "manufacturer": "Govee",
            "model": "H5140",
            "domain": "sensor",
            "integration": "test",
            "device_class": "carbon_dioxide",
            "unit": "ppm",
            "long_term_statistics": True,
            "churn": "heavy",
        }
    ]
    serialized = json.dumps(bodies[0])
    assert heavy.entity_id not in serialized
    assert quiet.entity_id not in serialized
    assert "1200" not in serialized
    question = bodies[0]["questions"]["r0"]
    assert question["type"] == "choice"
    assert question["instructions"] == RECORDER_CHURN_INSTRUCTIONS.format(index=0)
    assert set(question["criteria"]) == {"exclude", "throttle", "keep", OPTION_NONE}

    sensor_id = find_recipe_sensor(hass, entry, RECIPE_RECORDER_CHURN)
    assert sensor_id is not None
    state = hass.states.get(sensor_id)
    assert state is not None
    assert state.state == "1"
    assert state.attributes[ATTR_COUNTS]["exclude"] == 1
    exclude_item = state.attributes[ATTR_ITEMS]["exclude"][0]
    assert (exclude_item["changes_per_day"], exclude_item["bucket"]) == (1_200, "heavy")

    issue_id = f"{RECORDER_CHURN_ISSUE_PREFIX}{heavy.id}"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.active
    assert not issue.is_fixable
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.translation_key == ISSUE_RECORDER_EXCLUDE_SUGGESTION
    assert issue.translation_placeholders == {"entity_id": heavy.entity_id, "bucket": "heavy"}
    assert issue.learn_more_url == RECORDER_FILTER_DOCS_URL

    await restart_config_entry(hass, entry)
    assert len(posted_bodies(aioclient_mock)) == 1
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.active
    restored = hass.states.get(sensor_id)
    assert restored is not None
    assert restored.state == "1"
