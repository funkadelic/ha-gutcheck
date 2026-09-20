"""Tests for the confidence gate and answer classification. Never guesses."""

from __future__ import annotations

import pytest

from custom_components.gutcheck.const import CHOICE_CONFIDENCE_THRESHOLD, HEALTH_OPTIONS, OPTION_NONE, OPTION_WORTH_FIXING
from custom_components.gutcheck.recipes.base import Batch
from custom_components.gutcheck.recipes.gate import classify, gate_choice

ALLOWED = HEALTH_OPTIONS


def _answer(choice: object, confidence: object, answer_type: str = "choice") -> dict[str, object]:
    """A choice-shaped answer with the given choice and confidence, empty probabilities."""
    return {"type": answer_type, "choice": choice, "confidence": confidence, "probabilities": {}}


def test_confidence_at_threshold_is_accepted() -> None:
    """Confidence exactly at the threshold is accepted, not treated as below it."""
    answer = _answer(OPTION_WORTH_FIXING, 0.5)
    assert gate_choice(answer, ALLOWED, CHOICE_CONFIDENCE_THRESHOLD) == OPTION_WORTH_FIXING


def test_confidence_just_below_threshold_is_unsure() -> None:
    """Confidence one hair below the threshold is unsure, never rounded up."""
    answer = _answer(OPTION_WORTH_FIXING, 0.4999)
    assert gate_choice(answer, ALLOWED, CHOICE_CONFIDENCE_THRESHOLD) is None


def test_none_of_these_is_unsure() -> None:
    """A high-confidence "none of these" still gates to unsure, never to an action."""
    answer = _answer(OPTION_NONE, 0.99)
    assert gate_choice(answer, ALLOWED, CHOICE_CONFIDENCE_THRESHOLD) is None


def test_unexpected_choice_is_unsure() -> None:
    """A choice outside the allowed set gates to unsure, however confident the answer."""
    answer = _answer("delete_all", 0.99)
    assert gate_choice(answer, ALLOWED, CHOICE_CONFIDENCE_THRESHOLD) is None


def test_missing_answer_is_unsure() -> None:
    """No answer at all gates to unsure rather than raising."""
    assert gate_choice(None, ALLOWED, CHOICE_CONFIDENCE_THRESHOLD) is None


def test_non_dict_answer_is_unsure() -> None:
    """A malformed, non-dict answer gates to unsure rather than raising."""
    assert gate_choice("not-a-dict", ALLOWED, CHOICE_CONFIDENCE_THRESHOLD) is None


def test_noul_type_answer_is_unsure() -> None:
    """A noul answer routed through the choice gate is unsure; the two types are not interchangeable."""
    assert gate_choice({"type": "noul", "noul": 0.9}, ALLOWED, CHOICE_CONFIDENCE_THRESHOLD) is None


def test_string_confidence_is_unsure_not_a_crash() -> None:
    """A string confidence value gates to unsure instead of raising a TypeError."""
    answer = _answer(OPTION_WORTH_FIXING, "high")
    assert gate_choice(answer, ALLOWED, CHOICE_CONFIDENCE_THRESHOLD) is None


def test_bool_confidence_true_is_unsure() -> None:
    """A bool confidence is rejected even though bool is a subtype of int in Python."""
    answer = _answer(OPTION_WORTH_FIXING, True)
    assert gate_choice(answer, ALLOWED, CHOICE_CONFIDENCE_THRESHOLD) is None


def test_none_confidence_is_unsure() -> None:
    """A missing confidence value gates to unsure rather than raising."""
    answer = _answer(OPTION_WORTH_FIXING, None)
    assert gate_choice(answer, ALLOWED, CHOICE_CONFIDENCE_THRESHOLD) is None


@pytest.mark.parametrize("confidence", [1.5, -0.1, float("nan"), float("inf"), float("-inf"), 10**400])
def test_confidence_outside_zero_to_one_is_unsure(confidence: object) -> None:
    """A confidence the API contract does not allow must never open the gate."""
    answer = _answer(OPTION_WORTH_FIXING, confidence)
    assert gate_choice(answer, ALLOWED, CHOICE_CONFIDENCE_THRESHOLD) is None


def test_reversed_answer_order_still_maps_each_answer_to_its_own_entity() -> None:
    """classify() matches answers to subjects by key, not by the order the API returned them in."""
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
    """A below-threshold answer counts nowhere and never lands in the worth-fixing bucket."""
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


def test_missing_answer_lands_in_unsure_with_no_confidence() -> None:
    """A subject the API never answered still lands in unsure, with confidence None."""
    subjects = {"e0": {"entity_id": "sensor.a"}}
    batch = Batch(state={}, questions={}, subjects=subjects)
    response = {"model": "jev-latest", "answers": {}, "usage": {"input_tokens": 5, "output_tokens": 0}}
    payload = {"state": {}, "model": "jev-latest", "questions": {}}

    result = classify(batch, response, ALLOWED, payload)  # type: ignore[arg-type]

    assert result["unsure"] == [{"entity_id": "sensor.a", "confidence": None}]


def test_answer_with_malformed_confidence_lands_in_unsure_with_no_confidence() -> None:
    """A malformed confidence value lands the subject in unsure with confidence None, not the raw junk value."""
    subjects = {"e0": {"entity_id": "sensor.a"}}
    batch = Batch(state={}, questions={}, subjects=subjects)
    response = {
        "model": "jev-latest",
        "answers": {"e0": _answer(OPTION_WORTH_FIXING, "high")},
        "usage": {"input_tokens": 5, "output_tokens": 0},
    }
    payload = {"state": {}, "model": "jev-latest", "questions": {}}

    result = classify(batch, response, ALLOWED, payload)  # type: ignore[arg-type]

    assert result["unsure"] == [{"entity_id": "sensor.a", "confidence": None}]
