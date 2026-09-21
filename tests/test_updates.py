"""Tracer-style tests for the update review recipe: selection through to the sensor."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from homeassistant.components.update import DATA_COMPONENT, UpdateEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import DOMAIN, OPTION_FEATURE, OPTION_POSSIBLY_BREAKING, OPTION_ROUTINE, RECIPE_UPDATES
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


@dataclass
class _FakeUpdateEntity:
    """A minimal stand-in for the real update platform entity the notes fetch looks up."""

    available: bool = True
    supported_features: UpdateEntityFeature = field(default_factory=lambda: UpdateEntityFeature.RELEASE_NOTES)
    notes: str | None = None
    raises: Exception | None = None

    async def async_release_notes(self) -> str | None:
        """Return the configured notes, or raise the configured exception."""
        if self.raises is not None:
            raise self.raises
        return self.notes


def _install_update_entities(hass: HomeAssistant, entities: dict[str, _FakeUpdateEntity]) -> None:
    """Install fake update platform entities for the release-notes fetch path to find.

    The update recipe looks entities up through hass.data[DATA_COMPONENT],
    the same in-process path HA's own websocket handler uses; standing up a
    full update platform just to exercise that lookup would be a much larger
    fixture for the same behavior.
    """
    hass.data[DATA_COMPONENT] = SimpleNamespace(get_entity=entities.get)


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


async def test_fetched_release_notes_are_cleaned_into_the_payload(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """An entity supporting release notes gets its fetched, cleaned notes in the payload."""
    entry = register_pending_update(hass, release_summary="A short summary.")
    _install_update_entities(hass, {entry.entity_id: _FakeUpdateEntity(notes="# Full release notes\n\nWith **details**.")})
    register_jev_responses(aioclient_mock, [api_response({"u0": score_answer(0, 0.9)})])
    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    body = posted_bodies(aioclient_mock)[0]
    update_item = body["state"]["updates"][0]
    assert update_item["release_notes"] == "Full release notes With details."
    assert update_item["release_summary"] == "A short summary."


async def test_entity_without_release_notes_feature_uses_release_summary_only(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """An entity that does not support release notes is still scored, on its release summary alone."""
    entry = register_pending_update(hass, release_summary="Bug fixes only.")
    _install_update_entities(hass, {entry.entity_id: _FakeUpdateEntity(supported_features=UpdateEntityFeature(0))})
    register_jev_responses(aioclient_mock, [api_response({"u0": score_answer(0, 0.9)})])
    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    body = posted_bodies(aioclient_mock)[0]
    assert body["state"]["updates"][0]["release_notes"] == "Bug fixes only."


async def test_notes_fetch_that_raises_falls_back_to_the_summary_without_failing(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A raised exception during the notes fetch falls back to release_summary; the run still succeeds."""
    entry = register_pending_update(hass, release_summary="Fallback summary.")
    _install_update_entities(hass, {entry.entity_id: _FakeUpdateEntity(raises=RuntimeError("boom"))})
    register_jev_responses(aioclient_mock, [api_response({"u0": score_answer(0, 0.9)})])
    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    bodies = posted_bodies(aioclient_mock)
    assert len(bodies) == 1
    assert bodies[0]["state"]["updates"][0]["release_notes"] == "Fallback summary."


async def test_empty_notes_major_jump_entity_produces_no_question(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """An entity with no note text at all and a major version jump is decided in code, never asked about."""
    register_pending_update(hass, "update_excluded", release_summary=None)
    register_pending_update(hass, "update_asked", release_summary="Something to say.")
    register_jev_responses(aioclient_mock, [api_response({"u0": score_answer(0, 0.9)})])
    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    body = posted_bodies(aioclient_mock)[0]
    assert len(body["questions"]) == 1
    assert len(body["state"]["updates"]) == 1


async def test_posted_update_item_has_exactly_the_seven_allowed_fields(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The payload sends only the whitelisted fields for each update, never skipped_version or release_url."""
    entry = register_pending_update(hass, release_url="https://example.com/release", release_summary="Notes here.")
    _install_update_entities(hass, {entry.entity_id: _FakeUpdateEntity(notes="More notes.")})
    register_jev_responses(aioclient_mock, [api_response({"u0": score_answer(0, 0.9)})])
    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    body = posted_bodies(aioclient_mock)[0]
    expected_fields = {
        "integration",
        "installed_version",
        "latest_version",
        "version_jump",
        "title",
        "release_summary",
        "release_notes",
    }
    assert set(body["state"]["updates"][0]) == expected_fields


async def test_pressing_the_run_button_sends_one_request_and_updates_the_sensor(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Pressing the update review's Run button sends exactly one request and updates the sensor."""
    register_pending_update(hass)
    register_jev_responses(aioclient_mock, [api_response({"u0": score_answer(0, 0.9)})])
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(posted_bodies(aioclient_mock)) == 1

    button_entity_id = er.async_get(hass).async_get_entity_id(
        "button", DOMAIN, f"{mock_config_entry.entry_id}_{RECIPE_UPDATES}_run"
    )
    assert button_entity_id is not None

    aioclient_mock.clear_requests()
    register_jev_responses(aioclient_mock, [api_response({"u0": score_answer(2, 0.9)})])
    await hass.services.async_call("button", "press", {"entity_id": button_entity_id}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(posted_bodies(aioclient_mock)) == 1
    state = hass.states.get(updates_sensor_entity_id(hass, mock_config_entry))
    assert state is not None
    assert state.state == "1"
    assert state.attributes["counts"][OPTION_POSSIBLY_BREAKING] == 1
