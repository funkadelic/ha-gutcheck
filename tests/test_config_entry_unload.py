"""A carded entry that is only unloaded keeps its card until the next run."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import CONFIG_ENTRY_ISSUE_PREFIX, DOMAIN, RECIPE_CONFIG_ENTRIES
from custom_components.gutcheck.recipes.config_entry_const import CONFIG_ENTRY_OPTIONS, OPTION_DEAD

from .conftest import api_response, area_answer, posted_bodies, register_jev_responses


async def test_unloaded_entry_keeps_its_card_until_the_next_run_drops_it(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    triage_entry: MockConfigEntry,
    failing_entry: Any,
) -> None:
    """The live listener leaves the card after an unload, and the next run clears it."""
    entry = await failing_entry("unload_hub", ConfigEntryError("device offline"), title="Unload Hub", entry_id="unload_entry")

    register_jev_responses(aioclient_mock, [api_response({"c0": area_answer(OPTION_DEAD, 0.9, CONFIG_ENTRY_OPTIONS)})])
    triage_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(triage_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    registry = ir.async_get(hass)
    issue_id = f"{CONFIG_ENTRY_ISSUE_PREFIX}{entry.entry_id}"
    assert registry.async_get_issue(DOMAIN, issue_id) is not None

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert registry.async_get_issue(DOMAIN, issue_id) is not None

    button = er.async_get(hass).async_get_entity_id("button", DOMAIN, f"{triage_entry.entry_id}_{RECIPE_CONFIG_ENTRIES}_run")
    await hass.services.async_call("button", "press", {"entity_id": button}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert registry.async_get_issue(DOMAIN, issue_id) is None
    assert len(posted_bodies(aioclient_mock)) == 1
