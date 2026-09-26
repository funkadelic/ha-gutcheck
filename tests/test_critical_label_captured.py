"""Tests against a real critical label suggestions response captured from the target install."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr

from custom_components.gutcheck.client import validate_response
from custom_components.gutcheck.const import OPTION_SUGGESTED
from custom_components.gutcheck.recipes.critical_label import CriticalLabelRecipe
from custom_components.gutcheck.recipes.critical_label_const import (
    CRITICAL_LABEL_CRITERIA,
    CRITICAL_LABEL_INSTRUCTIONS,
    OPTION_NOT_CRITICAL,
)
from custom_components.gutcheck.recipes.gate import classify
from custom_components.gutcheck.recipes.shapes import Batch

from .conftest import critical_label_answer, register_unit_sensor

FIXTURES = Path(__file__).parent / "fixtures" / "captured"


def _load(name: str) -> dict[str, Any]:
    """The captured fixture JSON at `name`, parsed."""
    return json.loads((FIXTURES / name).read_text())


def test_critical_label_response_passes_validate_response() -> None:
    """A real captured critical label response satisfies validate_response's documented shape."""
    response = _load("critical_label_response.json")
    assert validate_response(response) == response


def test_the_captured_critical_label_payload_and_response_come_from_the_same_run() -> None:
    """Regenerating one fixture without the other would make the tests below lie."""
    payload = _load("critical_label_payload.json")
    response = _load("critical_label_response.json")
    assert set(payload["questions"]) == set(response["answers"]), (
        "critical_label_payload.json and critical_label_response.json disagree on question ids; recapture both together"
    )


def test_every_critical_label_answer_is_a_choice_with_probabilities_matching_its_question() -> None:
    """Every captured answer's probabilities cover exactly its own question's criteria, no more, no less."""
    payload = _load("critical_label_payload.json")
    response = _load("critical_label_response.json")
    for question_id, question in payload["questions"].items():
        answer = response["answers"][question_id]
        assert answer["type"] == "choice"
        assert set(answer["probabilities"]) == set(question["criteria"])


def test_the_captured_critical_label_questions_match_the_current_wording() -> None:
    """A change to the instructions or criteria leaves the captured pair stale until both are recaptured."""
    payload = _load("critical_label_payload.json")
    for index, question in enumerate(payload["questions"].values()):
        assert question["instructions"] == CRITICAL_LABEL_INSTRUCTIONS.format(index=index)
        assert question["criteria"] == CRITICAL_LABEL_CRITERIA


def test_critical_label_answer_builder_matches_a_captured_answer_key_set() -> None:
    """critical_label_answer() builds an answer with the same keys as a real captured choice answer; reused, not duplicated."""
    response = _load("critical_label_response.json")
    captured_answer = next(iter(response["answers"].values()))
    built_answer = critical_label_answer(OPTION_NOT_CRITICAL, 0.9)
    assert set(built_answer) == set(captured_answer)


def test_captured_critical_label_answers_classify_with_every_entity_accounted_for() -> None:
    """classify() places every captured entity into suggested, not_critical or unsure, none dropped; prints the real spread."""
    payload = _load("critical_label_payload.json")
    response = _load("critical_label_response.json")
    subjects = {question_id: {"registry_id": question_id} for question_id in payload["questions"]}
    batch = Batch(state=payload["state"], questions=payload["questions"], subjects=subjects)

    result = classify(batch, response, (OPTION_SUGGESTED, OPTION_NOT_CRITICAL), payload, CriticalLabelRecipe(None).gate)

    suggested = result["items"][OPTION_SUGGESTED]
    not_critical = result["items"][OPTION_NOT_CRITICAL]
    unsure = result["unsure"]
    assert len(suggested) + len(not_critical) + len(unsure) == len(payload["questions"])

    confidences = sorted(response["answers"][qid]["confidence"] for qid in payload["questions"])
    print(f"real run: suggested={len(suggested)} not_critical={len(not_critical)} unsure={len(unsure)}")
    print(f"real confidence spread: min={confidences[0]} median={confidences[len(confidences) // 2]} max={confidences[-1]}")
    print(f"real input_tokens: {response['usage']['input_tokens']}")


async def test_the_real_entity_shapes_round_trip_through_async_prepare(hass: HomeAssistant) -> None:
    """Registering entities shaped like the capture reproduces its entity items and criteria.

    Order differs from the capture (the recipe asks in entity registry id
    order, not the capture's original order), so both entities and criteria
    are compared as multisets via a canonical JSON form, not by position.
    """
    payload = _load("critical_label_payload.json")
    entities = payload["state"]["entities"]
    lr.async_get(hass).async_create("Critical")

    for index, item in enumerate(entities):
        entity_category = er.EntityCategory(item["entity_category"]) if item["entity_category"] else None
        register_unit_sensor(
            hass,
            f"cl{index}",
            domain=item["domain"],
            unit=None,
            original_device_class=item["device_class"],
            name=item["name"],
            platform=item["integration"],
            entity_category=entity_category,
            device_name=item["device_name"],
            manufacturer=item["manufacturer"],
            model=item["model"],
        )

    batch = await CriticalLabelRecipe("critical").async_prepare(hass)

    assert len(batch.state["entities"]) == len(entities)
    expected = sorted(json.dumps(item, sort_keys=True) for item in entities)
    actual = sorted(json.dumps(item, sort_keys=True) for item in batch.state["entities"])
    assert actual == expected

    expected_criteria = sorted(json.dumps(question["criteria"], sort_keys=True) for question in payload["questions"].values())
    actual_criteria = sorted(json.dumps(question["criteria"], sort_keys=True) for question in batch.questions.values())
    assert actual_criteria == expected_criteria
