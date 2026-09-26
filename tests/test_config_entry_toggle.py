"""Timeline tests: switching the stuck integration check on and off, running on demand, and removing the entry."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import (
    CONF_CONFIG_ENTRIES_ENABLED,
    CONF_DAILY_BUDGET,
    CONFIG_ENTRY_ISSUE_PREFIX,
    DEFAULT_DAILY_BUDGET,
    DOMAIN,
    RECIPE_CONFIG_ENTRIES,
    STORE_VERSION,
)
from custom_components.gutcheck.recipes.config_entry_const import CONFIG_ENTRY_OPTIONS, OPTION_DEAD
from custom_components.gutcheck.recipes.shapes import recipe_store_key

from .conftest import api_response, area_answer, find_triage_button, find_triage_sensor, posted_bodies, register_jev_responses


def _triage_issue_ids(hass: HomeAssistant) -> set[str]:
    """Every stuck integration card currently in the issue registry."""
    return {
        issue_id
        for domain, issue_id in ir.async_get(hass).issues
        if domain == DOMAIN and issue_id.startswith(CONFIG_ENTRY_ISSUE_PREFIX)
    }


async def _switch_config_entries(hass: HomeAssistant, entry: MockConfigEntry, enabled: bool) -> None:
    """Turn the stuck integration check on or off through the options flow."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_CONFIG_ENTRIES_ENABLED: enabled, CONF_DAILY_BUDGET: DEFAULT_DAILY_BUDGET}
    )
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_off_by_default_then_disable_reenable_run_and_removal_timeline(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    failing_entry: Any,
) -> None:
    """Off by default; enabling raises cards; disable clears them like the update review; re-enable, run and removal follow."""
    open_entry = await failing_entry("open_hub", ConfigEntryError("device offline"), title="Open Hub", entry_id="open_entry")
    ignored_entry = await failing_entry(
        "ignored_hub", ConfigEntryError("device offline"), title="Ignored Hub", entry_id="ignored_entry"
    )
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert find_triage_sensor(hass, mock_config_entry) is None
    assert find_triage_button(hass, mock_config_entry) is None
    assert posted_bodies(aioclient_mock) == []

    both_dead = api_response(
        {"c0": area_answer(OPTION_DEAD, 0.9, CONFIG_ENTRY_OPTIONS), "c1": area_answer(OPTION_DEAD, 0.9, CONFIG_ENTRY_OPTIONS)}
    )
    register_jev_responses(aioclient_mock, [both_dead])
    await _switch_config_entries(hass, mock_config_entry, True)

    assert find_triage_sensor(hass, mock_config_entry) is not None
    bodies = posted_bodies(aioclient_mock)
    assert len(bodies) == 1
    assert next(iter(bodies[0]["questions"])) == "c0"

    open_issue_id = f"{CONFIG_ENTRY_ISSUE_PREFIX}{open_entry.entry_id}"
    ignored_issue_id = f"{CONFIG_ENTRY_ISSUE_PREFIX}{ignored_entry.entry_id}"
    assert _triage_issue_ids(hass) == {open_issue_id, ignored_issue_id}
    ir.async_ignore_issue(hass, DOMAIN, ignored_issue_id, True)

    # Pressing Run stuck integration check posts one more request, first question id c0.
    button_entity_id = find_triage_button(hass, mock_config_entry)
    assert button_entity_id is not None
    aioclient_mock.clear_requests()
    register_jev_responses(aioclient_mock, [both_dead])
    await hass.services.async_call("button", "press", {"entity_id": button_entity_id}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)

    bodies = posted_bodies(aioclient_mock)
    assert len(bodies) == 1
    assert next(iter(bodies[0]["questions"])) == "c0"

    # Disable: these are advisory cards like the update review's, so both go, nothing is posted.
    posted_before = len(posted_bodies(aioclient_mock))
    await _switch_config_entries(hass, mock_config_entry, False)

    assert find_triage_sensor(hass, mock_config_entry) is None
    assert find_triage_button(hass, mock_config_entry) is None
    assert _triage_issue_ids(hass) == set()
    assert len(posted_bodies(aioclient_mock)) == posted_before

    # Re-enable within the cadence window: restored from the Store, no POST, the open card is back.
    posted_before = len(posted_bodies(aioclient_mock))
    await _switch_config_entries(hass, mock_config_entry, True)

    assert find_triage_sensor(hass, mock_config_entry) is not None
    assert len(posted_bodies(aioclient_mock)) == posted_before
    assert _triage_issue_ids(hass) == {open_issue_id, ignored_issue_id}

    # Removing the entry deletes every config_entry_ card and its Store.
    await hass.config_entries.async_remove(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert _triage_issue_ids(hass) == set()
    stored = await Store(hass, STORE_VERSION, recipe_store_key(RECIPE_CONFIG_ENTRIES)).async_load()
    assert stored is None
