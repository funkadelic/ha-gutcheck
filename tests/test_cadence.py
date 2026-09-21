"""Timeline tests for the weekly cadence: restart-free restores, scheduled catch-up, retry."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, STATE_UNAVAILABLE
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import DOMAIN, HEALTH_ISSUE_PREFIX, OPTION_WORTH_FIXING, RECIPE_HEALTH, STORE_VERSION
from custom_components.gutcheck.recipes.base import recipe_store_key

from .conftest import (
    api_response,
    choice_answer,
    health_sensor_entity_id,
    posted_bodies,
    register_jev_responses,
    register_unavailable_entity,
)

HEALTH_STORE_KEY = recipe_store_key(RECIPE_HEALTH)
# Inside RECIPE_INTERVAL on purpose: a store that is merely stale takes the
# never-run branch anyway, which would let a malformed one pass unnoticed.
RECENT_RUN = (dt_util.utcnow() - timedelta(days=1)).isoformat()


def _stale_stored_result(days_old: int) -> dict[str, Any]:
    """A stored recipe result last run `days_old` days ago."""
    last_run = (dt_util.utcnow() - timedelta(days=days_old)).isoformat()
    return {"last_run": last_run, "counts": {}, "items": {}, "unsure": [], "last_payload": None}


async def test_fresh_install_runs_once_after_startup_and_persists(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A fresh install waits for startup, then runs once and persists the result."""
    hass.set_state(CoreState.not_running)
    register_unavailable_entity(hass)
    register_jev_responses(aioclient_mock, [api_response({"e0": choice_answer(OPTION_WORTH_FIXING, 0.9)})])
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert posted_bodies(aioclient_mock) == []

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(posted_bodies(aioclient_mock)) == 1
    stored = hass_storage[HEALTH_STORE_KEY]["data"]
    assert stored["last_run"]
    assert stored["counts"][OPTION_WORTH_FIXING] == 1


