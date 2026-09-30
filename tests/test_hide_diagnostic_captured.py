"""Tests against a real diagnostic sensor suggestions run captured from the target install.

The capture is a list of requests and a list of answers in the same order: the
recipe asks ten sensors at a time, so a run of 122 goes out as 13 requests.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant

from custom_components.gutcheck.client import validate_response
from custom_components.gutcheck.const import OPTION_NONE, OPTION_SUGGESTED, SUBJECTS_PER_REQUEST
from custom_components.gutcheck.recipes.gate import classify
from custom_components.gutcheck.recipes.hide_diagnostic import HideDiagnosticRecipe
from custom_components.gutcheck.recipes.hide_diagnostic_const import (
    HIDE_DIAGNOSTIC_CONFIDENCE_THRESHOLD,
    HIDE_DIAGNOSTIC_CRITERIA,
    HIDE_DIAGNOSTIC_INSTRUCTIONS,
    OPTION_PRIMARY,
)
from custom_components.gutcheck.recipes.shapes import Batch
from custom_components.gutcheck.split import merge, split_batch

from .conftest import hide_diagnostic_answer, register_unit_sensor

FIXTURES = Path(__file__).parent / "fixtures" / "captured"

ASKED = 122
REQUESTS = 13


def _load(name: str) -> list[dict[str, Any]]:
    """The captured fixture JSON at `name`: one entry per request."""
    return json.loads((FIXTURES / name).read_text())


def _payloads() -> list[dict[str, Any]]:
    """The captured requests, in the order they were sent."""
    return _load("hide_diagnostic_payload.json")


def _responses() -> list[dict[str, Any]]:
    """The captured answers, one per request, in the same order."""
    return _load("hide_diagnostic_response.json")


def test_every_captured_hide_diagnostic_response_passes_validate_response() -> None:
    """Every real captured response satisfies validate_response's documented shape."""
    responses = _responses()
    assert len(responses) == REQUESTS
    for response in responses:
        assert validate_response(response) == response


def test_the_captured_hide_diagnostic_payloads_and_responses_come_from_the_same_run() -> None:
    """Regenerating one fixture without the other would make the tests below lie."""
    payloads = _payloads()
    responses = _responses()
    assert len(payloads) == len(responses)
    for payload, response in zip(payloads, responses, strict=True):
        assert set(payload["questions"]) == set(response["answers"]), (
            "hide_diagnostic_payload.json and hide_diagnostic_response.json disagree on question ids; recapture both together"
        )


def test_every_captured_hide_diagnostic_answer_is_a_choice_with_probabilities_matching_its_question() -> None:
    """Every captured answer's probabilities cover exactly its own question's criteria, no more, no less."""
    for payload, response in zip(_payloads(), _responses(), strict=True):
        for question_id, question in payload["questions"].items():
            answer = response["answers"][question_id]
            assert answer["type"] == "choice"
            assert set(answer["probabilities"]) == set(question["criteria"])


def test_the_captured_hide_diagnostic_requests_are_ten_sensors_at_a_time() -> None:
    """The capture is 122 asked sensors as twelve requests of ten and one of two, each with the seven whitelisted fields."""
    payloads = _payloads()
    sizes = [len(payload["state"]["sensors"]) for payload in payloads]
    assert sizes == [SUBJECTS_PER_REQUEST] * (REQUESTS - 1) + [ASKED - SUBJECTS_PER_REQUEST * (REQUESTS - 1)]
    assert sum(sizes) == ASKED
    fields = {"name", "device_name", "manufacturer", "model", "integration", "unit", "state_class"}
    for payload in payloads:
        assert len(payload["questions"]) == len(payload["state"]["sensors"])
        assert all(set(item) == fields for item in payload["state"]["sensors"])


def test_the_captured_hide_diagnostic_questions_match_the_current_wording() -> None:
    """A change to the instructions or criteria leaves the captured pair stale until both are recaptured.

    Each request counts its own sensors from zero, so the instruction index
    is the question's position within its request, while the key stays global.
    """
    for payload in _payloads():
        for local, question in enumerate(payload["questions"].values()):
            assert question["instructions"] == HIDE_DIAGNOSTIC_INSTRUCTIONS.format(index=local)
            assert question["criteria"] == HIDE_DIAGNOSTIC_CRITERIA


