"""One request per run, measured headroom, a per-run cap that degrades, and a loud refusal past it."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.budget import _estimate, estimate_tokens
from custom_components.gutcheck.const import (
    DEFAULT_DAILY_BUDGET,
    DOMAIN,
    MAX_UPDATES_PER_RUN,
    OPTION_POSSIBLY_BREAKING,
    OPTION_WORTH_FIXING,
    RECIPE_HEALTH,
    RECIPE_UPDATES,
    RELEASE_NOTES_MAX_CHARS,
    REQUEST_TOKEN_LIMIT,
    STATE_TOKEN_LIMIT,
    UPDATES_ISSUE_PREFIX,
    VERSION_JUMP_MAJOR,
)

from .conftest import (
    api_response,
    choice_answer,
    posted_bodies,
    register_jev_responses,
    register_pending_update,
    register_unavailable_entity,
    score_answer,
)

# The budget gate's own estimator undercounts a real payload by about 12%,
# so every comparison here applies this factor before checking a limit.
SAFETY_FACTOR = 1.15
# In the neighborhood of the target install's real pending-update count
# (56 as of 2026-09-18), and safely under MAX_UPDATES_PER_RUN so this run
# is provably uncapped.
REALISTIC_UPDATE_COUNT = 45


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    """Set up a Gut Check entry with a test API key."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: "test-key"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


