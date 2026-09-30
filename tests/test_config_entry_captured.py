"""Tests against a real stuck integration check run captured from the target install."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.client import validate_response
from custom_components.gutcheck.const import ATTR_COUNTS, ATTR_UNSURE, DOMAIN, OPTION_NONE
from custom_components.gutcheck.recipes.config_entries import ConfigEntryRecipe
from custom_components.gutcheck.recipes.config_entry_const import (
    CONFIG_ENTRY_OPTIONS,
    OPTION_DEAD,
    OPTION_NEEDS_REAUTH,
    OPTION_TRANSIENT,
)
from custom_components.gutcheck.recipes.gate import classify
from custom_components.gutcheck.recipes.shapes import Batch
from custom_components.gutcheck.split import split_batch

from .conftest import load_captured, posted_bodies, register_jev_responses, triage_sensor_entity_id


async def _stuck_entry(failing_entry: Any) -> MockConfigEntry:
    """A real setup_retry entry carrying the captured integration and reason."""
    item = load_captured("config_entry")[0][0]["state"]["entries"][0]
    entry = await failing_entry(item["integration"], ConfigEntryNotReady(item["reason"]), title="Coway", entry_id="coway_entry")
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert entry.reason == item["reason"]
    return entry


async def test_a_replayed_run_raises_no_card_and_lists_the_entry_as_unsure(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    triage_entry: MockConfigEntry,
    failing_entry: Any,
) -> None:
    """The captured none_of_these answer raises no card and leaves the entry in the sensor's unsure list."""
    stuck = await _stuck_entry(failing_entry)
    payloads, responses = load_captured("config_entry")
    register_jev_responses(aioclient_mock, responses)
    triage_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(triage_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert posted_bodies(aioclient_mock) == payloads
    assert [i for (domain, i) in ir.async_get(hass).issues if domain == DOMAIN] == []
    state = hass.states.get(triage_sensor_entity_id(hass, triage_entry))
    assert state is not None
    assert state.state == "0"
    assert state.attributes[ATTR_COUNTS] == {OPTION_TRANSIENT: 0, OPTION_NEEDS_REAUTH: 0, OPTION_DEAD: 0}
    unsure = state.attributes[ATTR_UNSURE]
    assert len(unsure) == 1
    assert unsure[0]["entry_id"] == stuck.entry_id
    assert unsure[0]["choice"] == OPTION_NONE
    assert unsure[0]["confidence"] == 0.83


def test_the_captured_config_entry_response_passes_validate_response() -> None:
    """The live response body has the shape the client accepts."""
    responses = load_captured("config_entry")[1]
    assert len(responses) == 1
    assert validate_response(responses[0]) == responses[0]


async def test_a_real_setup_retry_entry_round_trips_through_async_prepare(hass: HomeAssistant, failing_entry: Any) -> None:
    """A first run over a real setup_retry entry splits into exactly the request the live install sent."""
    await _stuck_entry(failing_entry)
    batch = await ConfigEntryRecipe().async_prepare(hass)
    assert batch.carried == {}
    assert split_batch(batch) == load_captured("config_entry")[0]


def test_the_captured_none_of_these_answer_gates_to_unsure() -> None:
    """none_of_these is not a verdict option, so a confident none_of_these still lands in unsure."""
    payload = load_captured("config_entry")[0][0]
    response = load_captured("config_entry")[1][0]
    recipe = ConfigEntryRecipe()
    assert recipe.gate(response["answers"]["c0"]) is None

    batch = Batch(state=payload["state"], questions=payload["questions"], subjects={"c0": {"entry_id": "coway_entry"}})
    result = classify(batch, response, CONFIG_ENTRY_OPTIONS, payload, recipe.gate)
    assert all(result["items"][option] == [] for option in CONFIG_ENTRY_OPTIONS)
    assert result["counts"] == {option: 0 for option in CONFIG_ENTRY_OPTIONS}
    assert result["unsure"] == [{"entry_id": "coway_entry", "confidence": 0.83, "choice": OPTION_NONE}]