def test_hide_diagnostic_answer_builder_matches_a_captured_answer_key_set() -> None:
    """hide_diagnostic_answer() builds an answer with the same keys as a real captured choice answer."""
    captured_answer = next(iter(_responses()[0]["answers"].values()))
    built_answer = hide_diagnostic_answer(OPTION_PRIMARY, 0.9)
    assert set(built_answer) == set(captured_answer)


def _captured_batch(payloads: list[dict[str, Any]]) -> Batch:
    """One batch over every captured question, keyed by the capture's own ids."""
    questions = {question_id: q for payload in payloads for question_id, q in payload["questions"].items()}
    sensors = [item for payload in payloads for item in payload["state"]["sensors"]]
    return Batch(
        state={"sensors": sensors},
        questions=questions,
        subjects={question_id: {"registry_id": question_id} for question_id in questions},
    )


def test_captured_hide_diagnostic_answers_classify_with_every_sensor_accounted_for() -> None:
    """classify() places every captured sensor into suggested, primary or unsure, none dropped; prints the real spread."""
    payloads = _payloads()
    responses = _responses()
    batch = _captured_batch(payloads)

    result = classify(
        batch,
        merge(payloads, responses),  # type: ignore[arg-type]
        (OPTION_SUGGESTED, OPTION_PRIMARY),
        payloads,  # type: ignore[arg-type]
        HideDiagnosticRecipe(None).gate,
    )

    suggested = result["items"][OPTION_SUGGESTED]
    primary = result["items"][OPTION_PRIMARY]
    unsure = result["unsure"]
    assert (len(suggested), len(primary), len(unsure)) == (29, 63, 30)
    assert len(suggested) + len(primary) + len(unsure) == ASKED

    confidences = sorted(answer["confidence"] for response in responses for answer in response["answers"].values())
    input_tokens = sum(response["usage"]["input_tokens"] for response in responses)
    assert input_tokens == 58_661
    print(f"real run: suggested={len(suggested)} primary={len(primary)} unsure={len(unsure)}")
    print(f"real confidence spread: min={confidences[0]} median={confidences[len(confidences) // 2]} max={confidences[-1]}")
    print(f"real input_tokens: {input_tokens} over {len(responses)} requests")


def test_the_unsure_captured_answers_are_low_confidence_or_none_of_these() -> None:
    """The 30 unsure answers are 22 below the threshold and 8 confident none-of-these, nothing else."""
    answers = [answer for response in _responses() for answer in response["answers"].values()]
    gate = HideDiagnosticRecipe(None).gate
    unsure = [answer for answer in answers if gate(answer) is None]
    below = [answer for answer in unsure if answer["confidence"] < HIDE_DIAGNOSTIC_CONFIDENCE_THRESHOLD]
    confident_none = [answer for answer in unsure if answer["confidence"] >= HIDE_DIAGNOSTIC_CONFIDENCE_THRESHOLD]
    assert (len(below), len(confident_none)) == (22, 8)
    assert all(answer["choice"] == OPTION_NONE for answer in confident_none)


async def test_the_real_sensor_shapes_round_trip_through_prepare_and_split(hass: HomeAssistant) -> None:
    """Registering sensors shaped like the capture reproduces its items, criteria and request sizes.

    The recipe asks in entity id order, not the capture's order, so items are
    compared as multisets via a canonical JSON form, and each request is
    checked for its own re-indexed wording rather than by position.
    """
    payloads = _payloads()
    items = [item for payload in payloads for item in payload["state"]["sensors"]]

    for index, item in enumerate(items):
        register_unit_sensor(
            hass,
            f"hd{index}",
            unit=item["unit"],
            name=item["name"],
            platform=item["integration"],
            state_class=item["state_class"],
            device_name=item["device_name"],
            manufacturer=item["manufacturer"],
            model=item["model"],
        )

    batch = await HideDiagnosticRecipe(None).async_prepare(hass)
    assert len(batch.state["sensors"]) == len(items)
    sent = split_batch(batch)

    assert [len(request["questions"]) for request in sent] == [len(payload["questions"]) for payload in payloads]
    for request in sent:
        assert len(request["state"]["sensors"]) == len(request["questions"])
        for local, question in enumerate(request["questions"].values()):
            assert question["instructions"] == HIDE_DIAGNOSTIC_INSTRUCTIONS.format(index=local)
            assert question["criteria"] == HIDE_DIAGNOSTIC_CRITERIA

    expected = sorted(json.dumps(item, sort_keys=True) for item in items)
    actual = sorted(json.dumps(item, sort_keys=True) for request in sent for item in request["state"]["sensors"])
    assert actual == expected
