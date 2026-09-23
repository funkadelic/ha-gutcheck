"""Tests for the score gate: rounding, range, and confidence, at the update recipe's own threshold."""

from __future__ import annotations

import math

import pytest

from custom_components.gutcheck.const import UPDATE_CONFIDENCE_THRESHOLD, UPDATE_OPTIONS
from custom_components.gutcheck.recipes.gate import gate_score

ALLOWED = UPDATE_OPTIONS


def _answer(score: object, confidence: object, answer_type: str = "score") -> dict[str, object]:
    """A score-shaped answer with the given score and confidence, an empty legend and probabilities."""
    return {"type": answer_type, "score": score, "confidence": confidence, "legend": {}, "probabilities": {}}


def test_clean_mid_level_answer_is_accepted() -> None:
    """A clean score that lands exactly on the middle level is accepted at that level."""
    answer = _answer(1, 0.9)
    assert gate_score(answer, ALLOWED, UPDATE_CONFIDENCE_THRESHOLD) == ALLOWED[1]


def test_score_rounding_up_to_the_top_level_at_the_threshold_is_accepted() -> None:
    """A score that rounds up to the top level, with confidence exactly at the threshold, is accepted."""
    answer = _answer(1.6, UPDATE_CONFIDENCE_THRESHOLD)
    assert gate_score(answer, ALLOWED, UPDATE_CONFIDENCE_THRESHOLD) == ALLOWED[2]


def test_top_level_score_just_below_the_threshold_is_rejected() -> None:
    """A top-level score with confidence one hair below the threshold is unsure, never rounded up."""
    answer = _answer(2, UPDATE_CONFIDENCE_THRESHOLD - 0.0001)
    assert gate_score(answer, ALLOWED, UPDATE_CONFIDENCE_THRESHOLD) is None


def test_level_above_the_last_one_is_rejected() -> None:
    """A score rounding above the last allowed level gates to unsure rather than raising."""
    answer = _answer(3, 0.9)
    assert gate_score(answer, ALLOWED, UPDATE_CONFIDENCE_THRESHOLD) is None


def test_negative_level_is_rejected() -> None:
    """A negative score gates to unsure rather than wrapping to a valid level."""
    answer = _answer(-1, 0.9)
    assert gate_score(answer, ALLOWED, UPDATE_CONFIDENCE_THRESHOLD) is None


def test_non_numeric_score_is_rejected() -> None:
    """A string score gates to unsure instead of raising a TypeError."""
    answer = _answer("high", 0.9)
    assert gate_score(answer, ALLOWED, UPDATE_CONFIDENCE_THRESHOLD) is None


def test_bool_score_is_rejected() -> None:
    """A bool score is rejected even though bool is a subtype of int in Python."""
    answer = _answer(True, 0.9)
    assert gate_score(answer, ALLOWED, UPDATE_CONFIDENCE_THRESHOLD) is None


def test_infinite_score_is_rejected() -> None:
    """An infinite score gates to unsure rather than raising."""
    answer = _answer(float("inf"), 0.9)
    assert gate_score(answer, ALLOWED, UPDATE_CONFIDENCE_THRESHOLD) is None


def test_nan_score_is_rejected() -> None:
    """A NaN score gates to unsure rather than raising."""
    answer = _answer(float("nan"), 0.9)
    assert gate_score(answer, ALLOWED, UPDATE_CONFIDENCE_THRESHOLD) is None


def test_oversized_int_score_is_rejected() -> None:
    """A score too large to convert to a float gates to unsure rather than raising an OverflowError."""
    answer = _answer(10**400, 0.9)
    assert gate_score(answer, ALLOWED, UPDATE_CONFIDENCE_THRESHOLD) is None


def test_choice_answer_is_rejected() -> None:
    """A choice answer routed through the score gate is unsure; the two types are not interchangeable."""
    answer = {"type": "choice", "choice": ALLOWED[0], "confidence": 0.9, "probabilities": {}}
    assert gate_score(answer, ALLOWED, UPDATE_CONFIDENCE_THRESHOLD) is None


def test_non_dict_answer_is_rejected() -> None:
    """A malformed, non-dict answer gates to unsure rather than raising."""
    assert gate_score("not-a-dict", ALLOWED, UPDATE_CONFIDENCE_THRESHOLD) is None


@pytest.mark.parametrize("confidence", [1.5, -0.1, float("nan"), float("inf"), float("-inf"), 10**400])
def test_confidence_outside_zero_to_one_is_rejected(confidence: object) -> None:
    """A confidence the API contract does not allow must never open the gate."""
    answer = _answer(1, confidence)
    assert gate_score(answer, ALLOWED, UPDATE_CONFIDENCE_THRESHOLD) is None


def test_a_score_of_one_half_on_three_levels_resolves_to_the_middle_level() -> None:
    """floor(score + 0.5), not round(): a score half-way between two levels rounds up, where round(0.5) gives 0."""
    answer = _answer(0.5, 0.9)
    assert round(0.5) == 0
    assert math.floor(0.5 + 0.5) == 1
    assert gate_score(answer, ALLOWED, UPDATE_CONFIDENCE_THRESHOLD) == ALLOWED[1]
