"""Tests for the tokens-used-today and cost-today sensors and their persistence."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import BUDGET_STORE_KEY, DOMAIN, OPTION_WORTH_FIXING

from .conftest import choice_answer, posted_bodies, register_jev_responses

TOKENS_UNIQUE_ID_SUFFIX = "_tokens_today"
COST_UNIQUE_ID_SUFFIX = "_cost_today"


def _api_response(answers: dict[str, Any], input_tokens: int) -> dict[str, Any]:
    return {
        "model": "jev-latest",
        "answers": answers,
        "usage": {"input_tokens": input_tokens, "output_tokens": 0},
    }


def _sensor_state(hass: HomeAssistant, entry: MockConfigEntry, suffix: str) -> str:
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}{suffix}")
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    return state.state


def _cost_value(hass: HomeAssistant, entry: MockConfigEntry) -> float:
    return float(_sensor_state(hass, entry, COST_UNIQUE_ID_SUFFIX))


def _register_one_unavailable_entity(hass: HomeAssistant) -> str:
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("sensor", "test", "unique_selectable")
    hass.states.async_set(entry.entity_id, STATE_UNAVAILABLE)
    return entry.entity_id


async def test_fresh_setup_shows_zero_tokens_and_cost(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> None:
    """No unavailable entities: nothing to send, so both sensors read 0."""
    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert _sensor_state(hass, mock_config_entry, TOKENS_UNIQUE_ID_SUFFIX) == "0"
    assert _cost_value(hass, mock_config_entry) == pytest.approx(0.0)


async def test_run_updates_sensors_and_persists_and_survives_restart(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A run's reported usage lands on both sensors, in the Store, and survives a restart."""
    entity_id = _register_one_unavailable_entity(hass)
    register_jev_responses(
        aioclient_mock,
        [_api_response({"e0": choice_answer(OPTION_WORTH_FIXING, 0.9)}, input_tokens=1234)],
    )
    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert _sensor_state(hass, mock_config_entry, TOKENS_UNIQUE_ID_SUFFIX) == "1234"
    assert _cost_value(hass, mock_config_entry) == pytest.approx(1234 * 0.042 / 1_000_000)

    registry = er.async_get(hass)
    tokens_entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{mock_config_entry.entry_id}{TOKENS_UNIQUE_ID_SUFFIX}")
    assert tokens_entity_id is not None
    tokens_state = hass.states.get(tokens_entity_id)
    assert tokens_state is not None
    assert tokens_state.attributes["daily_budget"] == 100_000
    assert tokens_state.attributes["remaining"] == 100_000 - 1234

    stored = hass_storage[BUDGET_STORE_KEY]["data"]
    assert stored["spent"] == 1234

    # Restart simulation: the entity has recovered, so the next automatic run
    # sends nothing and spent must not change; hass_storage carries over
    # unmodified across the unload/setup pair, the same as a real restart.
    hass.states.async_set(entity_id, "on")
    assert len(posted_bodies(aioclient_mock)) == 1

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert _sensor_state(hass, mock_config_entry, TOKENS_UNIQUE_ID_SUFFIX) == "1234"
    assert len(posted_bodies(aioclient_mock)) == 1


async def test_stored_yesterday_loads_as_zero(
    hass: HomeAssistant, hass_storage: dict[str, Any], mock_config_entry: MockConfigEntry
) -> None:
    """A counter stored under yesterday's date reads 0 today, with nothing to send."""
    hass_storage[BUDGET_STORE_KEY] = {
        "version": 1,
        "minor_version": 1,
        "key": BUDGET_STORE_KEY,
        "data": {"date": "2020-01-01", "spent": 500},
    }
    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert _sensor_state(hass, mock_config_entry, TOKENS_UNIQUE_ID_SUFFIX) == "0"


async def test_corrupt_stored_value_loads_as_zero(
    hass: HomeAssistant, hass_storage: dict[str, Any], mock_config_entry: MockConfigEntry
) -> None:
    """A malformed spent value is discarded; the counter starts at 0 rather than crashing setup."""
    hass_storage[BUDGET_STORE_KEY] = {
        "version": 1,
        "minor_version": 1,
        "key": BUDGET_STORE_KEY,
        "data": {"date": "2026-01-01", "spent": "abc"},
    }
    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert _sensor_state(hass, mock_config_entry, TOKENS_UNIQUE_ID_SUFFIX) == "0"


async def test_both_sensors_reset_at_local_midnight_with_no_request(
    hass: HomeAssistant,
    freezer: Any,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Crossing local midnight zeroes both sensors on its own, without sending anything."""
    freezer.move_to("2026-01-01T23:59:59-08:00")
    entity_id = _register_one_unavailable_entity(hass)
    register_jev_responses(
        aioclient_mock,
        [_api_response({"e0": choice_answer(OPTION_WORTH_FIXING, 0.9)}, input_tokens=1234)],
    )
    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _sensor_state(hass, mock_config_entry, TOKENS_UNIQUE_ID_SUFFIX) == "1234"

    hass.states.async_set(entity_id, "on")
    posted_before = len(posted_bodies(aioclient_mock))

    freezer.move_to("2026-01-02T00:00:00-08:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert _sensor_state(hass, mock_config_entry, TOKENS_UNIQUE_ID_SUFFIX) == "0"
    assert _cost_value(hass, mock_config_entry) == pytest.approx(0.0)
    assert len(posted_bodies(aioclient_mock)) == posted_before
