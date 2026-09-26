"""Tests against a real device class suggestions response captured from the target install."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.gutcheck.client import validate_response
from custom_components.gutcheck.const import OPTION_NONE, OPTION_SUGGESTED
from custom_components.gutcheck.recipes.device_class import DeviceClassRecipe
from custom_components.gutcheck.recipes.device_class_const import DEVICE_CLASS_NONE_DESCRIPTION
from custom_components.gutcheck.recipes.device_class_wording import instructions_for
from custom_components.gutcheck.recipes.gate import classify
from custom_components.gutcheck.recipes.shapes import Batch

from .conftest import area_answer, register_unit_sensor

FIXTURES = Path(__file__).parent / "fixtures" / "captured"


def _load(name: str) -> dict[str, Any]:
    """The captured fixture JSON at `name`, parsed."""
    return json.loads((FIXTURES / name).read_text())


def test_device_class_response_passes_validate_response() -> None:
    """A real captured device class response satisfies validate_response's documented shape."""
    response = _load("device_class_response.json")
    assert validate_response(response) == response


def test_the_captured_device_class_payload_and_response_come_from_the_same_run() -> None:
    """Regenerating one fixture without the other would make the tests below lie."""
    payload = _load("device_class_payload.json")
    response = _load("device_class_response.json")
    assert set(payload["questions"]) == set(response["answers"]), (
        "device_class_payload.json and device_class_response.json disagree on question ids; recapture both together"
    )


def test_every_device_class_answer_is_a_choice_with_probabilities_matching_its_question() -> None:
    """Every captured answer's probabilities cover exactly its own question's criteria, no more, no less."""
    payload = _load("device_class_payload.json")
    response = _load("device_class_response.json")
    for question_id, question in payload["questions"].items():
        answer = response["answers"][question_id]
        assert answer["type"] == "choice"
        assert set(answer["probabilities"]) == set(question["criteria"])


def test_the_captured_device_class_questions_match_the_current_wording() -> None:
    """A change to the instructions, their per-unit boundary cases, or the none-of-these wording leaves the pair stale."""
    payload = _load("device_class_payload.json")
    sensors = payload["state"]["sensors"]
    for index, (sensor, question) in enumerate(zip(sensors, payload["questions"].values(), strict=True)):
        assert question["instructions"] == instructions_for(sensor["unit"], index)
        assert question["criteria"][OPTION_NONE] == DEVICE_CLASS_NONE_DESCRIPTION


def test_area_answer_builder_matches_a_captured_device_class_answer_key_set() -> None:
    """area_answer() builds an answer with the same keys as a real captured choice answer; reused, not duplicated."""
    response = _load("device_class_response.json")
    captured_answer = next(iter(response["answers"].values()))
    built_answer = area_answer("water", 0.9, ["volume", "volume_storage"])
    assert set(built_answer) == set(captured_answer)


def test_the_capture_asks_a_sensor_whose_unit_fits_one_class() -> None:
    """At least one captured question offers exactly one class plus none of these, and its answer covers both."""
    payload = _load("device_class_payload.json")
    response = _load("device_class_response.json")
    one_class_questions = [
        (question_id, question) for question_id, question in payload["questions"].items() if len(question["criteria"]) == 2
    ]
    assert one_class_questions, "no captured question offers exactly one class plus none of these"
    for question_id, question in one_class_questions:
        answer = response["answers"][question_id]
        assert set(answer["probabilities"]) == set(question["criteria"])


def test_captured_device_class_answers_classify_with_every_sensor_accounted_for() -> None:
    """classify() places every captured sensor into suggested or unsure, none dropped, and prints the real spread."""
    payload = _load("device_class_payload.json")
    response = _load("device_class_response.json")
    subjects = {question_id: {"registry_id": question_id} for question_id in payload["questions"]}
    batch = Batch(state=payload["state"], questions=payload["questions"], subjects=subjects)

    result = classify(batch, response, (OPTION_SUGGESTED,), payload, DeviceClassRecipe(None).gate)

    suggested = result["items"][OPTION_SUGGESTED]
    unsure = result["unsure"]
    assert len(suggested) + len(unsure) == len(payload["questions"])
    for item in suggested:
        question = payload["questions"][
            next(qid for qid, subject in subjects.items() if subject["registry_id"] == item["registry_id"])
        ]
        assert item["choice"] in question["criteria"]

    confidences = sorted(response["answers"][qid]["confidence"] for qid in payload["questions"])
    print(f"real run: suggested={len(suggested)} unsure={len(unsure)}")
    print(f"real confidence spread: min={confidences[0]} median={confidences[len(confidences) // 2]} max={confidences[-1]}")
    print(f"real input_tokens: {response['usage']['input_tokens']}")


async def test_the_real_sensor_shapes_round_trip_through_async_prepare(hass: HomeAssistant) -> None:
    """Registering sensors shaped like the capture reproduces its sensor items and criteria.

    Order differs from the capture (the recipe asks in entity registry id
    order, not the capture's original order), so both sensors and criteria
    are compared as multisets via a canonical JSON form, not by position.
    """
    payload = _load("device_class_payload.json")
    sensors = payload["state"]["sensors"]

    for index, item in enumerate(sensors):
        entity_category = er.EntityCategory(item["entity_category"]) if item["entity_category"] else None
        register_unit_sensor(
            hass,
            f"dc{index}",
            unit=item["unit"],
            name=item["name"],
            platform=item["integration"],
            entity_category=entity_category,
            device_name=item["device_name"],
            manufacturer=item["manufacturer"],
            model=item["model"],
        )

    batch = await DeviceClassRecipe(None).async_prepare(hass)

    assert len(batch.state["sensors"]) == len(sensors)
    expected = sorted(json.dumps(item, sort_keys=True) for item in sensors)
    actual = sorted(json.dumps(item, sort_keys=True) for item in batch.state["sensors"])
    assert actual == expected

    expected_criteria = sorted(json.dumps(question["criteria"], sort_keys=True) for question in payload["questions"].values())
    actual_criteria = sorted(json.dumps(question["criteria"], sort_keys=True) for question in batch.questions.values())
    assert actual_criteria == expected_criteria
