"""Timeline tests: a rejection survives noisy answers, and new device class cards are capped and ordered."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import (
    DEVICE_CLASS_ISSUE_PREFIX,
    DOMAIN,
    MAX_NEW_DEVICE_CLASS_CARDS_PER_RUN,
    OPTION_NONE,
    RECIPE_DEVICE_CLASS,
)
from custom_components.gutcheck.recipes.device_class_cards import sync_device_class_cards
from custom_components.gutcheck.recipes.safety import SafetyRules

from .conftest import (
    api_response,
    area_answer,
    device_class_sensor_entity_id,
    posted_bodies,
    register_jev_responses,
    register_unit_sensor,
)

PERCENT_CANDIDATES = ["battery", "humidity", "moisture", "power_factor"]


async def _run_again(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Drive a second device class run directly, the same way a scheduled refresh would."""
    await entry.runtime_data.coordinators[RECIPE_DEVICE_CLASS].async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)


async def _setup(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry, response: dict[str, object]
) -> None:
    """Register the API response, add and set up the entry."""
    register_jev_responses(aioclient_mock, [api_response(response)])
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_an_ignored_card_survives_an_unsure_answer(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """An ignored card stays ignored, unchanged, when its sensor's next answer is a low-confidence guess."""
    sensor = register_unit_sensor(hass, "a", unit="%", name="A")
    await _setup(hass, aioclient_mock, device_class_entry, {"s0": area_answer("battery", 0.9, PERCENT_CANDIDATES)})

    issue_id = f"{DEVICE_CLASS_ISSUE_PREFIX}{sensor.id}"
    ir.async_ignore_issue(hass, DOMAIN, issue_id, True)
    before = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert before is not None
    before_placeholders = before.translation_placeholders

    aioclient_mock.clear_requests()
    register_jev_responses(aioclient_mock, [api_response({"s0": area_answer("battery", 0.2, PERCENT_CANDIDATES)})])
    await _run_again(hass, device_class_entry)

    after = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert after is not None
    assert after.dismissed_version is not None
    assert after.translation_placeholders == before_placeholders


async def test_an_ignored_card_survives_a_confident_none_of_these_answer(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """An ignored card stays ignored when its sensor's next answer is a confident none of these."""
    sensor = register_unit_sensor(hass, "a", unit="%", name="A")
    await _setup(hass, aioclient_mock, device_class_entry, {"s0": area_answer("battery", 0.9, PERCENT_CANDIDATES)})

    issue_id = f"{DEVICE_CLASS_ISSUE_PREFIX}{sensor.id}"
    ir.async_ignore_issue(hass, DOMAIN, issue_id, True)

    aioclient_mock.clear_requests()
    register_jev_responses(aioclient_mock, [api_response({"s0": area_answer(OPTION_NONE, 0.9, PERCENT_CANDIDATES)})])
    await _run_again(hass, device_class_entry)

    after = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert after is not None
    assert after.dismissed_version is not None


async def test_an_open_card_stays_open_unchanged_on_an_unsure_answer(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """A card the user has not ignored stays open when its sensor's next answer is unsure."""
    register_unit_sensor(hass, "a", unit="%", name="A")
    await _setup(hass, aioclient_mock, device_class_entry, {"s0": area_answer("battery", 0.9, PERCENT_CANDIDATES)})
    issue_ids = {
        issue_id
        for domain, issue_id in ir.async_get(hass).issues
        if domain == DOMAIN and issue_id.startswith(DEVICE_CLASS_ISSUE_PREFIX)
    }
    assert len(issue_ids) == 1
    issue_id = next(iter(issue_ids))
    before = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert before is not None
    assert before.dismissed_version is None

    aioclient_mock.clear_requests()
    register_jev_responses(aioclient_mock, [api_response({"s0": area_answer("battery", 0.2, PERCENT_CANDIDATES)})])
    await _run_again(hass, device_class_entry)

    after = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert after is not None
    assert after.dismissed_version is None
    assert after.translation_placeholders == before.translation_placeholders


async def test_a_sensor_given_a_class_by_hand_is_no_longer_asked_and_its_card_is_deleted(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """A sensor classed by hand since the last run is dropped from the ask, and its card is swept."""
    sensor = register_unit_sensor(hass, "a", unit="%", name="A")
    await _setup(hass, aioclient_mock, device_class_entry, {"s0": area_answer("battery", 0.9, PERCENT_CANDIDATES)})
    issue_id = f"{DEVICE_CLASS_ISSUE_PREFIX}{sensor.id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    er.async_get(hass).async_update_entity(sensor.entity_id, device_class="battery")
    aioclient_mock.clear_requests()
    await _run_again(hass, device_class_entry)

    # No other sensor qualifies, so this run has nothing to ask and posts nothing at all.
    assert posted_bodies(aioclient_mock) == []
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_a_sensor_removed_from_the_registry_has_its_card_deleted(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """A sensor removed since the last run drops its card too, even though it can no longer be asked about."""
    sensor = register_unit_sensor(hass, "a", unit="%", name="A")
    await _setup(hass, aioclient_mock, device_class_entry, {"s0": area_answer("battery", 0.9, PERCENT_CANDIDATES)})
    issue_id = f"{DEVICE_CLASS_ISSUE_PREFIX}{sensor.id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    er.async_get(hass).async_remove(sensor.entity_id)
    aioclient_mock.clear_requests()
    await _run_again(hass, device_class_entry)

    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_twelve_confident_sensors_yield_ten_cards_most_confident_first_then_twelve_on_the_next_run(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """A run with more confident suggestions than the cap raises only the cap's worth, the rest arriving next run."""
    sensors = [register_unit_sensor(hass, f"p{i}", unit="%", name=f"Percent {i}") for i in range(12)]
    ordered = sorted(sensors, key=lambda entry: entry.entity_id)

    # Nine distinct confidences above the cap, two tied right at the cut, one clearly below it.
    confidences = [0.99, 0.98, 0.97, 0.96, 0.95, 0.94, 0.93, 0.92, 0.91, 0.90, 0.90, 0.80]
    confidence_by_id = {sensor.id: confidence for sensor, confidence in zip(sensors, confidences, strict=True)}
    tied = sorted((sensor for sensor in sensors if confidence_by_id[sensor.id] == 0.90), key=lambda entry: entry.id)
    assert len(tied) == 2
    winner, loser = tied

    answers = {
        f"s{index}": area_answer("battery", confidence_by_id[entry.id], PERCENT_CANDIDATES) for index, entry in enumerate(ordered)
    }
    await _setup(hass, aioclient_mock, device_class_entry, answers)

    state = hass.states.get(device_class_sensor_entity_id(hass, device_class_entry))
    assert state is not None
    assert len(state.attributes["items"]["suggested"]) == 12
    assert state.state == str(MAX_NEW_DEVICE_CLASS_CARDS_PER_RUN)

    card_ids = {
        issue_id
        for domain, issue_id in ir.async_get(hass).issues
        if domain == DOMAIN and issue_id.startswith(DEVICE_CLASS_ISSUE_PREFIX)
    }
    assert len(card_ids) == MAX_NEW_DEVICE_CLASS_CARDS_PER_RUN
    assert f"{DEVICE_CLASS_ISSUE_PREFIX}{winner.id}" in card_ids
    assert f"{DEVICE_CLASS_ISSUE_PREFIX}{loser.id}" not in card_ids
    below_cut = next(sensor for sensor in sensors if confidence_by_id[sensor.id] == 0.80)
    assert f"{DEVICE_CLASS_ISSUE_PREFIX}{below_cut.id}" not in card_ids

    aioclient_mock.clear_requests()
    register_jev_responses(aioclient_mock, [api_response(answers)])
    await _run_again(hass, device_class_entry)

    card_ids_after = {
        issue_id
        for domain, issue_id in ir.async_get(hass).issues
        if domain == DOMAIN and issue_id.startswith(DEVICE_CLASS_ISSUE_PREFIX)
    }
    assert len(card_ids_after) == 12

    state_after = hass.states.get(device_class_sensor_entity_id(hass, device_class_entry))
    assert state_after is not None
    assert state_after.state == "12"


async def test_three_open_cards_plus_twenty_five_new_confident_suggestions_yield_thirteen_cards(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """Existing open cards never count against the per-run cap on new ones."""
    for i in range(3):
        register_unit_sensor(hass, f"f{i}", unit="%", name=f"First {i}")
    first_answers = {f"s{index}": area_answer("battery", 0.9, PERCENT_CANDIDATES) for index in range(3)}
    await _setup(hass, aioclient_mock, device_class_entry, first_answers)

    card_ids = {
        issue_id
        for domain, issue_id in ir.async_get(hass).issues
        if domain == DOMAIN and issue_id.startswith(DEVICE_CLASS_ISSUE_PREFIX)
    }
    assert len(card_ids) == 3

    for i in range(25):
        register_unit_sensor(hass, f"s{i}", unit="%", name=f"Second {i}")
    answers = {f"s{index}": area_answer("battery", 0.9, PERCENT_CANDIDATES) for index in range(3 + 25)}

    aioclient_mock.clear_requests()
    register_jev_responses(aioclient_mock, [api_response(answers)])
    await _run_again(hass, device_class_entry)

    card_ids_after = {
        issue_id
        for domain, issue_id in ir.async_get(hass).issues
        if domain == DOMAIN and issue_id.startswith(DEVICE_CLASS_ISSUE_PREFIX)
    }
    assert len(card_ids_after) == 13


async def test_a_code_decided_suggestion_outranks_a_0_99_model_answer_when_the_cap_binds(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every code-decided suggestion (confidence 1.0) gets a card ahead of a 0.99 model answer when the cap binds."""
    monkeypatch.setattr("custom_components.gutcheck.recipes.device_class_cards.MAX_NEW_DEVICE_CLASS_CARDS_PER_RUN", 1)
    decided = register_unit_sensor(hass, "decided", unit="m")
    asked = register_unit_sensor(hass, "asked", unit="%")
    suggested = [
        {"registry_id": asked.id, "choice": "battery", "confidence": 0.99},
        {"registry_id": decided.id, "choice": "distance", "confidence": 1.0},
    ]

    sync_device_class_cards(hass, SafetyRules(None), suggested, {"battery": "Battery", "distance": "Distance"})

    card_ids = {
        issue_id
        for domain, issue_id in ir.async_get(hass).issues
        if domain == DOMAIN and issue_id.startswith(DEVICE_CLASS_ISSUE_PREFIX)
    }
    assert card_ids == {f"{DEVICE_CLASS_ISSUE_PREFIX}{decided.id}"}


async def test_an_item_with_no_confidence_still_becomes_a_card_sorted_last(hass: HomeAssistant) -> None:
    """A malformed item carrying no confidence at all defaults to the lowest sort priority, not a crash."""
    sensor = register_unit_sensor(hass, "no_confidence", unit="%")

    sync_device_class_cards(hass, SafetyRules(None), [{"registry_id": sensor.id, "choice": "battery"}], {"battery": "Battery"})

    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{DEVICE_CLASS_ISSUE_PREFIX}{sensor.id}") is not None
