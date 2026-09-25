"""Timeline tests for a health run too large for one request, split into several."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.budget import estimate_tokens
from custom_components.gutcheck.const import HEALTH_CRITERIA, HEALTH_INSTRUCTIONS, MODEL, OPTION_EXPECTED
from custom_components.gutcheck.diagnostics import async_get_config_entry_diagnostics
from custom_components.gutcheck.recipes.health import HealthRecipe
from custom_components.gutcheck.recipes.shapes import Batch

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
