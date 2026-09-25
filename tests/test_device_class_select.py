"""Tests for sensor selection, unit narrowing, the code-decided single-class path, and question alignment."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.gutcheck.budget import estimate_tokens
from custom_components.gutcheck.const import (
    DEVICE_CLASS_CONFIDENCE_THRESHOLD,
    DEVICE_CLASS_INSTRUCTIONS,
    OPTION_NONE,
    OPTION_SUGGESTED,
)
from custom_components.gutcheck.recipes.device_class import DeviceClassRecipe
from custom_components.gutcheck.recipes.device_class_describe import candidate_classes
from custom_components.gutcheck.recipes.gate import classify
from custom_components.gutcheck.split import split_batch

from .conftest import area_answer, device_class_sensor_entity_id, posted_bodies, register_unit_sensor

LIMIT = "custom_components.gutcheck.budget.REQUEST_TOKEN_LIMIT"
PERCENT_CANDIDATES = ["battery", "humidity", "moisture", "power_factor"]
GALLON_CANDIDATES = ["volume", "volume_storage", "water"]


def test_candidate_classes_for_percent() -> None:
    """A percent unit narrows to exactly the four classes whose unit set accepts it."""
    assert candidate_classes("%") == ("battery", "humidity", "moisture", "power_factor")


def test_candidate_classes_for_meters() -> None:
    """A meters unit narrows to distance alone."""
    assert candidate_classes("m") == ("distance",)


def test_candidate_classes_for_an_unmatched_unit() -> None:
    """A unit no device class accepts narrows to nothing."""
    assert candidate_classes("pages") == ()


def test_candidate_classes_for_no_unit() -> None:
    """A missing unit narrows to nothing, even though some classes list None as an accepted unit."""
    assert candidate_classes(None) == ()


async def test_a_sensor_with_no_unit_is_never_selected(hass: HomeAssistant) -> None:
    """A sensor with no unit at all is excluded."""
    register_unit_sensor(hass, "no_unit", unit=None)

    batch = await DeviceClassRecipe(critical_label=None).async_prepare(hass)

    assert batch.subjects == {}
    assert batch.carried.get(OPTION_SUGGESTED, []) == []


async def test_a_sensor_with_an_original_device_class_is_never_selected(hass: HomeAssistant) -> None:
    """A sensor whose integration already set a device class is excluded."""
    register_unit_sensor(hass, "has_original", unit="%", original_device_class="battery")

    batch = await DeviceClassRecipe(critical_label=None).async_prepare(hass)

    assert batch.subjects == {}
    assert batch.carried.get(OPTION_SUGGESTED, []) == []


async def test_a_sensor_with_a_user_set_device_class_is_never_selected(hass: HomeAssistant) -> None:
    """A sensor with a user-set override and no integration class is still excluded."""
    register_unit_sensor(hass, "has_override", unit="%", device_class="battery")

    batch = await DeviceClassRecipe(critical_label=None).async_prepare(hass)

    assert batch.subjects == {}
    assert batch.carried.get(OPTION_SUGGESTED, []) == []


async def test_a_disabled_sensor_is_never_selected(hass: HomeAssistant) -> None:
    """A disabled sensor is excluded."""
    register_unit_sensor(hass, "disabled", unit="%", disabled_by=er.RegistryEntryDisabler.USER)

    batch = await DeviceClassRecipe(critical_label=None).async_prepare(hass)

    assert batch.subjects == {}
    assert batch.carried.get(OPTION_SUGGESTED, []) == []


async def test_a_sensor_carrying_the_critical_label_is_never_selected(hass: HomeAssistant) -> None:
    """A sensor carrying the configured critical label is excluded."""
    register_unit_sensor(hass, "critical", unit="%", labels=frozenset({"critical"}))

    batch = await DeviceClassRecipe(critical_label="critical").async_prepare(hass)

    assert batch.subjects == {}
    assert batch.carried.get(OPTION_SUGGESTED, []) == []


async def test_a_sensor_whose_device_carries_the_critical_label_is_never_selected(hass: HomeAssistant) -> None:
    """A sensor whose device carries the configured critical label is excluded."""
    register_unit_sensor(hass, "device_critical", unit="%", device_name="Device", device_labels=frozenset({"critical"}))

    batch = await DeviceClassRecipe(critical_label="critical").async_prepare(hass)

    assert batch.subjects == {}
    assert batch.carried.get(OPTION_SUGGESTED, []) == []


async def test_a_non_sensor_entity_with_a_unit_is_never_selected(hass: HomeAssistant) -> None:
    """A number entity, even with a matching unit and no device class, is excluded: only sensors qualify."""
    er.async_get(hass).async_get_or_create("number", "test", "num", unit_of_measurement="%")

    batch = await DeviceClassRecipe(critical_label=None).async_prepare(hass)

    assert batch.subjects == {}
    assert batch.carried.get(OPTION_SUGGESTED, []) == []


async def test_a_unit_no_class_accepts_is_never_asked_suggested_or_unsure(hass: HomeAssistant) -> None:
    """A sensor whose unit no device class accepts is dropped entirely, not even carried."""
    register_unit_sensor(hass, "pages", unit="pages")

    batch = await DeviceClassRecipe(critical_label=None).async_prepare(hass)

    assert batch.subjects == {}
    assert batch.carried.get(OPTION_SUGGESTED, []) == []


async def test_a_unit_one_class_accepts_is_carried_at_full_confidence_with_no_question(hass: HomeAssistant) -> None:
    """A sensor whose unit exactly one device class accepts is decided in code, not asked."""
    sensor = register_unit_sensor(hass, "meters", unit="m")

    batch = await DeviceClassRecipe(critical_label=None).async_prepare(hass)

    assert batch.questions == {}
    assert batch.carried[OPTION_SUGGESTED] == [
        {"registry_id": sensor.id, "entity_id": sensor.entity_id, "choice": "distance", "confidence": 1.0}
    ]


async def test_with_only_one_class_sensors_setup_posts_nothing_and_still_raises_a_card(
    hass: HomeAssistant, aioclient_mock, device_class_entry
) -> None:
    """A run where every qualifying sensor narrows to one class sends no request and still raises a card."""
    sensor = register_unit_sensor(hass, "meters", unit="m", name="Distance Sensor")
    device_class_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(device_class_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert posted_bodies(aioclient_mock) == []
    state = hass.states.get(device_class_sensor_entity_id(hass, device_class_entry))
    assert state is not None
    assert state.state == "1"
    suggested = state.attributes["items"]["suggested"]
    assert len(suggested) == 1
    assert suggested[0]["registry_id"] == sensor.id
    assert suggested[0]["confidence"] == 1.0


async def test_a_carried_sensor_between_two_asked_ones_does_not_desync_the_question_index(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A code-decided sensor sitting between two asked ones leaves the state list and question index aligned."""
    percent = register_unit_sensor(hass, "a", unit="%", name="A")
    meters = register_unit_sensor(hass, "b", unit="m", name="B")
    gallons = register_unit_sensor(hass, "c", unit="gal", name="C")

    recipe = DeviceClassRecipe(critical_label=None)
    batch = await recipe.async_prepare(hass)

    assert list(batch.questions) == ["s0", "s1"]
    assert batch.subjects["s0"]["registry_id"] == percent.id
    assert batch.subjects["s1"]["registry_id"] == gallons.id
    assert batch.state["sensors"][1]["name"] == "C"
    assert batch.questions["s1"]["instructions"] == DEVICE_CLASS_INSTRUCTIONS.format(index=1)
    assert batch.carried[OPTION_SUGGESTED] == [
        {"registry_id": meters.id, "entity_id": meters.entity_id, "choice": "distance", "confidence": 1.0}
    ]

    response = {
        "model": "jev-latest",
        "answers": {"s1": area_answer("water", 0.9, GALLON_CANDIDATES)},
        "usage": {"input_tokens": 5, "output_tokens": 0},
    }
    result = classify(batch, response, recipe.options, None, recipe.gate)
    suggested = {item["registry_id"]: item["choice"] for item in result["items"][OPTION_SUGGESTED]}
    assert suggested == {meters.id: "distance", gallons.id: "water"}

    whole = {"state": batch.state, "model": "jev-latest", "questions": batch.questions}
    monkeypatch.setattr(LIMIT, estimate_tokens(whole) - 1)
    payloads = split_batch(batch)

    assert len(payloads) >= 2
    for request in payloads:
        assert list(request["state"]) == ["sensors"]
        instructions = [question["instructions"] for question in request["questions"].values()]
        assert instructions == [DEVICE_CLASS_INSTRUCTIONS.format(index=local) for local in range(len(instructions))]


