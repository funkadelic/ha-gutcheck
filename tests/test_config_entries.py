"""End-to-end tracer: a stuck integration gets an advisory card, which clears when it loads."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import (
    CONFIG_ENTRY_INSTRUCTIONS,
    CONFIG_ENTRY_ISSUE_PREFIX,
    CONFIG_ENTRY_OPTIONS,
    DOMAIN,
    OPTION_NONE,
)

from .conftest import api_response, area_answer, posted_bodies, register_jev_responses, triage_sensor_entity_id


async def test_stuck_entry_gets_a_card_that_clears_when_it_loads(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    triage_entry: MockConfigEntry,
    failing_entry,
) -> None:
    """A setup_error entry is asked about once, a confident answer raises a card, and loading clears it."""
    entry = await failing_entry(
        "cloud_login",
        ConfigEntryError("Invalid credentials for suez water"),
        None,
        title="Cloud Login",
        entry_id="cloud_login_entry",
    )
    assert entry.state is ConfigEntryState.SETUP_ERROR

    register_jev_responses(aioclient_mock, [api_response({"c0": area_answer("needs_reauth", 0.9, CONFIG_ENTRY_OPTIONS)})])
    triage_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(triage_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    bodies = posted_bodies(aioclient_mock)
    assert len(bodies) == 1
    assert bodies[0]["state"]["entries"] == [
        {
            "integration": "cloud_login",
            "config_entry_state": "setup error",
            "reason": "Invalid credentials for suez water",
            "failing_for": "unknown",
        }
    ]
    question = bodies[0]["questions"]["c0"]
    assert question["type"] == "choice"
    assert question["instructions"] == CONFIG_ENTRY_INSTRUCTIONS.format(index=0)
    assert set(question["criteria"]) == {*CONFIG_ENTRY_OPTIONS, OPTION_NONE}

    sensor_id = triage_sensor_entity_id(hass, triage_entry)
    state = hass.states.get(sensor_id)
    assert state is not None
    assert state.state == "1"
    assert state.attributes["counts"]["needs_reauth"] == 1
    item = state.attributes["items"]["needs_reauth"][0]
    assert item["entry_id"] == entry.entry_id
    assert item["integration"] == "cloud_login"
    assert item["title"] == "Cloud Login"
    assert item["choice"] == "needs_reauth"
    assert item["confidence"] == 0.9

    issue_id = f"{CONFIG_ENTRY_ISSUE_PREFIX}{entry.entry_id}"
    registry = ir.async_get(hass)
    issue = registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.is_fixable is False
    assert issue.translation_key == "config_entry_needs_reauth"
    assert issue.learn_more_url == "/config/integrations/integration/cloud_login"
    assert issue.translation_placeholders == {"title": "Cloud Login", "integration": "cloud_login"}

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert entry.state is ConfigEntryState.LOADED
    assert registry.async_get_issue(DOMAIN, issue_id) is None
    state = hass.states.get(sensor_id)
    assert state is not None
    assert state.state == "0"
    assert len(posted_bodies(aioclient_mock)) == 1
