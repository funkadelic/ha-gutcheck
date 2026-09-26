"""Timeline tests: critical label suggestions' own restart, restore-filter and missing-label paths."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import label_registry as lr
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import (
    CONF_CRITICAL_LABEL,
    CRITICAL_LABEL_ISSUE_PREFIX,
    DOMAIN,
    OPTION_SUGGESTED,
    RECIPE_CRITICAL_LABEL,
)
from custom_components.gutcheck.recipes.critical_label import CriticalLabelRecipe
from custom_components.gutcheck.recipes.critical_label_const import OPTION_NOT_CRITICAL
from custom_components.gutcheck.recipes.shapes import RecipeResult

from .conftest import (
    api_response,
    critical_label_answer,
    posted_bodies,
    press_recipe_run,
    recipe_sensor_entity_id,
    register_jev_responses,
    register_unit_sensor,
    restart_config_entry,
)


async def test_restart_within_the_week_keeps_open_ignored_and_held_back_cards_with_no_request(
    hass: HomeAssistant, freezer: Any, aioclient_mock: AiohttpClientMocker, critical_label_entry: MockConfigEntry
) -> None:
    """A restart inside the cadence window restores for free: open and held-back items stay put, ignored stays ignored."""
    freezer.move_to("2026-01-01T00:00:00-08:00")
    lr.async_get(hass).async_create("Critical")
    valves = [register_unit_sensor(hass, f"v{i}", unit=None, name=f"Valve {i}", domain="valve") for i in range(11)]
    ordered = sorted(valves, key=lambda entry: entry.entity_id)
    held_back = ordered[-1]
    answers = {
        f"k{index}": critical_label_answer("critical", 0.6 if entry.id == held_back.id else 0.9)
        for index, entry in enumerate(ordered)
    }
    register_jev_responses(aioclient_mock, [api_response(answers)])
    critical_label_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(critical_label_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    open_ids = {f"{CRITICAL_LABEL_ISSUE_PREFIX}{entry.id}" for entry in ordered if entry.id != held_back.id}
    held_back_id = f"{CRITICAL_LABEL_ISSUE_PREFIX}{held_back.id}"
    registry = ir.async_get(hass)
    assert {issue_id for domain, issue_id in registry.issues if domain == DOMAIN} == open_ids
    assert registry.async_get_issue(DOMAIN, held_back_id) is None

    to_ignore = next(iter(open_ids))
    ir.async_ignore_issue(hass, DOMAIN, to_ignore, True)

    posted_before = len(posted_bodies(aioclient_mock))
    freezer.move_to("2026-01-04T00:00:00-08:00")
    await restart_config_entry(hass, critical_label_entry)

    assert len(posted_bodies(aioclient_mock)) == posted_before
    registry = ir.async_get(hass)
    assert {issue_id for domain, issue_id in registry.issues if domain == DOMAIN} == open_ids
    ignored = registry.async_get_issue(DOMAIN, to_ignore)
    assert ignored is not None
    assert ignored.dismissed_version is not None
    assert registry.async_get_issue(DOMAIN, held_back_id) is None


async def test_restore_drops_labelled_by_hand_disabled_and_removed_entities_from_every_bucket_and_unsure(
    hass: HomeAssistant,
) -> None:
    """A restore drops an entity labelled by hand, disabled, or removed since the run, recounting and clearing its card."""
    label = lr.async_get(hass).async_create("Critical")
    registry = er.async_get(hass)
    labelled_by_hand = registry.async_get_or_create("valve", "test", "labelled_by_hand")
    turned_disabled = registry.async_get_or_create("valve", "test", "turned_disabled")
    removed = registry.async_get_or_create("valve", "test", "removed")
    kept = registry.async_get_or_create("valve", "test", "kept")

    recipe = CriticalLabelRecipe(critical_label=label.label_id)
    result: RecipeResult = {
        "last_run": "",
        "counts": {OPTION_SUGGESTED: 2, OPTION_NOT_CRITICAL: 1},
        "items": {
            OPTION_SUGGESTED: [
                {"registry_id": labelled_by_hand.id, "confidence": 0.9},
                {"registry_id": kept.id, "confidence": 0.9},
            ],
            OPTION_NOT_CRITICAL: [{"registry_id": turned_disabled.id, "confidence": 0.9}],
        },
        "unsure": [{"registry_id": removed.id}],
        "last_payload": None,
    }
    await recipe.async_act(hass, result)
    kept_issue_id = f"{CRITICAL_LABEL_ISSUE_PREFIX}{kept.id}"
    dropped_issue_id = f"{CRITICAL_LABEL_ISSUE_PREFIX}{labelled_by_hand.id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, kept_issue_id) is not None
    assert ir.async_get(hass).async_get_issue(DOMAIN, dropped_issue_id) is not None

    registry.async_update_entity(labelled_by_hand.entity_id, labels={label.label_id})
    registry.async_update_entity(turned_disabled.entity_id, disabled_by=er.RegistryEntryDisabler.USER)
    registry.async_remove(removed.entity_id)

    await recipe.restore(hass, result)

    assert result["items"][OPTION_SUGGESTED] == [{"registry_id": kept.id, "confidence": 0.9}]
    assert result["counts"][OPTION_SUGGESTED] == 1
    assert result["items"][OPTION_NOT_CRITICAL] == []
    assert result["counts"][OPTION_NOT_CRITICAL] == 0
    assert result["unsure"] == []
    assert ir.async_get(hass).async_get_issue(DOMAIN, kept_issue_id) is not None
    assert ir.async_get(hass).async_get_issue(DOMAIN, dropped_issue_id) is None


async def test_clearing_the_label_then_restarting_empties_the_result_and_keeps_the_ignored_card(
    hass: HomeAssistant, freezer: Any, aioclient_mock: AiohttpClientMocker, critical_label_entry: MockConfigEntry
) -> None:
    """Clearing the critical label and restarting empties the recipe's result, no request, the ignored card kept.

    Picking the label again and running afterward raises the open card once
    more, while the ignored one stays ignored.
    """
    freezer.move_to("2026-01-01T00:00:00-08:00")
    lr.async_get(hass).async_create("Critical")
    valve = register_unit_sensor(hass, "v0", unit=None, name="Valve", domain="valve")
    register_jev_responses(aioclient_mock, [api_response({"k0": critical_label_answer("critical", 0.9)})])
    critical_label_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(critical_label_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    issue_id = f"{CRITICAL_LABEL_ISSUE_PREFIX}{valve.id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    ir.async_ignore_issue(hass, DOMAIN, issue_id, True)

    posted_before = len(posted_bodies(aioclient_mock))
    cleared_options = {key: value for key, value in critical_label_entry.options.items() if key != CONF_CRITICAL_LABEL}
    hass.config_entries.async_update_entry(critical_label_entry, options=cleared_options)
    freezer.move_to("2026-01-04T00:00:00-08:00")
    await restart_config_entry(hass, critical_label_entry)

    assert len(posted_bodies(aioclient_mock)) == posted_before
    sensor_state = hass.states.get(recipe_sensor_entity_id(hass, critical_label_entry, RECIPE_CRITICAL_LABEL))
    assert sensor_state is not None
    assert sensor_state.state == "0"
    assert sensor_state.attributes["items"][OPTION_SUGGESTED] == []
    assert sensor_state.attributes["unsure"] == []
    kept_issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert kept_issue is not None
    assert kept_issue.dismissed_version is not None

    hass.config_entries.async_update_entry(
        critical_label_entry, options={**critical_label_entry.options, CONF_CRITICAL_LABEL: "critical"}
    )
    register_jev_responses(aioclient_mock, [api_response({"k0": critical_label_answer("critical", 0.9)})])
    await press_recipe_run(hass, critical_label_entry, RECIPE_CRITICAL_LABEL)

    reraised = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert reraised is not None
    assert reraised.dismissed_version is not None


async def test_deleting_the_configured_label_then_restarting_empties_the_result_with_no_request(
    hass: HomeAssistant, freezer: Any, aioclient_mock: AiohttpClientMocker, critical_label_entry: MockConfigEntry
) -> None:
    """Deleting the configured label from the label registry, then restarting, empties the result with no request."""
    freezer.move_to("2026-01-01T00:00:00-08:00")
    label = lr.async_get(hass).async_create("Critical")
    valve = register_unit_sensor(hass, "v0", unit=None, name="Valve", domain="valve")
    register_jev_responses(aioclient_mock, [api_response({"k0": critical_label_answer("critical", 0.9)})])
    critical_label_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(critical_label_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    issue_id = f"{CRITICAL_LABEL_ISSUE_PREFIX}{valve.id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    posted_before = len(posted_bodies(aioclient_mock))
    lr.async_get(hass).async_delete(label.label_id)
    freezer.move_to("2026-01-04T00:00:00-08:00")
    await restart_config_entry(hass, critical_label_entry)

    assert len(posted_bodies(aioclient_mock)) == posted_before
    sensor_state = hass.states.get(recipe_sensor_entity_id(hass, critical_label_entry, RECIPE_CRITICAL_LABEL))
    assert sensor_state is not None
    assert sensor_state.state == "0"
    assert sensor_state.attributes["items"][OPTION_SUGGESTED] == []
    assert sensor_state.attributes["counts"][OPTION_SUGGESTED] == 0
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
