"""Timeline tests: issues survive ignores, renames and reloads; clear on recovery, reclassification or removal."""

from __future__ import annotations

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
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
)
from custom_components.gutcheck.recipes.health import HealthRecipe

from .conftest import api_response, choice_answer, health_item, health_result, posted_bodies, register_jev_responses


async def test_ignored_issue_survives_a_second_run_with_the_same_answers(hass: HomeAssistant) -> None:
    """Re-running with identical answers must not clear the user's ignore."""
    recipe = HealthRecipe(critical_label=None)
    result = health_result({OPTION_WORTH_FIXING: [health_item("sensor.a", "reg_a")]})

    await recipe.async_act(hass, result)
    ir.async_ignore_issue(hass, DOMAIN, f"{HEALTH_ISSUE_PREFIX}reg_a", True)

    await recipe.async_act(hass, result)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"{HEALTH_ISSUE_PREFIX}reg_a")
    assert issue is not None
    assert issue.dismissed_version is not None


async def test_renaming_the_entity_keeps_the_same_issue_id_and_ignore(hass: HomeAssistant) -> None:
    """The issue id is keyed to the registry id, not the entity_id, so a rename keeps the same card and its ignore."""
    recipe = HealthRecipe(critical_label=None)
    await recipe.async_act(hass, health_result({OPTION_WORTH_FIXING: [health_item("sensor.a", "reg_a")]}))
    ir.async_ignore_issue(hass, DOMAIN, f"{HEALTH_ISSUE_PREFIX}reg_a", True)

    await recipe.async_act(hass, health_result({OPTION_WORTH_FIXING: [health_item("sensor.a_renamed", "reg_a")]}))

    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"{HEALTH_ISSUE_PREFIX}reg_a")
    assert issue is not None
    assert issue.dismissed_version is not None
    assert issue.translation_placeholders is not None
    assert issue.translation_placeholders["entity_id"] == "sensor.a_renamed"


async def test_restore_drops_a_finding_labelled_critical_since_the_run(hass: HomeAssistant) -> None:
    """Tagging an entity critical must retire its card, even on the free restore path."""
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("sensor", "test", "critical_later")
    recipe = HealthRecipe(critical_label="critical")
    result = health_result({OPTION_WORTH_FIXING: [health_item(entry.entity_id, entry.id)]})

    await recipe.async_act(hass, result)
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{HEALTH_ISSUE_PREFIX}{entry.id}") is not None

    registry.async_update_entity(entry.entity_id, labels={"critical"})
    await recipe.restore(hass, result)

    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{HEALTH_ISSUE_PREFIX}{entry.id}") is None
    # Out of the result too, so the summary sensor does not keep listing it.
    assert result["items"][OPTION_WORTH_FIXING] == []
    assert result["counts"][OPTION_WORTH_FIXING] == 0


async def test_restore_drops_a_newly_critical_entity_from_every_bucket(hass: HomeAssistant) -> None:
    """A critical entity must not sit in safe-to-remove either, waiting for the next paid run."""
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("sensor", "test", "critical_safe")
    recipe = HealthRecipe(critical_label="critical")
    result = health_result({OPTION_SAFE_TO_REMOVE: [health_item(entry.entity_id, entry.id)]})
    result["counts"] = {OPTION_SAFE_TO_REMOVE: 1}

    registry.async_update_entity(entry.entity_id, labels={"critical"})
    await recipe.restore(hass, result)

    assert result["items"][OPTION_SAFE_TO_REMOVE] == []
    assert result["counts"][OPTION_SAFE_TO_REMOVE] == 0


async def test_restore_drops_a_finding_whose_entity_was_disabled_since_the_run(hass: HomeAssistant) -> None:
    """A disabled entity is out of scope for a run, so its stored card goes too."""
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("sensor", "test", "disabled_later")
    recipe = HealthRecipe(critical_label=None)
    result = health_result({OPTION_WORTH_FIXING: [health_item(entry.entity_id, entry.id)]})

    await recipe.async_act(hass, result)
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{HEALTH_ISSUE_PREFIX}{entry.id}") is not None

    registry.async_update_entity(entry.entity_id, disabled_by=er.RegistryEntryDisabler.USER)
    await recipe.restore(hass, result)

    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{HEALTH_ISSUE_PREFIX}{entry.id}") is None


