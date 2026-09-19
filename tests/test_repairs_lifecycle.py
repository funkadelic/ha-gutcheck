"""Timeline tests: issues survive ignores, renames and reloads; clear on recovery, reclassification or removal."""

from __future__ import annotations

from typing import Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import DOMAIN, HEALTH_ISSUE_PREFIX, OPTION_EXPECTED, OPTION_WORTH_FIXING
from custom_components.gutcheck.recipes.base import Item, RecipeResult
from custom_components.gutcheck.recipes.health import HealthRecipe

from .conftest import choice_answer, register_jev_responses


def _item(entity_id: str, registry_id: str) -> Item:
    return {
        "entity_id": entity_id,
        "registry_id": registry_id,
        "restored": False,
        "confidence": 0.9,
        "unavailable_for": "1 to 6 days",
    }


def _result(items: dict[str, list[Item]]) -> RecipeResult:
    return {"last_run": "", "counts": {}, "items": items, "unsure": [], "last_payload": None}


def _api_response(answers: dict[str, Any], input_tokens: int = 10) -> dict[str, Any]:
    return {"model": "jev-latest", "answers": answers, "usage": {"input_tokens": input_tokens, "output_tokens": 0}}


async def test_ignored_issue_survives_a_second_run_with_the_same_answers(hass: HomeAssistant) -> None:
    recipe = HealthRecipe(critical_label=None)
    result = _result({OPTION_WORTH_FIXING: [_item("sensor.a", "reg_a")]})

    await recipe.async_act(hass, result)
    ir.async_ignore_issue(hass, DOMAIN, f"{HEALTH_ISSUE_PREFIX}reg_a", True)

    await recipe.async_act(hass, result)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"{HEALTH_ISSUE_PREFIX}reg_a")
    assert issue is not None
    assert issue.dismissed_version is not None


async def test_renaming_the_entity_keeps_the_same_issue_id_and_ignore(hass: HomeAssistant) -> None:
    recipe = HealthRecipe(critical_label=None)
    await recipe.async_act(hass, _result({OPTION_WORTH_FIXING: [_item("sensor.a", "reg_a")]}))
    ir.async_ignore_issue(hass, DOMAIN, f"{HEALTH_ISSUE_PREFIX}reg_a", True)

    await recipe.async_act(hass, _result({OPTION_WORTH_FIXING: [_item("sensor.a_renamed", "reg_a")]}))

    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"{HEALTH_ISSUE_PREFIX}reg_a")
    assert issue is not None
    assert issue.dismissed_version is not None
    assert issue.translation_placeholders is not None
    assert issue.translation_placeholders["entity_id"] == "sensor\\.a\\_renamed"


async def test_state_becoming_available_deletes_the_issue_immediately(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.a", STATE_UNAVAILABLE)
    recipe = HealthRecipe(critical_label=None)
    await recipe.async_act(hass, _result({OPTION_WORTH_FIXING: [_item("sensor.a", "reg_a")]}))
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{HEALTH_ISSUE_PREFIX}reg_a") is not None

    hass.states.async_set("sensor.a", "on")
    await hass.async_block_till_done()

    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{HEALTH_ISSUE_PREFIX}reg_a") is None


async def test_state_becoming_unknown_also_counts_as_recovered(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.a", STATE_UNAVAILABLE)
    recipe = HealthRecipe(critical_label=None)
    await recipe.async_act(hass, _result({OPTION_WORTH_FIXING: [_item("sensor.a", "reg_a")]}))

    hass.states.async_set("sensor.a", STATE_UNKNOWN)
    await hass.async_block_till_done()

    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{HEALTH_ISSUE_PREFIX}reg_a") is None


async def test_already_recovered_before_the_next_run_is_cleared_at_once(hass: HomeAssistant) -> None:
    recipe = HealthRecipe(critical_label=None)
    hass.states.async_set("sensor.b", STATE_UNAVAILABLE)
    await recipe.async_act(hass, _result({OPTION_WORTH_FIXING: [_item("sensor.b", "reg_b")]}))

    # Recovers with no run in between: the next call's immediate check must
    # find it already gone, not wait for a state-change event that already passed.
    hass.states.async_set("sensor.b", "on")

    await recipe.async_act(hass, _result({OPTION_WORTH_FIXING: [_item("sensor.b", "reg_b")]}))

    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{HEALTH_ISSUE_PREFIX}reg_b") is None


async def test_reclassifying_worth_fixing_as_expected_deletes_its_issue(hass: HomeAssistant) -> None:
    recipe = HealthRecipe(critical_label=None)
    await recipe.async_act(hass, _result({OPTION_WORTH_FIXING: [_item("sensor.b", "reg_b")]}))
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{HEALTH_ISSUE_PREFIX}reg_b") is not None

    await recipe.async_act(hass, _result({OPTION_EXPECTED: [_item("sensor.b", "reg_b")]}))

    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{HEALTH_ISSUE_PREFIX}reg_b") is None


async def test_a_run_that_selects_nothing_deletes_every_health_issue(hass: HomeAssistant) -> None:
    recipe = HealthRecipe(critical_label=None)
    await recipe.async_act(hass, _result({OPTION_WORTH_FIXING: [_item("sensor.a", "reg_a")]}))

    await recipe.async_act(hass, _result({}))

    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{HEALTH_ISSUE_PREFIX}reg_a") is None


async def test_reload_keeps_the_issue_and_its_ignore(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    registry = er.async_get(hass)
    selectable = registry.async_get_or_create("sensor", "test", "unique_a")
    hass.states.async_set(selectable.entity_id, STATE_UNAVAILABLE)

    register_jev_responses(aioclient_mock, [_api_response({"e0": choice_answer(OPTION_WORTH_FIXING, 0.9)})])
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    issue_id = f"{HEALTH_ISSUE_PREFIX}{selectable.id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    ir.async_ignore_issue(hass, DOMAIN, issue_id, True)

    register_jev_responses(aioclient_mock, [_api_response({"e0": choice_answer(OPTION_WORTH_FIXING, 0.9)})])
    assert await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.dismissed_version is not None


async def test_removing_the_entry_deletes_every_issue_including_ignored(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    registry = er.async_get(hass)
    selectable = registry.async_get_or_create("sensor", "test", "unique_a")
    hass.states.async_set(selectable.entity_id, STATE_UNAVAILABLE)

    register_jev_responses(aioclient_mock, [_api_response({"e0": choice_answer(OPTION_WORTH_FIXING, 0.9)})])
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    issue_id = f"{HEALTH_ISSUE_PREFIX}{selectable.id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    ir.async_ignore_issue(hass, DOMAIN, issue_id, True)

    await hass.config_entries.async_remove(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_removing_an_entry_with_no_issues_succeeds(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.async_remove(mock_config_entry.entry_id)

    assert result["require_restart"] is False
