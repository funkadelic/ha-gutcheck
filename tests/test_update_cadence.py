"""Timeline tests for the update recipe's cadence: re-score only what changed, force on button press."""

from __future__ import annotations

from typing import Any

from homeassistant.const import CONF_API_KEY, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import (
    DOMAIN,
    OPTION_FEATURE,
    OPTION_POSSIBLY_BREAKING,
    OPTION_ROUTINE,
    RECIPE_UPDATES,
    UPDATES_ISSUE_PREFIX,
)
from custom_components.gutcheck.coordinator import RecipeCoordinator

from .conftest import (
    FakeUpdateEntity,
    api_response,
    install_update_entities,
    posted_bodies,
    register_jev_responses,
    register_pending_update,
    score_answer,
)


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    """Set up a Gut Check entry with a test API key."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: "test-key"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


def _coordinator(entry: MockConfigEntry) -> RecipeCoordinator:
    """The update recipe's coordinator for a set-up entry."""
    return entry.runtime_data.coordinators[RECIPE_UPDATES]  # type: ignore[no-any-return]


async def _run_again(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Drive a second (or later) run directly, the same way a scheduled refresh would."""
    await _coordinator(entry).async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)


def _sensor_state(hass: HomeAssistant, entry: MockConfigEntry) -> Any:
    """The update recipe's sensor state object."""
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{RECIPE_UPDATES}")
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    return state


async def test_unchanged_accepted_updates_send_no_second_request(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A second run where both updates were accepted and neither changed sends nothing, but the run time advances."""
    register_pending_update(hass, "update_a")
    register_pending_update(hass, "update_b")
    register_jev_responses(aioclient_mock, [api_response({"u0": score_answer(0, 0.9), "u1": score_answer(2, 0.9)})])
    entry = await _setup(hass)
    assert len(posted_bodies(aioclient_mock)) == 1

    first_state = _sensor_state(hass, entry)
    first_last_run = first_state.attributes["last_run"]
    first_last_payload = first_state.attributes["last_payload"]
    first_items = first_state.attributes["items"]

    await _run_again(hass, entry)

    assert len(posted_bodies(aioclient_mock)) == 1
    second_state = _sensor_state(hass, entry)
    assert second_state.attributes["last_payload"] == first_last_payload
    assert second_state.attributes["items"] == first_items
    assert second_state.attributes["counts"] == first_state.attributes["counts"]
    assert second_state.attributes["last_run"] != first_last_run


async def test_one_unchanged_one_unsure_asks_only_about_the_unsure_one(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The accepted, unchanged update carries forward; the unsure one is asked again."""
    register_pending_update(hass, "update_accepted")
    register_pending_update(hass, "update_unsure")
    register_jev_responses(aioclient_mock, [api_response({"u0": score_answer(0, 0.9), "u1": score_answer(1, 0.1)})])
    entry = await _setup(hass)
    assert len(posted_bodies(aioclient_mock)) == 1
    accepted_before = _sensor_state(hass, entry).attributes["items"][OPTION_ROUTINE][0]

    aioclient_mock.clear_requests()
    register_jev_responses(aioclient_mock, [api_response({"u0": score_answer(1, 0.9)})])
    await _run_again(hass, entry)

    bodies = posted_bodies(aioclient_mock)
    assert len(bodies) == 1
    assert len(bodies[0]["questions"]) == 1

    state = _sensor_state(hass, entry)
    assert state.attributes["items"][OPTION_ROUTINE][0] == accepted_before
    assert state.attributes["counts"][OPTION_FEATURE] == 1


async def test_a_latest_version_bump_asks_only_about_that_one(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Only the entity whose latest_version changed is asked about again."""
    # Named so entity_id order (a < b) matches question index order (u0, u1).
    register_pending_update(hass, "update_a_unchanged")
    register_pending_update(hass, "update_b_bumped")
    register_jev_responses(aioclient_mock, [api_response({"u0": score_answer(0, 0.9), "u1": score_answer(1, 0.9)})])
    entry = await _setup(hass)
    unchanged_before = _sensor_state(hass, entry).attributes["items"][OPTION_ROUTINE][0]

    register_pending_update(hass, "update_b_bumped", latest_version="3.0.0")
    aioclient_mock.clear_requests()
    register_jev_responses(aioclient_mock, [api_response({"u0": score_answer(2, 0.9)})])
    await _run_again(hass, entry)

    bodies = posted_bodies(aioclient_mock)
    assert len(bodies) == 1
    assert len(bodies[0]["questions"]) == 1

    state = _sensor_state(hass, entry)
    assert state.attributes["items"][OPTION_ROUTINE][0] == unchanged_before
    assert state.attributes["counts"][OPTION_POSSIBLY_BREAKING] == 1


async def test_an_installed_version_change_while_pending_asks_about_it(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A newly installed lower version while still pending is treated as a change, and asked about again."""
    register_pending_update(hass, "update_a")
    register_jev_responses(aioclient_mock, [api_response({"u0": score_answer(0, 0.9)})])
    entry = await _setup(hass)

    register_pending_update(hass, "update_a", installed_version="1.5.0")
    aioclient_mock.clear_requests()
    register_jev_responses(aioclient_mock, [api_response({"u0": score_answer(1, 0.9)})])
    await _run_again(hass, entry)

    bodies = posted_bodies(aioclient_mock)
    assert len(bodies) == 1
    assert len(bodies[0]["questions"]) == 1


async def test_a_cleared_skipped_version_asks_about_it(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A skipped_version that was cleared since the last run is treated as a change."""
    register_pending_update(hass, "update_a", skipped_version="1.5.0")
    register_jev_responses(aioclient_mock, [api_response({"u0": score_answer(0, 0.9)})])
    entry = await _setup(hass)

    register_pending_update(hass, "update_a", skipped_version=None)
    aioclient_mock.clear_requests()
    register_jev_responses(aioclient_mock, [api_response({"u0": score_answer(1, 0.9)})])
    await _run_again(hass, entry)

    bodies = posted_bodies(aioclient_mock)
    assert len(bodies) == 1
    assert len(bodies[0]["questions"]) == 1


async def test_carried_item_relinks_to_the_entitys_current_release_url(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A carried card is re-created from the entity's current state, not a stale stored link."""
    update_entry = register_pending_update(hass, "update_a", release_url="https://example.com/old")
    register_jev_responses(aioclient_mock, [api_response({"u0": score_answer(2, 0.9)})])
    entry = await _setup(hass)
    issue_id = f"{UPDATES_ISSUE_PREFIX}{update_entry.id}"
    first_issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert first_issue is not None
    assert first_issue.learn_more_url == "https://example.com/old"

    register_pending_update(hass, "update_a", release_url="https://example.com/new")
    aioclient_mock.clear_requests()
    await _run_again(hass, entry)

    assert posted_bodies(aioclient_mock) == []
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.learn_more_url == "https://example.com/new"


async def test_empty_notes_major_jump_update_carries_into_possibly_breaking_with_no_question(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The code-decided empty-notes, major-jump update never reaches the model, on the very first run."""
    register_pending_update(hass, "update_empty", release_summary=None)
    entry = await _setup(hass)

    assert posted_bodies(aioclient_mock) == []
    state = _sensor_state(hass, entry)
    assert state.attributes["counts"][OPTION_POSSIBLY_BREAKING] == 1
    item = state.attributes["items"][OPTION_POSSIBLY_BREAKING][0]
    assert "confidence" not in item
    assert "score" not in item


async def test_an_update_unavailable_during_a_run_keeps_its_classification_and_its_card(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An update entity that happens to be unavailable when a run lands is carried, not swept."""
    update_entry = register_pending_update(hass, "update_a")
    register_jev_responses(aioclient_mock, [api_response({"u0": score_answer(2, 0.9)})])
    entry = await _setup(hass)
    issue_id = f"{UPDATES_ISSUE_PREFIX}{update_entry.id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    classified_before = _sensor_state(hass, entry).attributes["items"][OPTION_POSSIBLY_BREAKING][0]

    hass.states.async_set(update_entry.entity_id, STATE_UNAVAILABLE)
    aioclient_mock.clear_requests()
    await _run_again(hass, entry)

    assert posted_bodies(aioclient_mock) == []
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    state = _sensor_state(hass, entry)
    assert state.state == "1"
    assert state.attributes["items"][OPTION_POSSIBLY_BREAKING] == [classified_before]


async def test_pressing_run_button_asks_about_everything_even_when_unchanged(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A button press forces a full re-score, ignoring what would otherwise carry forward."""
    register_pending_update(hass, "update_a")
    register_pending_update(hass, "update_b")
    register_jev_responses(aioclient_mock, [api_response({"u0": score_answer(0, 0.9), "u1": score_answer(1, 0.9)})])
    entry = await _setup(hass)

    button_entity_id = er.async_get(hass).async_get_entity_id("button", DOMAIN, f"{entry.entry_id}_{RECIPE_UPDATES}_run")
    assert button_entity_id is not None
    aioclient_mock.clear_requests()
    register_jev_responses(aioclient_mock, [api_response({"u0": score_answer(0, 0.9), "u1": score_answer(1, 0.9)})])

    await hass.services.async_call("button", "press", {"entity_id": button_entity_id}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)

    bodies = posted_bodies(aioclient_mock)
    assert len(bodies) == 1
    assert len(bodies[0]["questions"]) == 2


async def test_fully_carried_run_leaves_tokens_today_unchanged(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A run that sends no request spends no additional tokens."""
    register_pending_update(hass, "update_a")
    register_jev_responses(aioclient_mock, [api_response({"u0": score_answer(0, 0.9)}, input_tokens=42)])
    entry = await _setup(hass)
    tokens_entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_tokens_today")
    assert tokens_entity_id is not None
    before = hass.states.get(tokens_entity_id)
    assert before is not None

    await _run_again(hass, entry)

    assert len(posted_bodies(aioclient_mock)) == 1
    after = hass.states.get(tokens_entity_id)
    assert after is not None
    assert after.state == before.state


async def test_a_removed_update_entity_drops_out_with_no_pruning_step(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An update that disappears from the registry simply stops being selected; nothing prunes it explicitly."""
    kept = register_pending_update(hass, "update_kept")
    removed = register_pending_update(hass, "update_removed")
    register_jev_responses(aioclient_mock, [api_response({"u0": score_answer(0, 0.9), "u1": score_answer(1, 0.9)})])
    entry = await _setup(hass)
    assert _sensor_state(hass, entry).state == "2"

    er.async_get(hass).async_remove(removed.entity_id)
    aioclient_mock.clear_requests()
    await _run_again(hass, entry)

    assert posted_bodies(aioclient_mock) == []
    state = _sensor_state(hass, entry)
    assert state.state == "1"
    remaining_ids = {item["entity_id"] for bucket in state.attributes["items"].values() for item in bucket}
    assert remaining_ids == {kept.entity_id}


async def test_carried_update_skips_the_notes_fetch(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A second run fetches release notes only for the update whose version moved, not the one carried forward."""
    unchanged = register_pending_update(hass, "update_a")
    moved = register_pending_update(hass, "update_b")
    fakes = {unchanged.entity_id: FakeUpdateEntity(notes="Notes."), moved.entity_id: FakeUpdateEntity(notes="Notes.")}
    install_update_entities(hass, fakes)
    register_jev_responses(
        aioclient_mock,
        [
            api_response({"u0": score_answer(0, 0.9), "u1": score_answer(0, 0.9)}),
            api_response({"u0": score_answer(0, 0.9)}),
        ],
    )
    entry = await _setup(hass)
    assert [fake.fetches for fake in fakes.values()] == [1, 1]

    register_pending_update(hass, "update_b", latest_version="2.1.0")
    await _run_again(hass, entry)

    assert [fake.fetches for fake in fakes.values()] == [1, 2]
    assert len(posted_bodies(aioclient_mock)) == 2
