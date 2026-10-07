"""What the coordinator does with a restored result: first-run force flags, the 7-day edge, running."""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.const import CONF_API_KEY, EVENT_HOMEASSISTANT_STARTED, STATE_UNAVAILABLE
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker, AiohttpClientMockResponse

from custom_components.gutcheck.const import (
    API_URL,
    DOMAIN,
    OPTION_WORTH_FIXING,
    RECIPE_CONFIG_ENTRIES,
    RECIPE_HEALTH,
    RECIPE_UPDATES,
    STORE_VERSION,
)
from custom_components.gutcheck.coordinator import RecipeCoordinator
from custom_components.gutcheck.recipes.config_entry_const import CONFIG_ENTRY_OPTIONS, OPTION_DEAD
from custom_components.gutcheck.recipes.shapes import recipe_store_key

from .conftest import (
    api_response,
    area_answer,
    choice_answer,
    posted_bodies,
    register_jev_responses,
    register_pending_update,
    register_unavailable_entity,
    score_answer,
    triage_sensor_entity_id,
)


async def _restored_updates_coordinator(
    hass: HomeAssistant, freezer: Any, aioclient_mock: AiohttpClientMocker
) -> RecipeCoordinator:
    """Run the update recipe once, then reload the entry two days later and check it restored with no new request."""
    freezer.move_to("2026-01-01T00:00:00-08:00")
    register_pending_update(hass, "update_a")
    register_jev_responses(
        aioclient_mock, [api_response({"u0": score_answer(0, 0.9)}), api_response({"u0": score_answer(0, 0.9)})]
    )
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: "test-key"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(posted_bodies(aioclient_mock)) == 1

    freezer.move_to("2026-01-03T00:00:00-08:00")
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(posted_bodies(aioclient_mock)) == 1
    coordinator: RecipeCoordinator = entry.runtime_data.coordinators[RECIPE_UPDATES]
    assert coordinator.data is not None
    assert coordinator.running is False
    return coordinator


async def test_the_first_run_after_a_restore_keeps_an_unchanged_answer(
    hass: HomeAssistant, freezer: Any, aioclient_mock: AiohttpClientMocker
) -> None:
    """Nothing asked a full re-score, so an unchanged accepted update is not sent again."""
    coordinator = await _restored_updates_coordinator(hass, freezer, aioclient_mock)

    await coordinator.async_refresh()

    assert len(posted_bodies(aioclient_mock)) == 1
    assert coordinator.running is False


async def test_a_full_rescore_requested_after_a_restore_asks_again(
    hass: HomeAssistant, freezer: Any, aioclient_mock: AiohttpClientMocker
) -> None:
    """The first full re-score after a restart is honored even though no run has finished yet."""
    coordinator = await _restored_updates_coordinator(hass, freezer, aioclient_mock)

    coordinator.force_full_rescore()
    await coordinator.async_refresh()

    assert len(posted_bodies(aioclient_mock)) == 2


async def test_a_result_exactly_seven_days_old_is_not_restored(
    hass: HomeAssistant, freezer: Any, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """At exactly the interval the run starts from nothing, so no restored data shows while its request is open."""
    freezer.move_to("2026-01-01T00:00:00-08:00")
    register_unavailable_entity(hass)
    started, hold = asyncio.Event(), asyncio.Event()
    calls = 0

    async def _serve(method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        """Answer every POST, holding the second one open until released."""
        nonlocal calls
        calls += 1
        if calls == 2:
            started.set()
            await hold.wait()
        body = api_response({"e0": choice_answer(OPTION_WORTH_FIXING, 0.9)})
        return AiohttpClientMockResponse(method=method, url=url, status=200, json=body)

    aioclient_mock.post(API_URL, side_effect=_serve)
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    freezer.move_to("2026-01-08T00:00:00-08:00")
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await asyncio.wait_for(started.wait(), 5)

    coordinator = mock_config_entry.runtime_data.coordinators[RECIPE_HEALTH]
    assert coordinator.data is None
    assert coordinator.running is True
    hold.set()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert coordinator.running is False


async def test_a_stale_stored_result_is_not_shown_but_the_first_run_keeps_its_first_seen(
    hass: HomeAssistant,
    freezer: Any,
    hass_storage: dict[str, Any],
    aioclient_mock: AiohttpClientMocker,
    triage_entry: MockConfigEntry,
    failing_entry: Any,
) -> None:
    """A result over a week old stays hidden, yet the first run still reads its first_seen."""
    freezer.move_to("2026-01-10T00:00:00-08:00")
    first_seen_at = "2026-01-01T08:00:00+00:00"
    await failing_entry("stale_hub", ConfigEntryError("timed out"), title="Stale Hub", entry_id="stale_entry")
    item = {
        "entry_id": "stale_entry",
        "integration": "stale_hub",
        "title": "Stale Hub",
        "first_seen": first_seen_at,
        "failing_for": "unknown",
    }
    key = recipe_store_key(RECIPE_CONFIG_ENTRIES)
    hass_storage[key] = {
        "version": STORE_VERSION,
        "minor_version": 1,
        "key": key,
        "data": {
            "last_run": "2026-01-02T08:00:00+00:00",
            "counts": {option: int(option == OPTION_DEAD) for option in CONFIG_ENTRY_OPTIONS},
            "items": {option: [item] if option == OPTION_DEAD else [] for option in CONFIG_ENTRY_OPTIONS},
            "unsure": [],
            "last_payload": None,
        },
    }
    hass.set_state(CoreState.not_running)
    triage_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(triage_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = triage_entry.runtime_data.coordinators[RECIPE_CONFIG_ENTRIES]
    assert coordinator.data is None
    assert hass.states.get(triage_sensor_entity_id(hass, triage_entry)).state == STATE_UNAVAILABLE
    assert posted_bodies(aioclient_mock) == []

    register_jev_responses(aioclient_mock, [api_response({"c0": area_answer(OPTION_DEAD, 0.9, CONFIG_ENTRY_OPTIONS)})])
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert posted_bodies(aioclient_mock)[0]["state"]["entries"][0]["failing_for"] == "longer than 1 week"
    assert coordinator.data["items"][OPTION_DEAD][0]["first_seen"] == first_seen_at
