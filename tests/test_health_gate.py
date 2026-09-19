"""Tests for the confidence gate and answer classification. Never guesses."""

from __future__ import annotations

from custom_components.gutcheck.const import CHOICE_CONFIDENCE_THRESHOLD, HEALTH_OPTIONS, OPTION_NONE, OPTION_WORTH_FIXING
from custom_components.gutcheck.recipes.base import Batch
from custom_components.gutcheck.recipes.gate import classify, gate_choice

ALLOWED = HEALTH_OPTIONS


def _answer(choice: object, confidence: object, answer_type: str = "choice") -> dict[str, object]:
    return {"type": answer_type, "choice": choice, "confidence": confidence, "probabilities": {}}


def test_confidence_at_threshold_is_accepted() -> None:
    answer = _answer(OPTION_WORTH_FIXING, 0.5)
    assert gate_choice(answer, ALLOWED, CHOICE_CONFIDENCE_THRESHOLD) == OPTION_WORTH_FIXING


def test_confidence_just_below_threshold_is_unsure() -> None:
    answer = _answer(OPTION_WORTH_FIXING, 0.4999)
    assert gate_choice(answer, ALLOWED, CHOICE_CONFIDENCE_THRESHOLD) is None


def test_none_of_these_is_unsure() -> None:
    answer = _answer(OPTION_NONE, 0.99)
    assert gate_choice(answer, ALLOWED, CHOICE_CONFIDENCE_THRESHOLD) is None


def test_unexpected_choice_is_unsure() -> None:
    answer = _answer("delete_all", 0.99)
    assert gate_choice(answer, ALLOWED, CHOICE_CONFIDENCE_THRESHOLD) is None


def test_missing_answer_is_unsure() -> None:
    assert gate_choice(None, ALLOWED, CHOICE_CONFIDENCE_THRESHOLD) is None


def test_non_dict_answer_is_unsure() -> None:
    assert gate_choice("not-a-dict", ALLOWED, CHOICE_CONFIDENCE_THRESHOLD) is None


def test_noul_type_answer_is_unsure() -> None:
    assert gate_choice({"type": "noul", "noul": 0.9}, ALLOWED, CHOICE_CONFIDENCE_THRESHOLD) is None


def test_string_confidence_is_unsure_not_a_crash() -> None:
    answer = _answer(OPTION_WORTH_FIXING, "high")
    assert gate_choice(answer, ALLOWED, CHOICE_CONFIDENCE_THRESHOLD) is None


def test_bool_confidence_true_is_unsure() -> None:
    answer = _answer(OPTION_WORTH_FIXING, True)
    assert gate_choice(answer, ALLOWED, CHOICE_CONFIDENCE_THRESHOLD) is None


def test_none_confidence_is_unsure() -> None:
    answer = _answer(OPTION_WORTH_FIXING, None)
    assert gate_choice(answer, ALLOWED, CHOICE_CONFIDENCE_THRESHOLD) is None


def test_reversed_answer_order_still_maps_each_answer_to_its_own_entity() -> None:
    subjects = {"e0": {"entity_id": "sensor.a"}, "e1": {"entity_id": "sensor.b"}}
    batch = Batch(state={}, questions={}, subjects=subjects)
    response = {
        "model": "jev-latest",
        "answers": {
            "e1": _answer(OPTION_WORTH_FIXING, 0.9),
            "e0": _answer(OPTION_NONE, 0.9),
        },
        "usage": {"input_tokens": 5, "output_tokens": 0},
    }
    payload = {"state": {}, "model": "jev-latest", "questions": {}}

    result = classify(batch, response, ALLOWED, payload)  # type: ignore[arg-type]

    assert result["counts"][OPTION_WORTH_FIXING] == 1
    assert result["items"][OPTION_WORTH_FIXING][0]["entity_id"] == "sensor.b"
    assert len(result["unsure"]) == 1
    assert result["unsure"][0]["entity_id"] == "sensor.a"


def test_unsure_entity_is_absent_from_every_count_and_from_worth_fixing() -> None:
    subjects = {"e0": {"entity_id": "sensor.a"}}
    batch = Batch(state={}, questions={}, subjects=subjects)
    response = {
        "model": "jev-latest",
        "answers": {"e0": _answer(OPTION_WORTH_FIXING, 0.1)},
        "usage": {"input_tokens": 5, "output_tokens": 0},
    }
    payload = {"state": {}, "model": "jev-latest", "questions": {}}

    result = classify(batch, response, ALLOWED, payload)  # type: ignore[arg-type]

    assert sum(result["counts"].values()) == 0
    assert result["items"][OPTION_WORTH_FIXING] == []
    assert len(result["unsure"]) == 1