async def test_restore_drops_a_renamed_entity_that_is_now_critical(hass: HomeAssistant) -> None:
    """A rename must not smuggle a newly critical entity past the restore filter.

    The stored entity id stops resolving once the entity is renamed, so the
    lookup has to go through the registry id the finding also carries.
    """
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("sensor", "test", "renamed_then_critical")
    recipe = HealthRecipe(critical_label="critical")
    result = health_result({OPTION_WORTH_FIXING: [health_item(entry.entity_id, entry.id)]})

    await recipe.async_act(hass, result)
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{HEALTH_ISSUE_PREFIX}{entry.id}") is not None

    registry.async_update_entity(entry.entity_id, new_entity_id="sensor.renamed_by_the_user", labels={"critical"})
    await recipe.restore(hass, result)

    assert result["items"][OPTION_WORTH_FIXING] == []
    assert result["counts"][OPTION_WORTH_FIXING] == 0
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{HEALTH_ISSUE_PREFIX}{entry.id}") is None


async def test_restore_of_a_result_with_no_worth_fixing_bucket_clears_the_cards(hass: HomeAssistant) -> None:
    """An older stored result may carry no worth-fixing key at all; restoring it must not fail."""
    recipe = HealthRecipe(critical_label=None)
    await recipe.async_act(hass, health_result({OPTION_WORTH_FIXING: [health_item("sensor.a", "reg_e")]}))
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{HEALTH_ISSUE_PREFIX}reg_e") is not None

    await recipe.restore(hass, health_result({}))

    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{HEALTH_ISSUE_PREFIX}reg_e") is None


async def test_restore_keeps_a_finding_whose_entity_is_no_longer_registered(hass: HomeAssistant) -> None:
    """An unknown entity id is left alone, so a rename does not discard an ignore."""
    recipe = HealthRecipe(critical_label="critical")

    await recipe.restore(hass, health_result({OPTION_WORTH_FIXING: [health_item("sensor.never_registered", "reg_c")]}))

    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{HEALTH_ISSUE_PREFIX}reg_c") is not None


async def test_restore_keeps_a_finding_whose_entity_is_still_eligible(hass: HomeAssistant) -> None:
    """The critical filter must not retire ordinary findings on restore."""
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("sensor", "test", "still_eligible")
    recipe = HealthRecipe(critical_label="critical")
    result = health_result({OPTION_WORTH_FIXING: [health_item(entry.entity_id, entry.id)]})

    await recipe.restore(hass, result)

    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{HEALTH_ISSUE_PREFIX}{entry.id}") is not None


async def test_state_becoming_available_deletes_the_issue_immediately(hass: HomeAssistant) -> None:
    """Recovery to an available state deletes the issue at once, without waiting for the next run."""
    hass.states.async_set("sensor.a", STATE_UNAVAILABLE)
    recipe = HealthRecipe(critical_label=None)
    await recipe.async_act(hass, health_result({OPTION_WORTH_FIXING: [health_item("sensor.a", "reg_a")]}))
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{HEALTH_ISSUE_PREFIX}reg_a") is not None

    hass.states.async_set("sensor.a", "on")
    await hass.async_block_till_done()

    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{HEALTH_ISSUE_PREFIX}reg_a") is None


async def test_state_becoming_unknown_does_not_count_as_recovered(hass: HomeAssistant) -> None:
    """Unknown says nothing about whether the entity came back, so the card stays until it really does."""
    hass.states.async_set("sensor.a", STATE_UNAVAILABLE)
    recipe = HealthRecipe(critical_label=None)
    await recipe.async_act(hass, health_result({OPTION_WORTH_FIXING: [health_item("sensor.a", "reg_a")]}))

    hass.states.async_set("sensor.a", STATE_UNKNOWN)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{HEALTH_ISSUE_PREFIX}reg_a") is not None

    hass.states.async_set("sensor.a", "on")
    await hass.async_block_till_done()

    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{HEALTH_ISSUE_PREFIX}reg_a") is None