async def test_each_questions_criteria_are_exactly_its_own_candidates_plus_none(hass: HomeAssistant) -> None:
    """A gallons sensor's question offers exactly volume, volume_storage, water and none of these."""
    register_unit_sensor(hass, "tank", unit="gal")

    batch = await DeviceClassRecipe(critical_label=None).async_prepare(hass)

    question = next(iter(batch.questions.values()))
    assert set(question["criteria"].keys()) == {*GALLON_CANDIDATES, OPTION_NONE}


async def test_criteria_use_home_assistants_own_translated_class_names(hass: HomeAssistant) -> None:
    """The criteria description for each candidate is Home Assistant's own translated name for it."""
    register_unit_sensor(hass, "tank", unit="gal")

    batch = await DeviceClassRecipe(critical_label=None).async_prepare(hass)

    criteria = next(iter(batch.questions.values()))["criteria"]
    assert criteria["volume"] == "Volume"
    assert criteria["volume_storage"] == "Stored volume"
    assert criteria["water"] == "Water"


def test_gate_accepts_a_known_class_at_the_threshold_and_rejects_just_below() -> None:
    """A known class at exactly the confidence threshold is accepted; a hair below is not."""
    recipe = DeviceClassRecipe(critical_label=None)

    assert recipe.gate(area_answer("battery", DEVICE_CLASS_CONFIDENCE_THRESHOLD, PERCENT_CANDIDATES)) == OPTION_SUGGESTED
    assert recipe.gate(area_answer("battery", DEVICE_CLASS_CONFIDENCE_THRESHOLD - 0.0001, PERCENT_CANDIDATES)) is None


