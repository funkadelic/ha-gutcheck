"""Tests against real device class suggestions answers captured from the target install.

The capture is a list of requests and a list of answers in the same order: the
recipe asks ten sensors at a time, so a run of 58 goes out as six requests.
"""

from __future__ import annotations

import json
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.gutcheck.client import validate_response
from custom_components.gutcheck.const import OPTION_NONE, OPTION_SUGGESTED
from custom_components.gutcheck.recipes.device_class import DeviceClassRecipe
from custom_components.gutcheck.recipes.device_class_const import DEVICE_CLASS_INSTRUCTIONS, DEVICE_CLASS_NONE_DESCRIPTION
from custom_components.gutcheck.recipes.device_class_wording import instructions_for, template_for
from custom_components.gutcheck.recipes.gate import classify
from custom_components.gutcheck.recipes.shapes import Batch
from custom_components.gutcheck.split import split_batch

from .conftest import area_answer, captured_whole, load_captured, register_unit_sensor

SIZES = [10, 10, 10, 10, 10, 8]
ASKED = sum(SIZES)


def test_every_captured_device_class_response_passes_validate_response() -> None:
    """Every real captured device class response satisfies validate_response's documented shape."""
    responses = load_captured("device_class")[1]
    assert len(responses) == len(SIZES)
    for response in responses:
        assert validate_response(response) == response


def test_the_captured_device_class_payloads_and_responses_come_from_the_same_run() -> None:
    """Regenerating one fixture without the other would make the tests below lie."""
    payloads, responses = load_captured("device_class")
    assert len(payloads) == len(responses)
    for payload, response in zip(payloads, responses, strict=True):
        assert set(payload["questions"]) == set(response["answers"]), (
            "device_class_payload.json and device_class_response.json disagree on question ids; recapture both together"
        )


def test_every_device_class_answer_is_a_choice_with_probabilities_matching_its_question() -> None:
    """Every captured answer's probabilities cover exactly its own question's criteria, no more, no less."""
    for payload, response in zip(*load_captured("device_class"), strict=True):
        for question_id, question in payload["questions"].items():
            answer = response["answers"][question_id]
            assert answer["type"] == "choice"
            assert set(answer["probabilities"]) == set(question["criteria"])


def test_the_captured_device_class_requests_are_ten_sensors_at_a_time() -> None:
    """The capture is 58 sensors as five requests of ten and one of eight, one question per sensor."""
    payloads = load_captured("device_class")[0]
    assert [len(payload["questions"]) for payload in payloads] == SIZES
    for payload in payloads:
        assert len(payload["state"]["sensors"]) == len(payload["questions"])


def test_the_captured_device_class_questions_match_the_current_wording() -> None:
    """A change to the instructions, their per-unit boundary cases, or the none-of-these wording leaves the pair stale.

    Each request counts its own sensors from zero, so the instruction index is
    the question's position within its request, while the key stays global.
    """
    for payload in load_captured("device_class")[0]:
        pairs = zip(payload["state"]["sensors"], payload["questions"].values(), strict=True)
        for local, (sensor, question) in enumerate(pairs):
            assert question["instructions"] == instructions_for(sensor["unit"], local)
            assert question["criteria"][OPTION_NONE] == DEVICE_CLASS_NONE_DESCRIPTION


def test_area_answer_builder_matches_a_captured_device_class_answer_key_set() -> None:
    """area_answer() builds an answer with the same keys as a real captured choice answer; reused, not duplicated."""
    captured_answer = next(iter(load_captured("device_class")[1][0]["answers"].values()))
    built_answer = area_answer("water", 0.9, ["volume", "volume_storage"])
    assert set(built_answer) == set(captured_answer)


