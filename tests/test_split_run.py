"""Timeline tests for a health run too large for one request, split into several."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.budget import BudgetGate, estimate_tokens
from custom_components.gutcheck.client import GutCheckClient
from custom_components.gutcheck.const import (
    FAILED_RUN_RETRY,
    HEALTH_CRITERIA,
    HEALTH_INSTRUCTIONS,
    MODEL,
    OPTION_EXPECTED,
    RECIPE_HEALTH,
    STORE_VERSION,
)
from custom_components.gutcheck.coordinator import RecipeCoordinator
from custom_components.gutcheck.diagnostics import async_get_config_entry_diagnostics
from custom_components.gutcheck.recipes.health import HealthRecipe
from custom_components.gutcheck.recipes.shapes import Batch, recipe_store_key

from .conftest import (
    api_response,
    choice_answer,
    health_sensor_entity_id,
    posted_bodies,
    register_jev_responses,
    register_unavailable_entity,
)


def _slice_body(batch: Batch, start: int, end: int) -> dict[str, Any]:
    """The request a split should send for subjects [start:end], built without the splitter."""
    ids = list(batch.questions)[start:end]
    return {
        "state": {"entities": batch.state["entities"][start:end]},
        "model": MODEL,
        "questions": {
            qid: {"type": "choice", "instructions": HEALTH_INSTRUCTIONS.format(index=local), "criteria": HEALTH_CRITERIA}
            for local, qid in enumerate(ids)
        },
    }


async def _two_per_request(hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, count: int) -> Batch:
    """Register count identical entities and cap requests at the size of a two-subject slice."""
    for index in range(count):
        register_unavailable_entity(hass, f"split_{index}")
    batch = await HealthRecipe(None).async_prepare(hass)
    monkeypatch.setattr("custom_components.gutcheck.budget.REQUEST_TOKEN_LIMIT", estimate_tokens(_slice_body(batch, 0, 2)))
    return batch


async def test_oversized_health_run_goes_out_as_several_requests(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Five subjects at two per request post three slice bodies and classify every subject."""
    hass.set_state(CoreState.not_running)
    batch = await _two_per_request(hass, monkeypatch, 5)
    expected = [_slice_body(batch, 0, 2), _slice_body(batch, 2, 4), _slice_body(batch, 4, 5)]
    answers = [{qid: choice_answer(OPTION_EXPECTED, 0.9) for qid in body["questions"]} for body in expected]
    register_jev_responses(aioclient_mock, [api_response(a, tokens) for a, tokens in zip(answers, (10, 20, 30), strict=True)])
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done(wait_background_tasks=True)

    bodies = posted_bodies(aioclient_mock)
    assert bodies == expected
    assert [qid for body in bodies for qid in body["questions"]] == [f"e{index}" for index in range(5)]

    state = hass.states.get(health_sensor_entity_id(hass, mock_config_entry))
    assert state is not None
    assert sum(state.attributes["counts"].values()) + len(state.attributes["unsure"]) == 5
    assert state.attributes["last_payload"] == expected
    assert mock_config_entry.runtime_data.budget.spent_today == 60

    diagnostics = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    assert diagnostics["recipes"]["health"]["last_payload"] == expected


async def _coordinator(hass: HomeAssistant, entry: MockConfigEntry) -> RecipeCoordinator:
    """A health coordinator on its own budget gate, driven directly rather than through setup."""
    entry.add_to_hass(hass)
    budget = BudgetGate(hass, GutCheckClient(async_get_clientsession(hass), "test-key"), daily_budget=100_000)
    await budget.async_load()
    return RecipeCoordinator(hass, entry, budget, HealthRecipe(None))


async def test_second_request_failing_retries_the_run_within_the_hour(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 500 on request 2 fails the run on the usual retry schedule, keeping request 1's actual."""
    await _two_per_request(hass, monkeypatch, 3)
    register_jev_responses(aioclient_mock, [api_response({}, 10), (500, {"error": "boom"})])
    coordinator = await _coordinator(hass, mock_config_entry)

    with pytest.raises(UpdateFailed) as excinfo:
        await coordinator._async_update_data()

    assert excinfo.value.retry_after == FAILED_RUN_RETRY.total_seconds()
    assert coordinator.budget.spent_today == 10


async def test_second_request_rejecting_the_key_starts_reauth(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 401 on request 2 maps to ConfigEntryAuthFailed, keeping request 1's actual."""
    await _two_per_request(hass, monkeypatch, 3)
    register_jev_responses(aioclient_mock, [api_response({}, 10), (401, {"error": "unauthorized"})])
    coordinator = await _coordinator(hass, mock_config_entry)

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()

    assert coordinator.budget.spent_today == 10


async def test_lone_subject_too_large_for_any_request_still_fails(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no subject fitting on its own, the run fails as too large, sending and spending nothing."""
    for index in range(3):
        register_unavailable_entity(hass, f"split_{index}")
    monkeypatch.setattr("custom_components.gutcheck.budget.REQUEST_TOKEN_LIMIT", 1)
    register_jev_responses(aioclient_mock, [])
    coordinator = await _coordinator(hass, mock_config_entry)

    with pytest.raises(UpdateFailed, match="run was too large to send"):
        await coordinator._async_update_data()

    assert posted_bodies(aioclient_mock) == []
    assert coordinator.budget.spent_today == 0


async def test_stored_single_request_payload_restores_without_a_call(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A recent stored result whose last_payload is one request dict restores as is, for free."""
    hass.set_state(CoreState.not_running)
    stored_payload = {"state": {"entities": []}, "model": MODEL, "questions": {}}
    last_run = (dt_util.utcnow() - timedelta(days=1)).isoformat()
    key = recipe_store_key(RECIPE_HEALTH)
    hass_storage[key] = {
        "version": STORE_VERSION,
        "minor_version": 1,
        "key": key,
        "data": {"last_run": last_run, "counts": {}, "items": {}, "unsure": [], "last_payload": stored_payload},
    }
    register_jev_responses(aioclient_mock, [])
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert posted_bodies(aioclient_mock) == []
    state = hass.states.get(health_sensor_entity_id(hass, mock_config_entry))
    assert state is not None
    assert state.attributes["last_payload"] == stored_payload
