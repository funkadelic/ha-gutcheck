"""Timeline tests for the update recipe's Run button: what a forced re-score asks again and what it keeps."""

from __future__ import annotations

from typing import Any

from homeassistant.const import CONF_API_KEY, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import DOMAIN, OPTION_POSSIBLY_BREAKING, RECIPE_UPDATES, UPDATES_ISSUE_PREFIX
from custom_components.gutcheck.recipes.update_const import MAX_UPDATES_PER_RUN

from .conftest import api_response, posted_bodies, register_jev_responses, register_pending_update, score_answer


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    """Set up a Gut Check entry with a test API key."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: "test-key"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


def _button(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    """The update recipe's Run button entity id."""
    entity_id = er.async_get(hass).async_get_entity_id("button", DOMAIN, f"{entry.entry_id}_{RECIPE_UPDATES}_run")
    assert entity_id is not None
    return entity_id


async def _press(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Press the update recipe's Run button and let its run finish."""
    await hass.services.async_call("button", "press", {"entity_id": _button(hass, entry)}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)


def _sensor_state(hass: HomeAssistant, entry: MockConfigEntry) -> Any:
    """The update recipe's sensor state object."""
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{RECIPE_UPDATES}")
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    return state


async def test_a_press_keeps_an_unavailable_updates_classification_and_card(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An update that is unavailable when Run is pressed cannot be asked about, so it keeps what it had."""
    update_entry = register_pending_update(hass, "update_a")
    register_jev_responses(aioclient_mock, [api_response({"u0": score_answer(2, 0.9)})])
    entry = await _setup(hass)
    issue_id = f"{UPDATES_ISSUE_PREFIX}{update_entry.id}"
    classified_before = _sensor_state(hass, entry).attributes["items"][OPTION_POSSIBLY_BREAKING][0]

    hass.states.async_set(update_entry.entity_id, STATE_UNAVAILABLE)
    aioclient_mock.clear_requests()
    await _press(hass, entry)

    assert posted_bodies(aioclient_mock) == []
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    assert _sensor_state(hass, entry).attributes["items"][OPTION_POSSIBLY_BREAKING] == [classified_before]


async def test_a_press_keeps_a_cap_deferred_updates_classification_and_card(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An update pushed past the cap on a forced run keeps its prior classification and card."""
    deferred = register_pending_update(hass, "upd_0000", installed_version="1.0.0", latest_version="1.0.1")
    register_jev_responses(aioclient_mock, [api_response({"u0": score_answer(2, 0.9)})])
    entry = await _setup(hass)
    issue_id = f"{UPDATES_ISSUE_PREFIX}{deferred.id}"
    classified_before = _sensor_state(hass, entry).attributes["items"][OPTION_POSSIBLY_BREAKING][0]

    for index in range(1, MAX_UPDATES_PER_RUN + 1):
        register_pending_update(hass, f"upd_{index:04d}", installed_version="1.0.0", latest_version="2.0.0")
    aioclient_mock.clear_requests()
    register_jev_responses(aioclient_mock, [api_response({f"u{i}": score_answer(0, 0.9) for i in range(MAX_UPDATES_PER_RUN)})])
    await _press(hass, entry)

    assert len(posted_bodies(aioclient_mock)[0]["questions"]) == MAX_UPDATES_PER_RUN
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    assert _sensor_state(hass, entry).attributes["items"][OPTION_POSSIBLY_BREAKING] == [classified_before]


async def test_a_failed_forced_run_leaves_the_next_run_forced(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A press whose run fails still re-asks an unchanged, accepted update on the next run."""
    register_pending_update(hass, "update_a")
    register_jev_responses(aioclient_mock, [api_response({"u0": score_answer(0, 0.9)})])
    entry = await _setup(hass)

    aioclient_mock.clear_requests()
    register_jev_responses(aioclient_mock, [(500, {"error": "boom"})])
    await _press(hass, entry)
    assert _sensor_state(hass, entry).state == STATE_UNAVAILABLE

    aioclient_mock.clear_requests()
    register_jev_responses(aioclient_mock, [api_response({"u0": score_answer(0, 0.9)})])
    await entry.runtime_data.coordinators[RECIPE_UPDATES].async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)

    bodies = posted_bodies(aioclient_mock)
    assert len(bodies) == 1
    assert len(bodies[0]["questions"]) == 1