def test_the_capture_asks_a_sensor_whose_unit_fits_one_class() -> None:
    """At least one captured question offers exactly one class plus none of these, and its answer covers both."""
    one_class_questions = [
        (question_id, question, response)
        for payload, response in zip(*load_captured("device_class"), strict=True)
        for question_id, question in payload["questions"].items()
        if len(question["criteria"]) == 2
    ]
    assert one_class_questions, "no captured question offers exactly one class plus none of these"
    for question_id, question, response in one_class_questions:
        assert set(response["answers"][question_id]["probabilities"]) == set(question["criteria"])


def test_captured_device_class_answers_classify_with_every_sensor_accounted_for() -> None:
    """classify() places every captured sensor into suggested or unsure, none dropped, and prints the real spread."""
    responses = load_captured("device_class")[1]
    whole, merged = captured_whole("device_class", "sensors")
    subjects = {question_id: {"registry_id": question_id} for question_id in whole["questions"]}
    batch = Batch(state=whole["state"], questions=whole["questions"], subjects=subjects)

    result = classify(batch, merged, (OPTION_SUGGESTED,), whole, DeviceClassRecipe(None).gate)  # type: ignore[arg-type]

    suggested = result["items"][OPTION_SUGGESTED]
    unsure = result["unsure"]
    assert (len(suggested), len(unsure)) == (23, 35)
    assert len(suggested) + len(unsure) == ASKED
    for item in suggested:
        assert item["choice"] in whole["questions"][item["registry_id"]]["criteria"]

    confidences = sorted(answer["confidence"] for response in responses for answer in response["answers"].values())
    input_tokens = sum(response["usage"]["input_tokens"] for response in responses)
    assert input_tokens == 21_285
    print(f"real run: suggested={len(suggested)} unsure={len(unsure)}")
    print(f"real confidence spread: min={confidences[0]} median={confidences[len(confidences) // 2]} max={confidences[-1]}")
    print(f"real input_tokens: {input_tokens} over {len(responses)} requests")


def test_the_captured_device_class_requests_are_what_split_batch_sends() -> None:
    """Rebuilding the capture as one batch and splitting it reproduces the captured requests exactly."""
    payloads = load_captured("device_class")[0]
    whole = captured_whole("device_class", "sensors")[0]
    templates = {f"s{index}": template_for(sensor["unit"]) for index, sensor in enumerate(whole["state"]["sensors"])}
    batch = Batch(
        state=whole["state"],
        questions=whole["questions"],
        subjects={},
        list_key="sensors",
        template=DEVICE_CLASS_INSTRUCTIONS,
        templates=templates,
    )
    assert split_batch(batch) == payloads


async def test_the_real_sensor_shapes_round_trip_through_prepare_and_split(hass: HomeAssistant) -> None:
    """Registering sensors shaped like the capture reproduces its sensor items, criteria and request sizes.

    Order differs from the capture (the recipe asks in entity registry id
    order, not the capture's original order), so sensors and criteria are
    compared as multisets via a canonical JSON form, and each request is
    checked for its own re-indexed wording rather than by position.
    """
    payloads = load_captured("device_class")[0]
    sensors: list[dict[str, Any]] = [item for payload in payloads for item in payload["state"]["sensors"]]

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
    sent = split_batch(batch)

    assert [len(request["questions"]) for request in sent] == SIZES
    for request in sent:
        assert len(request["state"]["sensors"]) == len(request["questions"])
        pairs = zip(request["state"]["sensors"], request["questions"].values(), strict=True)
        for local, (sensor, question) in enumerate(pairs):
            assert question["instructions"] == instructions_for(sensor["unit"], local)

    expected = sorted(json.dumps(item, sort_keys=True) for item in sensors)
    actual = sorted(json.dumps(item, sort_keys=True) for request in sent for item in request["state"]["sensors"])
    assert actual == expected

    captured_criteria = [q["criteria"] for payload in payloads for q in payload["questions"].values()]
    sent_criteria = [q["criteria"] for request in sent for q in request["questions"].values()]
    assert sorted(json.dumps(c, sort_keys=True) for c in sent_criteria) == sorted(
        json.dumps(c, sort_keys=True) for c in captured_criteria
    )
