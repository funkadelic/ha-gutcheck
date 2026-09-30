"""Timeline tests for the still-failing card over the captured coway entry."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import (
    ATTR_UNSURE,
    CONFIG_ENTRY_ISSUE_PREFIX,
    DOMAIN,
    ISSUE_CONFIG_ENTRY_DEAD,
    ISSUE_CONFIG_ENTRY_STILL_FAILING,
)
from custom_components.gutcheck.recipes.config_entry_const import (
    CONFIG_ENTRY_OPTIONS,
    CONFIG_ENTRY_UNSURE_CARD_AFTER,
    OPTION_DEAD,
)

from .conftest import (
    api_response,
    area_answer,
    load_captured,
    posted_bodies,
    press_triage_run,
    register_jev_responses,
    restart_config_entry,
    triage_sensor_entity_id,
)

ISSUE_ID = f"{CONFIG_ENTRY_ISSUE_PREFIX}coway_entry"


async def _coway(failing_entry: Any, *later: Exception | None) -> tuple[MockConfigEntry, dict[str, Any]]:
    """The captured stuck entry: not ready with the captured reason, then any later outcomes."""
    item = load_captured("config_entry")[0][0]["state"]["entries"][0]
    entry = await failing_entry(
        item["integration"], ConfigEntryNotReady(item["reason"]), *later, title="Coway", entry_id="coway_entry"
    )
    assert entry.state is ConfigEntryState.SETUP_RETRY
    return entry, item


def _sensor_state(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    """The stuck integration check sensor's state."""
    state = hass.states.get(triage_sensor_entity_id(hass, entry))
    assert state is not None
    return state.state


async def test_a_week_unsure_entry_raises_its_card_and_it_survives_a_restart_and_clears_on_load(
    hass: HomeAssistant,
    freezer: Any,
    aioclient_mock: AiohttpClientMocker,
    triage_entry: MockConfigEntry,
    failing_entry: Any,
) -> None:
    """Week-long unsure raises the card; restart keeps it with no POST; the entry loading clears it.

    The second run reuses the real first-run answer, the only one captured.
    """
    freezer.move_to("2026-01-01T00:00:00-08:00")
    coway, item = await _coway(failing_entry, ConfigEntryNotReady("still down"), None)
    payloads, responses = load_captured("config_entry")
    register_jev_responses(aioclient_mock, [responses[0], responses[0]])
    triage_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(triage_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert posted_bodies(aioclient_mock) == payloads
    assert not [i for (domain, i) in ir.async_get(hass).issues if domain == DOMAIN]
    assert _sensor_state(hass, triage_entry) == "0"
    state = hass.states.get(triage_sensor_entity_id(hass, triage_entry))
    assert state is not None
    assert state.attributes[ATTR_UNSURE][0]["reason"] == item["reason"]

    freezer.tick(CONFIG_ENTRY_UNSURE_CARD_AFTER)
    await press_triage_run(hass, triage_entry)

    assert posted_bodies(aioclient_mock)[1]["state"]["entries"][0]["failing_for"] == "longer than 1 week"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID)
    assert issue is not None
    assert not issue.is_fixable
    assert issue.translation_key == ISSUE_CONFIG_ENTRY_STILL_FAILING
    assert issue.learn_more_url == f"/config/integrations/integration/{item['integration']}"
    assert issue.translation_placeholders == {"title": "Coway", "integration": item["integration"], "reason": item["reason"]}
    assert _sensor_state(hass, triage_entry) == "1"

    await restart_config_entry(hass, triage_entry)
    assert len(posted_bodies(aioclient_mock)) == 2
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is not None
    assert _sensor_state(hass, triage_entry) == "1"

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
    await hass.async_block_till_done(wait_background_tasks=True)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=60))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert coway.state is ConfigEntryState.LOADED
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None
    assert _sensor_state(hass, triage_entry) == "0"


async def test_the_card_hands_its_id_to_a_verdict_and_a_dismissal_follows_the_entry_across_kinds(
    hass: HomeAssistant,
    freezer: Any,
    aioclient_mock: AiohttpClientMocker,
    triage_entry: MockConfigEntry,
    failing_entry: Any,
) -> None:
    """Unsure card, then a dead verdict on the same id, then ignore, then unsure again stays hidden."""
    freezer.move_to("2026-01-01T00:00:00-08:00")
    await _coway(failing_entry)
    captured = load_captured("config_entry")[1][0]
    dead = api_response({"c0": area_answer(OPTION_DEAD, 0.9, CONFIG_ENTRY_OPTIONS)})
    register_jev_responses(aioclient_mock, [captured, captured, dead, captured])
    triage_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(triage_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    freezer.tick(CONFIG_ENTRY_UNSURE_CARD_AFTER)
    await press_triage_run(hass, triage_entry)
    registry = ir.async_get(hass)
    issue = registry.async_get_issue(DOMAIN, ISSUE_ID)
    assert issue is not None
    assert issue.translation_key == ISSUE_CONFIG_ENTRY_STILL_FAILING

    await press_triage_run(hass, triage_entry)
    issue = registry.async_get_issue(DOMAIN, ISSUE_ID)
    assert issue is not None
    assert issue.translation_key == ISSUE_CONFIG_ENTRY_DEAD
    assert set(issue.translation_placeholders or {}) == {"title", "integration"}

    ir.async_ignore_issue(hass, DOMAIN, ISSUE_ID, True)
    assert _sensor_state(hass, triage_entry) == "0"

    await press_triage_run(hass, triage_entry)
    issue = registry.async_get_issue(DOMAIN, ISSUE_ID)
    assert issue is not None
    assert issue.translation_key == ISSUE_CONFIG_ENTRY_STILL_FAILING
    assert issue.dismissed_version is not None
    assert _sensor_state(hass, triage_entry) == "0"
