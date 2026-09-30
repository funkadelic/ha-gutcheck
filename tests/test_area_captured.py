"""Tests against real area-suggestions answers captured from the target install.

The capture is a list of requests and a list of answers in the same order: the
recipe asks ten devices at a time, so a run of 48 goes out as five requests.
"""

from __future__ import annotations

import json
from typing import Any

from homeassistant.core import HomeAssistant

from custom_components.gutcheck.client import validate_response
from custom_components.gutcheck.const import OPTION_NONE, OPTION_SUGGESTED
from custom_components.gutcheck.recipes.area_const import AREA_CONFIDENCE_THRESHOLD, AREA_INSTRUCTIONS
from custom_components.gutcheck.recipes.area_describe import area_criteria
from custom_components.gutcheck.recipes.areas import AreaRecipe
from custom_components.gutcheck.recipes.gate import classify, gate_choice
from custom_components.gutcheck.recipes.shapes import Batch
from custom_components.gutcheck.split import split_batch

from .conftest import AreaEntitySpec, area_answer, captured_whole, create_areas, load_captured, register_area_device

SIZES = [10, 10, 10, 10, 8]
ASKED = sum(SIZES)


def _batch(whole: dict[str, Any]) -> Batch:
    """A batch over the capture with the area recipe's split fields, subjects left empty."""
    return Batch(
        state=whole["state"],
        questions=whole["questions"],
        subjects={},
        list_key="devices",
        template=AREA_INSTRUCTIONS,
    )


def test_every_captured_area_response_passes_validate_response() -> None:
    """Every real captured area response satisfies validate_response's documented shape."""
    responses = load_captured("area")[1]
    assert len(responses) == len(SIZES)
    for response in responses:
        assert validate_response(response) == response


def test_the_captured_area_payloads_and_responses_come_from_the_same_run() -> None:
    """Regenerating one fixture without the other would make the tests below lie."""
    payloads, responses = load_captured("area")
    assert len(payloads) == len(responses)
    for payload, response in zip(payloads, responses, strict=True):
        assert set(payload["questions"]) == set(response["answers"]), (
            "area_payload.json and area_response.json disagree on question ids; recapture both together"
        )


def test_every_area_answer_is_a_choice_with_probabilities_matching_its_question() -> None:
    """Every captured answer's probabilities cover exactly its own question's criteria, no more, no less."""
    for payload, response in zip(*load_captured("area"), strict=True):
        for question_id, question in payload["questions"].items():
            answer = response["answers"][question_id]
            assert answer["type"] == "choice"
            assert set(answer["probabilities"]) == set(question["criteria"])


def test_the_captured_area_requests_are_ten_devices_at_a_time() -> None:
    """The capture is 48 devices as four requests of ten and one of eight, one question per device."""
    payloads = load_captured("area")[0]
    assert [len(payload["questions"]) for payload in payloads] == SIZES
    for payload in payloads:
        assert len(payload["state"]["devices"]) == len(payload["questions"])


def test_the_captured_area_questions_match_the_current_wording() -> None:
    """A change to the area question's wording leaves the captured pair stale until it is recaptured.

    Each request counts its own devices from zero, so the instruction index is
    the question's position within its request, while the key stays global.
    """
    payloads = load_captured("area")[0]
    first_criteria = next(iter(payloads[0]["questions"].values()))["criteria"]
    for payload in payloads:
        for local, question in enumerate(payload["questions"].values()):
            assert question["instructions"] == AREA_INSTRUCTIONS.format(index=local)
            assert question["criteria"] == first_criteria


def test_area_answer_builder_matches_a_captured_answer_key_set() -> None:
    """area_answer() builds an answer with the same keys as a real captured choice answer."""
    captured_answer = next(iter(load_captured("area")[1][0]["answers"].values()))
    built_answer = area_answer("expected", 0.9, ["Kitchen", "Garage"])
    assert set(built_answer) == set(captured_answer)


