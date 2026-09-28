"""Ranking, the per-run cap, and rejection memory across reruns for critical label suggestion cards."""

from __future__ import annotations

from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import label_registry as lr
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import CRITICAL_LABEL_ISSUE_PREFIX, DOMAIN, ITEM_HELD_BACK, RECIPE_CRITICAL_LABEL
from custom_components.gutcheck.recipes.critical_label_cards import sync_critical_label_cards
from custom_components.gutcheck.recipes.critical_label_const import OPTION_CRITICAL, OPTION_NOT_CRITICAL
from custom_components.gutcheck.recipes.safety import SafetyRules

from .conftest import (
    api_response,
    critical_label_answer,
    press_recipe_run,
    recipe_sensor_entity_id,
    register_jev_responses,
    register_unit_sensor,
)


async def _ignore_card(hass: HomeAssistant, issue_id: str) -> None:
    """Ignore issue_id through Home Assistant's own repairs flow manager."""
    assert await async_setup_component(hass, "repairs", {})
    manager = repairs_flow_manager(hass)
    assert manager is not None
    result = await manager.async_init(DOMAIN, data={"issue_id": issue_id})
    assert result["type"] is FlowResultType.MENU
    result = await manager.async_configure(result["flow_id"], {"next_step_id": "ignore"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "suggestion_ignored"


def _open_ids(registry: ir.IssueRegistry) -> set[str]:
    """Every open critical label card's registry id, prefix stripped."""
    return {
        issue_id.removeprefix(CRITICAL_LABEL_ISSUE_PREFIX)
        for domain, issue_id in registry.issues
        if domain == DOMAIN and issue_id.startswith(CRITICAL_LABEL_ISSUE_PREFIX)
    }


async def test_sync_skips_an_item_whose_entity_no_longer_resolves(hass: HomeAssistant) -> None:
    """An item naming a registry id with no matching, qualifying entity raises no card."""
    lr.async_get(hass).async_create("Critical")
    sync_critical_label_cards(hass, SafetyRules("critical"), [{"registry_id": "gone"}])

    assert not any(issue_id.startswith(CRITICAL_LABEL_ISSUE_PREFIX) for _domain, issue_id in ir.async_get(hass).issues)


async def test_code_decided_items_outrank_model_items_within_the_cap(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, critical_label_entry: MockConfigEntry
) -> None:
    """Twelve code-decided smoke sensors and three valves answered critical: the first run cards only smoke sensors."""
    lr.async_get(hass).async_create("Critical")
    smoke_sensors = [
        register_unit_sensor(hass, f"smoke{i:02d}", domain="binary_sensor", unit=None, original_device_class="smoke")
        for i in range(12)
    ]
    valve_a = register_unit_sensor(hass, "valve_a", domain="valve", unit=None)
    valve_b = register_unit_sensor(hass, "valve_b", domain="valve", unit=None)
    valve_c = register_unit_sensor(hass, "valve_c", domain="valve", unit=None)
    answer = api_response(
        {
            "k0": critical_label_answer(OPTION_CRITICAL, 1.0),
            "k1": critical_label_answer(OPTION_CRITICAL, 0.8),
            "k2": critical_label_answer(OPTION_CRITICAL, 0.6),
        }
    )
    register_jev_responses(aioclient_mock, [answer, answer])
    critical_label_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(critical_label_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    state = hass.states.get(recipe_sensor_entity_id(hass, critical_label_entry, RECIPE_CRITICAL_LABEL))
    assert state is not None
    assert state.state == "10"

    registry = ir.async_get(hass)
    smoke_ids = {sensor.id for sensor in smoke_sensors}
    all_ids = smoke_ids | {valve_a.id, valve_b.id, valve_c.id}
    open_after_first_run = _open_ids(registry)
    assert len(open_after_first_run) == 10
    assert open_after_first_run <= smoke_ids, "a valve (model-decided) must never outrank a smoke sensor (code-decided)"

    suggested = state.attributes["items"]["suggested"]
    held_back_ids = {item["registry_id"] for item in suggested if item.get(ITEM_HELD_BACK)}
    assert held_back_ids == all_ids - open_after_first_run
    assert len(held_back_ids) == 5

    await press_recipe_run(hass, critical_label_entry, RECIPE_CRITICAL_LABEL)

    assert _open_ids(registry) == all_ids


async def test_ignored_card_stays_ignored_after_a_rerun_with_the_same_answer(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, critical_label_entry: MockConfigEntry
) -> None:
    """A rerun with the same critical answer leaves an ignored card ignored and uncounted."""
    lr.async_get(hass).async_create("Critical")
    valve = register_unit_sensor(hass, "water_valve", domain="valve", unit=None)
    answer = api_response({"k0": critical_label_answer(OPTION_CRITICAL, 0.9)})
    register_jev_responses(aioclient_mock, [answer, answer])
    critical_label_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(critical_label_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    issue_id = f"{CRITICAL_LABEL_ISSUE_PREFIX}{valve.id}"
    await _ignore_card(hass, issue_id)

    await press_recipe_run(hass, critical_label_entry, RECIPE_CRITICAL_LABEL)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.dismissed_version is not None
    state = hass.states.get(recipe_sensor_entity_id(hass, critical_label_entry, RECIPE_CRITICAL_LABEL))
    assert state is not None
    assert state.state == "0"


async def test_ignored_card_stays_ignored_when_the_entity_comes_back_not_critical_or_unsure(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, critical_label_entry: MockConfigEntry
) -> None:
    """An ignored card survives a rerun where the entity answers not_critical, and one where it answers unsure."""
    lr.async_get(hass).async_create("Critical")
    valve = register_unit_sensor(hass, "water_valve", domain="valve", unit=None)
    register_jev_responses(
        aioclient_mock,
        [
            api_response({"k0": critical_label_answer(OPTION_CRITICAL, 0.9)}),
            api_response({"k0": critical_label_answer(OPTION_NOT_CRITICAL, 0.9)}),
            api_response({"k0": critical_label_answer(OPTION_CRITICAL, 0.2)}),
        ],
    )
    critical_label_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(critical_label_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    issue_id = f"{CRITICAL_LABEL_ISSUE_PREFIX}{valve.id}"
    await _ignore_card(hass, issue_id)
    registry = ir.async_get(hass)

    await press_recipe_run(hass, critical_label_entry, RECIPE_CRITICAL_LABEL)
    issue = registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.dismissed_version is not None

    await press_recipe_run(hass, critical_label_entry, RECIPE_CRITICAL_LABEL)
    issue = registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.dismissed_version is not None


async def test_open_card_is_deleted_when_the_entity_comes_back_not_critical(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, critical_label_entry: MockConfigEntry
) -> None:
    """An open (unignored) card is deleted once the model no longer backs it."""
    lr.async_get(hass).async_create("Critical")
    valve = register_unit_sensor(hass, "water_valve", domain="valve", unit=None)
    register_jev_responses(
        aioclient_mock,
        [
            api_response({"k0": critical_label_answer(OPTION_CRITICAL, 0.9)}),
            api_response({"k0": critical_label_answer(OPTION_NOT_CRITICAL, 0.9)}),
        ],
    )
    critical_label_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(critical_label_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    issue_id = f"{CRITICAL_LABEL_ISSUE_PREFIX}{valve.id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    await press_recipe_run(hass, critical_label_entry, RECIPE_CRITICAL_LABEL)

    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
