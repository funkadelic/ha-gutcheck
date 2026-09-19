"""Tests against real API responses captured from the target install."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from custom_components.gutcheck.client import validate_response
from custom_components.gutcheck.const import HEALTH_OPTIONS
from custom_components.gutcheck.recipes.base import Batch
from custom_components.gutcheck.recipes.gate import classify

from .conftest import choice_answer

FIXTURES = Path(__file__).parent / "fixtures" / "captured"


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


def test_health_response_passes_validate_response() -> None:
    response = _load("health_response.json")
    assert validate_response(response) == response


def test_validate_response_fixture_passes_validate_response() -> None:
    response = _load("validate_response.json")
    assert validate_response(response) == response


def test_every_answer_is_a_choice_with_probabilities_matching_its_question() -> None:
    payload = _load("health_payload.json")
    response = _load("health_response.json")
    for question_id, question in payload["questions"].items():
        answer = response["answers"][question_id]
        assert answer["type"] == "choice"
        assert set(answer["probabilities"]) == set(question["criteria"])


def test_captured_answers_classify_with_every_subject_accounted_for() -> None:
    payload = _load("health_payload.json")
    response = _load("health_response.json")
    subjects = {question_id: {"entity_id": question_id} for question_id in payload["questions"]}
    batch = Batch(state=payload["state"], questions=payload["questions"], subjects=subjects)

    result = classify(batch, response, HEALTH_OPTIONS, payload)  # type: ignore[arg-type]

    accepted = sum(result["counts"].values())
    assert accepted + len(result["unsure"]) == len(payload["questions"])
    for option in result["counts"]:
        assert option in HEALTH_OPTIONS


def test_validate_response_answer_is_a_noul_with_no_confidence() -> None:
    response = _load("validate_response.json")
    answer = response["answers"]["q"]
    assert answer["type"] == "noul"
    assert isinstance(answer["noul"], float)
    assert "confidence" not in answer


def test_choice_answer_builder_matches_a_captured_answer_key_set() -> None:
    response = _load("health_response.json")
    captured_answer = next(iter(response["answers"].values()))
    built_answer = choice_answer("expected", 0.9)
    assert set(built_answer) == set(captured_answer)