def test_captured_area_answers_classify_with_every_device_accounted_for() -> None:
    """classify() places every captured device into suggested or unsure, none dropped, and prints the real spread."""
    _, responses = load_captured("area")
    whole, merged = captured_whole("area", "devices")
    area_names = tuple(name for name in whole["questions"]["d0"]["criteria"] if name != OPTION_NONE)
    subjects = {question_id: {"registry_id": question_id} for question_id in whole["questions"]}
    batch = Batch(state=whole["state"], questions=whole["questions"], subjects=subjects)

    def _gate(answer: object) -> str | None:
        """The area recipe's own gate: a confident, listed area, never none_of_these."""
        return OPTION_SUGGESTED if gate_choice(answer, area_names, AREA_CONFIDENCE_THRESHOLD) is not None else None

    result = classify(batch, merged, (OPTION_SUGGESTED,), whole, _gate)  # type: ignore[arg-type]

    suggested = result["items"][OPTION_SUGGESTED]
    unsure = result["unsure"]
    assert (len(suggested), len(unsure)) == (7, 41)
    assert len(suggested) + len(unsure) == ASKED
    for item in suggested:
        assert item["choice"] in area_names

    confidences = sorted(answer["confidence"] for response in responses for answer in response["answers"].values())
    input_tokens = sum(response["usage"]["input_tokens"] for response in responses)
    assert input_tokens == 18_632
    print(f"real run: suggested={len(suggested)} unsure={len(unsure)}")
    print(f"real confidence spread: min={confidences[0]} median={confidences[len(confidences) // 2]} max={confidences[-1]}")
    print(f"real input_tokens: {input_tokens} over {len(responses)} requests")


def test_the_captured_area_requests_are_what_split_batch_sends() -> None:
    """Rebuilding the capture as one batch and splitting it reproduces the captured requests exactly."""
    payloads = load_captured("area")[0]
    whole = captured_whole("area", "devices")[0]
    assert split_batch(_batch(whole)) == payloads


async def test_the_real_device_shapes_round_trip_through_prepare_and_split(hass: HomeAssistant) -> None:
    """Registering devices and areas shaped like the capture reproduces its items, criteria and request sizes.

    Order differs from the capture (the recipe asks in device registry id
    order, not the capture's original order), so devices are compared as
    multisets via a canonical JSON form, and each request is checked for its
    own re-indexed wording rather than by position.
    """
    payloads = load_captured("area")[0]
    devices = [item for payload in payloads for item in payload["state"]["devices"]]
    area_names = [name for name in payloads[0]["questions"]["d0"]["criteria"] if name != OPTION_NONE]
    create_areas(hass, *area_names)

    for index, item in enumerate(devices):
        entity_domains: list[str] = item["entity_domains"]
        device_classes: list[str] = item["device_classes"]
        entities: list[AreaEntitySpec] = [AreaEntitySpec(domain=domain) for domain in entity_domains]
        entities.extend(AreaEntitySpec(domain=entity_domains[0], device_class=device_class) for device_class in device_classes)
        register_area_device(
            hass,
            f"d{index}",
            domain=item["integration"],
            name=item["name"],
            manufacturer=item["manufacturer"],
            model=item["model"],
            entities=entities,
        )

    batch = await AreaRecipe(None).async_prepare(hass)
    assert len(batch.state["devices"]) == len(devices)
    sent = split_batch(batch)

    assert [len(request["questions"]) for request in sent] == SIZES
    expected_criteria = area_criteria({name: name for name in area_names})
    for request in sent:
        assert len(request["state"]["devices"]) == len(request["questions"])
        for local, question in enumerate(request["questions"].values()):
            assert question["instructions"] == AREA_INSTRUCTIONS.format(index=local)
            assert question["criteria"] == expected_criteria

    expected = sorted(json.dumps(item, sort_keys=True) for item in devices)
    actual = sorted(json.dumps(item, sort_keys=True) for request in sent for item in request["state"]["devices"])
    assert actual == expected