async def _run_again(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Drive a second run directly, the same way a scheduled refresh would."""
    await entry.runtime_data.coordinators[RECIPE_UPDATES].async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)


def _sensor_state(hass: HomeAssistant, entry: MockConfigEntry, recipe_id: str) -> Any:
    """One recipe's summary sensor state for a set-up entry."""
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{recipe_id}")
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    return state


def _full_size_state_item() -> dict[str, Any]:
    """One update at HA's real release_summary cap plus a maximum-length fetched excerpt.

    For measuring per-item token cost only; not tied to how the offline
    tests below build their own (deliberately smaller) entities.
    """
    return {
        "integration": "homeassistant_supervisor",
        "installed_version": "2026.9.1",
        "latest_version": "2026.10.0",
        "version_jump": VERSION_JUMP_MAJOR,
        "title": "Home Assistant Supervisor 2026.10.0",
        "release_summary": "x" * 255,
        "release_notes": "x" * RELEASE_NOTES_MAX_CHARS,
    }


async def test_one_run_is_one_request_with_measured_headroom(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A realistic pending-update count sends one request, comfortably inside both token limits."""
    for index in range(REALISTIC_UPDATE_COUNT):
        register_pending_update(hass, f"upd_{index:04d}", title=f"Update {index}")
    answers = {f"u{i}": score_answer(i % 3, 0.9) for i in range(REALISTIC_UPDATE_COUNT)}
    register_jev_responses(aioclient_mock, [api_response(answers)])

    await _setup(hass)

    bodies = posted_bodies(aioclient_mock)
    assert len(bodies) == 1
    body = bodies[0]
    assert len(body["questions"]) == REALISTIC_UPDATE_COUNT
    assert len(body["state"]["updates"]) == REALISTIC_UPDATE_COUNT

    factored_request = estimate_tokens(body) * SAFETY_FACTOR
    assert factored_request < REQUEST_TOKEN_LIMIT

    state_estimate = _estimate(body["state"])
    longest_question = max(_estimate(question) for question in body["questions"].values())
    factored_state = (state_estimate + longest_question) * SAFETY_FACTOR
    assert factored_state < STATE_TOKEN_LIMIT


def test_max_updates_per_run_is_set_from_the_measured_ceiling_with_the_safety_factor() -> None:
    """MAX_UPDATES_PER_RUN sits at or below the state-limit ceiling for one full-size update, factored."""
    per_item = _estimate(_full_size_state_item()) * SAFETY_FACTOR
    ceiling = int(STATE_TOKEN_LIMIT // per_item)
    print(f"full-size update estimate, factored: {per_item:.0f} tokens; STATE_TOKEN_LIMIT trips past {ceiling} updates")

    assert ceiling >= MAX_UPDATES_PER_RUN


async def test_more_pending_than_the_cap_asks_about_the_highest_jump_ones_first(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """More pending updates than the cap still sends one request, capped, highest jump first."""
    jumps = [("1.0.0", "1.0.1"), ("1.0.0", "1.5.0"), ("1.0.0", "2.0.0")]  # patch, minor, major
    total = MAX_UPDATES_PER_RUN * 3
    for index in range(total):
        installed, latest = jumps[index % 3]
        register_pending_update(
            hass, f"upd_{index:04d}", installed_version=installed, latest_version=latest, title=f"Update {index}"
        )
    answers = {f"u{i}": score_answer(0, 0.9) for i in range(MAX_UPDATES_PER_RUN)}
    register_jev_responses(aioclient_mock, [api_response(answers)])

    entry = await _setup(hass)

    bodies = posted_bodies(aioclient_mock)
    assert len(bodies) == 1
    body = bodies[0]
    assert len(body["questions"]) == MAX_UPDATES_PER_RUN
    assert len(body["state"]["updates"]) == MAX_UPDATES_PER_RUN
    assert {item["version_jump"] for item in body["state"]["updates"]} == {VERSION_JUMP_MAJOR}

    state = _sensor_state(hass, entry, RECIPE_UPDATES)
    assert state.state != "unavailable"


async def test_an_update_cut_by_the_cap_keeps_its_prior_classification_and_its_card(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An update ranked past the cap defers only its question; its bucket and its Repairs card stay put."""
    deferred = register_pending_update(hass, "upd_0000", installed_version="1.0.0", latest_version="1.0.1", title="Update 0")
    register_jev_responses(aioclient_mock, [api_response({"u0": score_answer(2, 0.9)})])
    entry = await _setup(hass)
    issue_id = f"{UPDATES_ISSUE_PREFIX}{deferred.id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    classified_before = _sensor_state(hass, entry, RECIPE_UPDATES).attributes["items"][OPTION_POSSIBLY_BREAKING][0]

    # Its version moves on, so it cannot carry forward on a version match, and
    # a cap's worth of major jumps arrive to outrank it.
    register_pending_update(hass, "upd_0000", installed_version="1.0.0", latest_version="1.0.2", title="Update 0")
    for index in range(1, MAX_UPDATES_PER_RUN + 1):
        register_pending_update(
            hass, f"upd_{index:04d}", installed_version="1.0.0", latest_version="2.0.0", title=f"Update {index}"
        )
    aioclient_mock.clear_requests()
    register_jev_responses(aioclient_mock, [api_response({f"u{i}": score_answer(0, 0.9) for i in range(MAX_UPDATES_PER_RUN)})])
    await _run_again(hass, entry)

    bodies = posted_bodies(aioclient_mock)
    assert len(bodies) == 1
    assert len(bodies[0]["questions"]) == MAX_UPDATES_PER_RUN

    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    state = _sensor_state(hass, entry, RECIPE_UPDATES)
    assert state.state == str(MAX_UPDATES_PER_RUN + 1)
    assert state.attributes["items"][OPTION_POSSIBLY_BREAKING] == [classified_before]


async def test_an_oversized_request_is_refused_with_no_partial_result(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even under the cap, a request too large to send is refused loudly rather than dropping updates silently.

    Building enough real oversized entities to organically cross
    REQUEST_TOKEN_LIMIT even after capping needs an unreasonably slow
    fixture, since the cap already keeps a full-size run comfortably
    inside both limits by design. This lowers the limit for the test's own
    duration instead, per the plan's documented fallback, to prove the
    existing budget backstop still triggers on this code path.
    """
    monkeypatch.setattr("custom_components.gutcheck.budget.REQUEST_TOKEN_LIMIT", 100)
    for index in range(3):
        register_pending_update(hass, f"upd_{index:04d}", title=f"Update {index}")

    entry = await _setup(hass)

    assert posted_bodies(aioclient_mock) == []
    state = _sensor_state(hass, entry, RECIPE_UPDATES)
    assert state.state == "unavailable"
    tokens_entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_tokens_today")
    assert tokens_entity_id is not None
    tokens_state = hass.states.get(tokens_entity_id)
    assert tokens_state is not None
    assert tokens_state.state == "0"


async def test_a_health_run_and_a_full_update_run_the_same_day_both_fit_the_default_budget(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Both coordinators refreshing on a fresh install's first day fit comfortably inside the default budget."""
    register_unavailable_entity(hass)
    for index in range(REALISTIC_UPDATE_COUNT):
        register_pending_update(hass, f"upd_{index:04d}", title=f"Update {index}")
    register_jev_responses(
        aioclient_mock,
        [
            api_response({"e0": choice_answer(OPTION_WORTH_FIXING, 0.9)}),
            api_response({f"u{i}": score_answer(i % 3, 0.9) for i in range(REALISTIC_UPDATE_COUNT)}),
        ],
    )

    entry = await _setup(hass)

    assert len(posted_bodies(aioclient_mock)) == 2
    budget = entry.runtime_data.budget
    assert budget.spent_today <= DEFAULT_DAILY_BUDGET
    assert budget.remaining >= 0
    for recipe_id in (RECIPE_HEALTH, RECIPE_UPDATES):
        assert _sensor_state(hass, entry, recipe_id).state != "unavailable"
