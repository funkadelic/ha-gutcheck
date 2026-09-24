"""Tests against real API responses captured from the target install."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from custom_components.gutcheck.client import validate_response
from custom_components.gutcheck.const import (
    CHOICE_CONFIDENCE_THRESHOLD,
    HEALTH_OPTIONS,
    OPTION_ROUTINE,
    UPDATE_CRITERIA,
    UPDATE_INSTRUCTIONS,
)
from custom_components.gutcheck.recipes.gate import classify, gate_choice
from custom_components.gutcheck.recipes.shapes import Batch
from custom_components.gutcheck.recipes.updates import UpdateRecipe

from .conftest import choice_answer

FIXTURES = Path(__file__).parent / "fixtures" / "captured"


def _load(name: str) -> dict[str, Any]:
    """The captured fixture JSON at `name`, parsed."""
    return json.loads((FIXTURES / name).read_text())


def test_health_response_passes_validate_response() -> None:
    """A real captured health-recipe response satisfies validate_response's documented shape."""
    response = _load("health_response.json")
    assert validate_response(response) == response


def test_validate_response_fixture_passes_validate_response() -> None:
    """A real captured noul response satisfies validate_response's documented shape."""
    response = _load("validate_response.json")
    assert validate_response(response) == response


def test_the_captured_payload_and_response_come_from_the_same_run() -> None:
    """Regenerating one fixture without the other would make the tests below lie."""
    payload = _load("health_payload.json")
    response = _load("health_response.json")
    assert set(payload["questions"]) == set(response["answers"]), (
        "health_payload.json and health_response.json disagree on question ids; recapture both together"
    )


def test_every_answer_is_a_choice_with_probabilities_matching_its_question() -> None:
    """Every captured answer's probabilities cover exactly its question's criteria, no more, no less."""
    payload = _load("health_payload.json")
    response = _load("health_response.json")
    for question_id, question in payload["questions"].items():
        answer = response["answers"][question_id]
        assert answer["type"] == "choice"
        assert set(answer["probabilities"]) == set(question["criteria"])


def test_captured_answers_classify_with_every_subject_accounted_for() -> None:
    """classify() places every captured subject into either a count bucket or unsure, none dropped."""
    payload = _load("health_payload.json")
    response = _load("health_response.json")
    subjects = {question_id: {"entity_id": question_id} for question_id in payload["questions"]}
    batch = Batch(state=payload["state"], questions=payload["questions"], subjects=subjects)

    def _gate(answer: object) -> str | None:
        """The plain choice gate at the health recipe's own threshold."""
        return gate_choice(answer, HEALTH_OPTIONS, CHOICE_CONFIDENCE_THRESHOLD)

    result = classify(batch, response, HEALTH_OPTIONS, payload, _gate)  # type: ignore[arg-type]

    accepted = sum(result["counts"].values())
    assert accepted + len(result["unsure"]) == len(payload["questions"])
    for option in result["counts"]:
        assert option in HEALTH_OPTIONS


def test_validate_response_answer_is_a_noul_with_no_confidence() -> None:
    """The captured noul answer has no confidence field, matching the noul primitive's documented shape."""
    response = _load("validate_response.json")
    answer = response["answers"]["q"]
    assert answer["type"] == "noul"
    assert isinstance(answer["noul"], float)
    assert "confidence" not in answer


def test_choice_answer_builder_matches_a_captured_answer_key_set() -> None:
    """choice_answer() builds an answer with the same keys as a real captured choice answer."""
    response = _load("health_response.json")
    captured_answer = next(iter(response["answers"].values()))
    built_answer = choice_answer("expected", 0.9)
    assert set(built_answer) == set(captured_answer)


def test_update_response_passes_validate_response() -> None:
    """A real captured update-review response satisfies validate_response's documented shape."""
    response = _load("update_response.json")
    assert validate_response(response) == response


def test_the_captured_update_payload_and_response_come_from_the_same_run() -> None:
    """Regenerating one update fixture without the other would make the tests below lie."""
    payload = _load("update_payload.json")
    response = _load("update_response.json")
    assert set(payload["questions"]) == set(response["answers"]), (
        "update_payload.json and update_response.json disagree on question ids; recapture both together"
    )


def test_the_captured_update_question_matches_the_current_wording() -> None:
    """A change to the update question's wording leaves the captured pair stale until it is recaptured."""
    payload = _load("update_payload.json")
    for index, question in enumerate(payload["questions"].values()):
        assert question["instructions"] == UPDATE_INSTRUCTIONS.format(index=index)
        assert question["criteria"] == UPDATE_CRITERIA


def test_every_captured_update_answer_is_a_score_over_its_criteria_levels() -> None:
    """Each real score answer keys its probabilities by level index, one per criterion."""
    payload = _load("update_payload.json")
    response = _load("update_response.json")
    for question_id, question in payload["questions"].items():
        answer = response["answers"][question_id]
        assert answer["type"] == "score"
        assert set(answer["probabilities"]) == {str(level) for level in range(len(question["criteria"]))}


def test_the_captured_patch_release_gates_to_routine() -> None:
    """The real answer for a bug-fix-only patch release passes the update gate as routine."""
    response = _load("update_response.json")
    assert UpdateRecipe(None).gate(response["answers"]["u0"]) == OPTION_ROUTINE