async def test_entity_removed_from_the_state_machine_leaves_the_issue_alone(hass: HomeAssistant) -> None:
    """An entity removed from the state machine, not recovered, leaves its issue untouched."""
    hass.states.async_set("sensor.a", STATE_UNAVAILABLE)
    recipe = HealthRecipe(critical_label=None)
    await recipe.async_act(hass, health_result({OPTION_WORTH_FIXING: [health_item("sensor.a", "reg_a")]}))

    hass.states.async_remove("sensor.a")
    await hass.async_block_till_done()

    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{HEALTH_ISSUE_PREFIX}reg_a") is not None


async def test_already_recovered_before_the_next_run_is_cleared_at_once(hass: HomeAssistant) -> None:
    """A recovery whose state-change event has already passed is still caught by the next run's immediate check."""
    recipe = HealthRecipe(critical_label=None)
    hass.states.async_set("sensor.b", STATE_UNAVAILABLE)
    await recipe.async_act(hass, health_result({OPTION_WORTH_FIXING: [health_item("sensor.b", "reg_b")]}))

    # Recovers with no run in between: the next call's immediate check must
    # find it already gone, not wait for a state-change event that already passed.
    hass.states.async_set("sensor.b", "on")

    await recipe.async_act(hass, health_result({OPTION_WORTH_FIXING: [health_item("sensor.b", "reg_b")]}))

    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{HEALTH_ISSUE_PREFIX}reg_b") is None


async def test_reclassifying_worth_fixing_as_expected_deletes_its_issue(hass: HomeAssistant) -> None:
    """A later run that reclassifies the entity out of worth-fixing deletes its stale issue."""
    recipe = HealthRecipe(critical_label=None)
    await recipe.async_act(hass, health_result({OPTION_WORTH_FIXING: [health_item("sensor.b", "reg_b")]}))
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{HEALTH_ISSUE_PREFIX}reg_b") is not None

    await recipe.async_act(hass, health_result({OPTION_EXPECTED: [health_item("sensor.b", "reg_b")]}))

    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{HEALTH_ISSUE_PREFIX}reg_b") is None


async def test_a_run_that_selects_nothing_deletes_every_health_issue(hass: HomeAssistant) -> None:
    """A run that selects nothing clears every previously raised health issue."""
    recipe = HealthRecipe(critical_label=None)
    await recipe.async_act(hass, health_result({OPTION_WORTH_FIXING: [health_item("sensor.a", "reg_a")]}))

    await recipe.async_act(hass, health_result({}))

    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{HEALTH_ISSUE_PREFIX}reg_a") is None


async def test_reload_keeps_the_issue_and_its_ignore(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A reload inside the interval restores for free and keeps the existing issue and its ignore."""
    registry = er.async_get(hass)
    selectable = registry.async_get_or_create("sensor", "test", "unique_a")
    hass.states.async_set(selectable.entity_id, STATE_UNAVAILABLE)

    register_jev_responses(aioclient_mock, [api_response({"e0": choice_answer(OPTION_WORTH_FIXING, 0.9)})])
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    issue_id = f"{HEALTH_ISSUE_PREFIX}{selectable.id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    ir.async_ignore_issue(hass, DOMAIN, issue_id, True)

    # No second response queued on purpose: a reload inside the interval restores
    # the stored result for free, so it must not reach the API at all.
    assert await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(posted_bodies(aioclient_mock)) == 1
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.dismissed_version is not None


async def test_removing_the_entry_deletes_every_issue_including_ignored(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Removing the config entry deletes every issue, ignored ones included."""
    registry = er.async_get(hass)
    selectable = registry.async_get_or_create("sensor", "test", "unique_a")
    hass.states.async_set(selectable.entity_id, STATE_UNAVAILABLE)

    register_jev_responses(aioclient_mock, [api_response({"e0": choice_answer(OPTION_WORTH_FIXING, 0.9)})])
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
    """Removing an entry that never raised an issue still succeeds, with no restart required."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.async_remove(mock_config_entry.entry_id)

    assert result["require_restart"] is False