def test_gate_rejects_a_confident_none_of_these() -> None:
    """A confident "none of these" is never a suggestion: it is not a device class."""
    recipe = DeviceClassRecipe(critical_label=None)

    assert recipe.gate(area_answer(OPTION_NONE, 0.9, PERCENT_CANDIDATES)) is None


async def test_an_off_criteria_answer_lands_in_unsure_with_no_choice_and_raises_no_card(hass: HomeAssistant) -> None:
    """A confident answer naming a class outside its own question's options lands in unsure, with no choice carried."""
    register_unit_sensor(hass, "battery_pct", unit="%")
    recipe = DeviceClassRecipe(critical_label=None)
    batch = await recipe.async_prepare(hass)
    question_id = next(iter(batch.questions))
    response = {
        "model": "jev-latest",
        "answers": {question_id: area_answer("temperature", 0.9, PERCENT_CANDIDATES)},
        "usage": {"input_tokens": 5, "output_tokens": 0},
    }

    result = classify(batch, response, recipe.options, None, recipe.gate)

    assert result["items"][OPTION_SUGGESTED] == []
    assert len(result["unsure"]) == 1
    assert "choice" not in result["unsure"][0]


async def test_sensors_are_asked_in_entity_id_order_and_two_runs_post_identical_bodies(hass: HomeAssistant) -> None:
    """Two runs over the same, unchanged registries build the same state and questions, in entity id order."""
    register_unit_sensor(hass, "b", unit="%", name="B")
    register_unit_sensor(hass, "a", unit="gal", name="A")

    first_batch = await DeviceClassRecipe(critical_label=None).async_prepare(hass)
    second_batch = await DeviceClassRecipe(critical_label=None).async_prepare(hass)

    assert first_batch.state == second_batch.state
    assert first_batch.questions == second_batch.questions
    order = [subject["entity_id"] for subject in first_batch.subjects.values()]
    assert order == sorted(order)
