"""Ranking, the ten-card cap and rejection memory for diagnostic sensor cards."""

from __future__ import annotations

from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import (
    ATTR_COUNTS,
    ATTR_ITEMS,
    CONF_DAILY_BUDGET,
    CONF_HIDE_DIAGNOSTIC_ENABLED,
    DEFAULT_DAILY_BUDGET,
    DEVICE_CLASS_ISSUE_PREFIX,
    DOMAIN,
    HIDE_DIAGNOSTIC_ISSUE_PREFIX,
    ITEM_HELD_BACK,
    OPTION_NONE,
    OPTION_SUGGESTED,
    RECIPE_HIDE_DIAGNOSTIC,
)
from custom_components.gutcheck.recipes.hide_diagnostic_const import OPTION_DIAGNOSTIC, OPTION_PRIMARY

from .conftest import (
    KEPT_DEVICE_CLASS_OPTIONS,
    api_response,
    area_answer,
    hide_diagnostic_answer,
    posted_bodies,
    press_recipe_run,
    recipe_sensor_entity_id,
    register_jev_responses,
    register_jev_responses_by_question,
    register_unit_sensor,
    restart_config_entry,
)


def _open_ids(hass: HomeAssistant) -> set[str]:
    """Every hide card issue id currently in the registry."""
    return {
        issue_id
        for domain, issue_id in ir.async_get(hass).issues
        if domain == DOMAIN and issue_id.startswith(HIDE_DIAGNOSTIC_ISSUE_PREFIX)
    }


def _card(hass: HomeAssistant, sensor_id: str) -> ir.IssueEntry | None:
    """The hide card for this registry id, or None."""
    return ir.async_get(hass).async_get_issue(DOMAIN, f"{HIDE_DIAGNOSTIC_ISSUE_PREFIX}{sensor_id}")


async def _ignore(hass: HomeAssistant, issue_id: str) -> None:
    """Ignore a card through the repairs flow manager."""
    assert await async_setup_component(hass, "repairs", {})
    manager = repairs_flow_manager(hass)
    assert manager is not None
    result = await manager.async_init(DOMAIN, data={"issue_id": issue_id})
    result = await manager.async_configure(result["flow_id"], {"next_step_id": "ignore"})
    assert result["type"] is FlowResultType.ABORT


def _recipe_state(hass: HomeAssistant, entry: MockConfigEntry) -> tuple[str, dict]:
    """The recipe sensor's state and attributes."""
    state = hass.states.get(recipe_sensor_entity_id(hass, entry, RECIPE_HIDE_DIAGNOSTIC))
    assert state is not None
    return state.state, dict(state.attributes)


