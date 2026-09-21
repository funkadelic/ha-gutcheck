"""Tracer-style tests for the update review recipe: selection through to the sensor."""

from __future__ import annotations

import json
from typing import Any

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import OPTION_FEATURE, OPTION_POSSIBLY_BREAKING, OPTION_ROUTINE, RECIPE_UPDATES
from custom_components.gutcheck.recipes.shapes import recipe_store_key

from .conftest import (
    api_response,
    posted_bodies,
    register_jev_responses,
    register_pending_update,
    score_answer,
    updates_sensor_entity_id,
)

UPDATES_STORE_KEY = recipe_store_key(RECIPE_UPDATES)


async def test_two_pending_updates_are_scored_in_one_request(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Two pending updates are scored in exactly one request, and the sensor counts both."""
    register_pending_update(hass, "update_a", release_url="https://example.com/release_a")
    register_pending_update(hass, "update_b", release_url="https://example.com/release_b")
    register_jev_responses(
        aioclient_mock,
        [api_response({"u0": score_answer(0, 0.9), "u1": score_answer(2, 0.9)})],
    )
    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    bodies = posted_bodies(aioclient_mock)
    assert len(bodies) == 1
    body = bodies[0]
    assert set(body["questions"].keys()) == {"u0", "u1"}

    state = hass.states.get(updates_sensor_entity_id(hass, mock_config_entry))
    assert state is not None
    assert state.state == "2"
    assert state.attributes["counts"][OPTION_ROUTINE] == 1
    assert state.attributes["counts"][OPTION_POSSIBLY_BREAKING] == 1
    assert state.attributes["counts"][OPTION_FEATURE] == 0

    # No release url, and no selected entity's release url value, ever left the process.
    serialized_body = json.dumps(body)
    assert "release_url" not in serialized_body
    assert "https://example.com/release_a" not in serialized_body
    assert "https://example.com/release_b" not in serialized_body

    accepted_item = state.attributes["items"][OPTION_ROUTINE][0]
    assert "confidence" in accepted_item
    assert "score" in accepted_item


async def test_below_threshold_answer_lands_in_unsure(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A score with confidence below the update threshold goes to unsure, not any count."""
    register_pending_update(hass)
    register_jev_responses(aioclient_mock, [api_response({"u0": score_answer(2, 0.1)})])
    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    state = hass.states.get(updates_sensor_entity_id(hass, mock_config_entry))
    assert state is not None
    assert sum(state.attributes["counts"].values()) == 0
    assert len(state.attributes["unsure"]) == 1


async def test_stored_update_result_restores_after_a_reload_with_no_request(
    hass: HomeAssistant,
    freezer: Any,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A stored update result restores on reload inside the run interval, with no further request.

    This is what Task 0's shapes.py split fixed: the update recipe's stored
    items carry only entity_id and registry_id, no unavailable_for, so the
    store-parsing guard must accept them against the update recipe's own
    declared key set rather than the health recipe's.
    """
    freezer.move_to("2026-01-01T00:00:00-08:00")
    register_pending_update(hass)
    register_jev_responses(aioclient_mock, [api_response({"u0": score_answer(0, 0.9)})])
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(posted_bodies(aioclient_mock)) == 1

    freezer.move_to("2026-01-04T00:00:00-08:00")
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(posted_bodies(aioclient_mock)) == 1
    state = hass.states.get(updates_sensor_entity_id(hass, mock_config_entry))
    assert state is not None
    assert state.state == "1"
    assert state.attributes["counts"][OPTION_ROUTINE] == 1
