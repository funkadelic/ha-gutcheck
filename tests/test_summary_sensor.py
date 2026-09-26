"""Tracer: each summary sensor counts its own open, unignored Repairs cards."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import (
    DOMAIN,
    HEALTH_ISSUE_PREFIX,
    OPTION_EXPECTED,
    OPTION_SAFE_TO_REMOVE,
    OPTION_WORTH_FIXING,
    RECIPE_HEALTH,
    RECIPE_UPDATES,
)

from .conftest import (
    api_response,
    choice_answer,
    recipe_sensor_entity_id,
    register_jev_responses_by_question,
    register_pending_update,
    register_unavailable_entity,
    score_answer,
)

_HEALTH_ANSWERS = {
    "e0": choice_answer(OPTION_WORTH_FIXING, 0.9),
    "e1": choice_answer(OPTION_EXPECTED, 0.9),
    "e2": choice_answer(OPTION_WORTH_FIXING, 0.9),
}


async def test_each_sensor_counts_its_own_open_cards_and_skips_ignored_ones(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """A card under another domain or recipe never counts; ignoring one drops it at once and it stays out after the next run."""
    first_entity_id = register_unavailable_entity(hass, "unique_a")
    register_unavailable_entity(hass, "unique_b")
    register_unavailable_entity(hass, "unique_c")
    register_pending_update(hass)

    ir.async_create_issue(
        hass,
        "other",
        f"{HEALTH_ISSUE_PREFIX}foreign",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="foreign",
    )

    register_jev_responses_by_question(
        aioclient_mock,
        {"e0": api_response(_HEALTH_ANSWERS), "u0": api_response({"u0": score_answer(2, 0.9)})},
    )
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    health_state = hass.states.get(recipe_sensor_entity_id(hass, mock_config_entry, RECIPE_HEALTH))
    assert health_state is not None
    assert health_state.state == "2"
    assert health_state.attributes["counts"] == {OPTION_EXPECTED: 1, OPTION_WORTH_FIXING: 2, OPTION_SAFE_TO_REMOVE: 0}

    updates_state = hass.states.get(recipe_sensor_entity_id(hass, mock_config_entry, RECIPE_UPDATES))
    assert updates_state is not None
    assert updates_state.state == "1"

    first_entry = er.async_get(hass).async_get(first_entity_id)
    assert first_entry is not None
    ir.async_ignore_issue(hass, DOMAIN, f"{HEALTH_ISSUE_PREFIX}{first_entry.id}", True)
    await hass.async_block_till_done()
    ignored_state = hass.states.get(recipe_sensor_entity_id(hass, mock_config_entry, RECIPE_HEALTH))
    assert ignored_state is not None
    assert ignored_state.state == "1"
    updates_after_ignore = hass.states.get(recipe_sensor_entity_id(hass, mock_config_entry, RECIPE_UPDATES))
    assert updates_after_ignore is not None
    assert updates_after_ignore.state == "1"

    aioclient_mock.clear_requests()
    register_jev_responses_by_question(aioclient_mock, {"e0": api_response(_HEALTH_ANSWERS)})
    await mock_config_entry.runtime_data.coordinators[RECIPE_HEALTH].async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)

    recounted_state = hass.states.get(recipe_sensor_entity_id(hass, mock_config_entry, RECIPE_HEALTH))
    assert recounted_state is not None
    assert recounted_state.state == "1"
    assert recounted_state.attributes["counts"][OPTION_WORTH_FIXING] == 2