async def test_signal_strength_ranks_ahead_of_a_confident_model_answer_within_ten_new_cards(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hide_diagnostic_entry: MockConfigEntry
) -> None:
    """Twelve signal-strength sensors and three model-decided ones: the first run raises ten signal ones, the next the rest."""
    signal = [
        register_unit_sensor(hass, f"sig{i:02d}", unit="dBm", name=f"Signal {i}", original_device_class="signal_strength")
        for i in range(12)
    ]
    asked = [register_unit_sensor(hass, f"ssid{i}", unit=None, name=f"Network {i}") for i in range(3)]
    answers = {"h0": hide_diagnostic_answer(OPTION_DIAGNOSTIC, 1.0), "h1": hide_diagnostic_answer(OPTION_DIAGNOSTIC, 0.8)}
    answers["h2"] = hide_diagnostic_answer(OPTION_DIAGNOSTIC, 0.6)
    register_jev_responses(aioclient_mock, [api_response(answers), api_response(answers)])
    hide_diagnostic_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(hide_diagnostic_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    signal_ids = {f"{HIDE_DIAGNOSTIC_ISSUE_PREFIX}{sensor.id}" for sensor in signal}
    asked_ids = {f"{HIDE_DIAGNOSTIC_ISSUE_PREFIX}{sensor.id}" for sensor in asked}
    first_run = _open_ids(hass)
    assert len(first_run) == 10
    assert first_run <= signal_ids, "a model answer, even at 1.0, never outranks a code-decided sensor"
    state, attributes = _recipe_state(hass, hide_diagnostic_entry)
    assert state == "10"
    assert attributes[ATTR_COUNTS][OPTION_SUGGESTED] == 15
    held_back = {item["registry_id"] for item in attributes[ATTR_ITEMS][OPTION_SUGGESTED] if item.get(ITEM_HELD_BACK)}
    assert {f"{HIDE_DIAGNOSTIC_ISSUE_PREFIX}{registry_id}" for registry_id in held_back} == (signal_ids | asked_ids) - first_run

    await press_recipe_run(hass, hide_diagnostic_entry, RECIPE_HIDE_DIAGNOSTIC)

    assert _open_ids(hass) == signal_ids | asked_ids
    assert _recipe_state(hass, hide_diagnostic_entry)[0] == "15"


async def test_an_ignored_card_stays_ignored_and_uncounted_through_every_kind_of_rerun(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hide_diagnostic_entry: MockConfigEntry
) -> None:
    """Same answer, primary and unsure reruns keep the ignore; an open card whose sensor turns primary is deleted."""
    first = register_unit_sensor(hass, "net_a", unit=None, name="Network A")
    second = register_unit_sensor(hass, "net_b", unit=None, name="Network B")
    ordered = sorted((first, second), key=lambda sensor: sensor.entity_id)
    ignored, open_one = ordered
    diagnostic = api_response(
        {"h0": hide_diagnostic_answer(OPTION_DIAGNOSTIC, 0.9), "h1": hide_diagnostic_answer(OPTION_DIAGNOSTIC, 0.9)}
    )
    primary = api_response({"h0": hide_diagnostic_answer(OPTION_PRIMARY, 0.9), "h1": hide_diagnostic_answer(OPTION_PRIMARY, 0.9)})
    unsure = api_response({"h0": hide_diagnostic_answer(OPTION_NONE, 0.9), "h1": hide_diagnostic_answer(OPTION_DIAGNOSTIC, 0.2)})
    register_jev_responses(aioclient_mock, [diagnostic, diagnostic, primary, unsure, diagnostic])
    hide_diagnostic_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(hide_diagnostic_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    await _ignore(hass, f"{HIDE_DIAGNOSTIC_ISSUE_PREFIX}{ignored.id}")

    for expected_open in (True, False, False, True):
        await press_recipe_run(hass, hide_diagnostic_entry, RECIPE_HIDE_DIAGNOSTIC)
        card = _card(hass, ignored.id)
        assert card is not None
        assert card.dismissed_version is not None
        assert (_card(hass, open_one.id) is not None) is expected_open
    assert _recipe_state(hass, hide_diagnostic_entry)[0] == "1"


async def test_an_ignored_card_outlives_an_open_device_class_card_a_restart_and_being_asked_again(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hide_diagnostic_entry: MockConfigEntry
) -> None:
    """The user's rejection is never the price of a device class card, a restart, or a later diagnostic answer."""
    sensor = register_unit_sensor(hass, "phone_pct", unit="%", name="Phone level")
    register_jev_responses_by_question(
        aioclient_mock, {"h0": api_response({"h0": hide_diagnostic_answer(OPTION_DIAGNOSTIC, 0.9)})}
    )
    hide_diagnostic_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(hide_diagnostic_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    await _ignore(hass, f"{HIDE_DIAGNOSTIC_ISSUE_PREFIX}{sensor.id}")

    both_on = {**KEPT_DEVICE_CLASS_OPTIONS, CONF_HIDE_DIAGNOSTIC_ENABLED: True, CONF_DAILY_BUDGET: DEFAULT_DAILY_BUDGET}
    aioclient_mock.clear_requests()
    answer = area_answer("battery", 0.9, ["battery", "humidity", "moisture", "power_factor"])
    register_jev_responses_by_question(aioclient_mock, {"s0": api_response({"s0": answer})})
    result = await hass.config_entries.options.async_init(hide_diagnostic_entry.entry_id)
    await hass.config_entries.options.async_configure(result["flow_id"], both_on)
    await hass.async_block_till_done(wait_background_tasks=True)
    device_class_card = ir.async_get(hass).async_get_issue(DOMAIN, f"{DEVICE_CLASS_ISSUE_PREFIX}{sensor.id}")
    assert device_class_card is not None
    assert device_class_card.dismissed_version is None

    for step in ("run", "restart"):
        if step == "run":
            await press_recipe_run(hass, hide_diagnostic_entry, RECIPE_HIDE_DIAGNOSTIC)
        else:
            await restart_config_entry(hass, hide_diagnostic_entry)
        card = _card(hass, sensor.id)
        assert card is not None
        assert card.dismissed_version is not None
        assert [set(body["questions"]) for body in posted_bodies(aioclient_mock)] == [{"s0"}]

    ir.async_ignore_issue(hass, DOMAIN, f"{DEVICE_CLASS_ISSUE_PREFIX}{sensor.id}", True)
    aioclient_mock.clear_requests()
    register_jev_responses_by_question(
        aioclient_mock, {"h0": api_response({"h0": hide_diagnostic_answer(OPTION_DIAGNOSTIC, 0.9)})}
    )
    await press_recipe_run(hass, hide_diagnostic_entry, RECIPE_HIDE_DIAGNOSTIC)

    assert [set(body["questions"]) for body in posted_bodies(aioclient_mock)] == [{"h0"}]
    card = _card(hass, sensor.id)
    assert card is not None
    assert card.dismissed_version is not None
    assert _recipe_state(hass, hide_diagnostic_entry)[0] == "0"


async def test_an_ignored_card_outlives_the_user_hiding_the_sensor_by_hand_and_unhiding_it(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hide_diagnostic_entry: MockConfigEntry
) -> None:
    """Hiding a rejected sensor by hand keeps its ignored card, so unhiding it later raises no new card."""
    sensor = register_unit_sensor(hass, "phone_wifi", unit=None, name="Wi-Fi connection")
    diagnostic = api_response({"h0": hide_diagnostic_answer(OPTION_DIAGNOSTIC, 0.9)})
    register_jev_responses(aioclient_mock, [diagnostic, diagnostic])
    hide_diagnostic_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(hide_diagnostic_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    await _ignore(hass, f"{HIDE_DIAGNOSTIC_ISSUE_PREFIX}{sensor.id}")
    registry = er.async_get(hass)

    for hidden_by in (er.RegistryEntryHider.USER, None):
        registry.async_update_entity(sensor.entity_id, hidden_by=hidden_by)
        await press_recipe_run(hass, hide_diagnostic_entry, RECIPE_HIDE_DIAGNOSTIC)
        card = _card(hass, sensor.id)
        assert card is not None
        assert card.dismissed_version is not None
        assert _recipe_state(hass, hide_diagnostic_entry)[0] == "0"
    assert len(posted_bodies(aioclient_mock)) == 2