async def test_restart_within_a_week_restores_without_posting(
    hass: HomeAssistant,
    freezer: Any,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A restart 3 days after the last run restores the sensor for free."""
    freezer.move_to("2026-01-01T00:00:00-08:00")
    register_unavailable_entity(hass)
    register_jev_responses(aioclient_mock, [api_response({"e0": choice_answer(OPTION_WORTH_FIXING, 0.9)})])
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(posted_bodies(aioclient_mock)) == 1

    freezer.move_to("2026-01-04T00:00:00-08:00")
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(posted_bodies(aioclient_mock)) == 1
    state = hass.states.get(health_sensor_entity_id(hass, mock_config_entry))
    assert state is not None
    assert state.state == "1"
    assert state.attributes["counts"][OPTION_WORTH_FIXING] == 1
    assert state.attributes["last_payload"] is not None


async def test_scheduled_refresh_fires_seven_days_after_the_last_run(
    hass: HomeAssistant,
    freezer: Any,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The next run lands 7 days after the last one, not 7 days after the restart."""
    freezer.move_to("2026-01-01T00:00:00-08:00")
    register_unavailable_entity(hass)
    register_jev_responses(aioclient_mock, [api_response({"e0": choice_answer(OPTION_WORTH_FIXING, 0.9)})])
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(posted_bodies(aioclient_mock)) == 1

    freezer.move_to("2026-01-03T00:00:00-08:00")
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(posted_bodies(aioclient_mock)) == 1

    aioclient_mock.clear_requests()
    register_jev_responses(aioclient_mock, [api_response({"e0": choice_answer(OPTION_WORTH_FIXING, 0.9)})])

    # 7 days after the original last_run (Jan 1), not after the Jan 3 restart.
    freezer.move_to("2026-01-08T00:00:05-08:00")
    for _ in range(5):
        await asyncio.sleep(0)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(posted_bodies(aioclient_mock)) == 1


async def test_restart_with_a_stale_stored_run_re_runs_at_startup(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A stored last_run 8 days old is treated as due; it runs once startup completes."""
    hass.set_state(CoreState.not_running)
    register_unavailable_entity(hass)
    hass_storage[HEALTH_STORE_KEY] = {
        "version": STORE_VERSION,
        "minor_version": 1,
        "key": HEALTH_STORE_KEY,
        "data": _stale_stored_result(days_old=8),
    }
    register_jev_responses(aioclient_mock, [api_response({"e0": choice_answer(OPTION_WORTH_FIXING, 0.9)})])
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert posted_bodies(aioclient_mock) == []

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(posted_bodies(aioclient_mock)) == 1


@pytest.mark.parametrize(
    "malformed_data",
    [
        pytest.param({"not": "a recipe result"}, id="missing_keys"),
        pytest.param(
            {"last_run": 12345, "counts": {}, "items": {}, "unsure": [], "last_payload": None}, id="non_string_last_run"
        ),
        pytest.param(
            {"last_run": "not-a-timestamp", "counts": {}, "items": {}, "unsure": [], "last_payload": None},
            id="unparseable_last_run",
        ),
        pytest.param(
            {"last_run": RECENT_RUN, "counts": "nope", "items": {}, "unsure": [], "last_payload": None},
            id="non_dict_counts",
        ),
        pytest.param(
            {"last_run": RECENT_RUN, "counts": {}, "items": "nope", "unsure": [], "last_payload": None},
            id="non_dict_items",
        ),
        pytest.param(
            {"last_run": RECENT_RUN, "counts": {}, "items": {}, "unsure": "nope", "last_payload": None},
            id="non_list_unsure",
        ),
        pytest.param(
            {"last_run": "2099-01-01T00:00:00+00:00", "counts": {}, "items": {}, "unsure": [], "last_payload": None},
            id="last_run_in_the_future",
        ),
        pytest.param(
            {
                "last_run": RECENT_RUN,
                "counts": {},
                "items": {OPTION_WORTH_FIXING: [{}]},
                "unsure": [],
                "last_payload": None,
            },
            id="item_missing_the_fields_restore_reads",
        ),
        pytest.param(
            {
                "last_run": RECENT_RUN,
                "counts": {},
                "items": {OPTION_WORTH_FIXING: "nope"},
                "unsure": [],
                "last_payload": None,
            },
            id="non_list_bucket",
        ),
        pytest.param(
            {
                "last_run": RECENT_RUN,
                "counts": {OPTION_WORTH_FIXING: "nope"},
                "items": {},
                "unsure": [],
                "last_payload": None,
            },
            id="non_int_count",
        ),
    ],
)
async def test_malformed_stored_value_is_treated_as_never_run(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    malformed_data: dict[str, Any],
) -> None:
    """A stored value that isn't a proper RecipeResult is treated as never run."""
    hass.set_state(CoreState.not_running)
    register_unavailable_entity(hass)
    hass_storage[HEALTH_STORE_KEY] = {
        "version": STORE_VERSION,
        "minor_version": 1,
        "key": HEALTH_STORE_KEY,
        "data": malformed_data,
    }
    register_jev_responses(aioclient_mock, [api_response({"e0": choice_answer(OPTION_WORTH_FIXING, 0.9)})])
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert posted_bodies(aioclient_mock) == []

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(posted_bodies(aioclient_mock)) == 1


async def test_restore_rearms_recovery_tracking_with_no_api_call(
    hass: HomeAssistant,
    freezer: Any,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """After a restore, the restored worth-fixing entity's issue still clears on recovery, with no POST."""
    freezer.move_to("2026-01-01T00:00:00-08:00")
    entity_id = register_unavailable_entity(hass)
    registry_id = er.async_get(hass).async_get(entity_id).id  # type: ignore[union-attr]

    register_jev_responses(aioclient_mock, [api_response({"e0": choice_answer(OPTION_WORTH_FIXING, 0.9)})])
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    issue_id = f"{HEALTH_ISSUE_PREFIX}{registry_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    freezer.move_to("2026-01-04T00:00:00-08:00")
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(posted_bodies(aioclient_mock)) == 1

    hass.states.async_set(entity_id, "on")
    await hass.async_block_till_done()

    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    assert len(posted_bodies(aioclient_mock)) == 1


async def test_failed_scheduled_run_retries_after_an_hour(
    hass: HomeAssistant,
    freezer: Any,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A 500 on a scheduled run leaves the sensor unavailable, then retries by itself an hour later."""
    freezer.move_to("2026-01-01T00:00:00-08:00")
    register_unavailable_entity(hass)
    register_jev_responses(aioclient_mock, [(500, {"error": "boom"})])
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    state = hass.states.get(health_sensor_entity_id(hass, mock_config_entry))
    assert state is not None
    assert state.state == STATE_UNAVAILABLE

    aioclient_mock.clear_requests()
    register_jev_responses(aioclient_mock, [api_response({"e0": choice_answer(OPTION_WORTH_FIXING, 0.9)})])

    freezer.tick(3600 + 5)
    for _ in range(5):
        await asyncio.sleep(0)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(posted_bodies(aioclient_mock)) == 1
    state = hass.states.get(health_sensor_entity_id(hass, mock_config_entry))
    assert state is not None
    assert state.state == "1"


async def test_removing_the_entry_deletes_the_recipe_store(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Removing the entry deletes the persisted recipe Store, not just its Repairs issues."""
    register_unavailable_entity(hass)
    register_jev_responses(aioclient_mock, [api_response({"e0": choice_answer(OPTION_WORTH_FIXING, 0.9)})])
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert HEALTH_STORE_KEY in hass_storage

    await hass.config_entries.async_remove(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert HEALTH_STORE_KEY not in hass_storage
