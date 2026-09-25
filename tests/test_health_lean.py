"""Tests for the health recipe's coarser lean hint on unsure entries."""

from __future__ import annotations

import pytest

from custom_components.gutcheck.const import (
    LEAN_NEEDS_ATTENTION,
    OPTION_EXPECTED,
    OPTION_NONE,
    OPTION_SAFE_TO_REMOVE,
    OPTION_WORTH_FIXING,
)
from custom_components.gutcheck.recipes.gate import classify
from custom_components.gutcheck.recipes.health import HealthRecipe
from custom_components.gutcheck.recipes.shapes import Batch, _parse_stored_result

RECIPE = HealthRecipe(None)


def _answer(probabilities: object, answer_type: str = "choice") -> dict[str, object]:
    """A choice-shaped, otherwise-unsure answer carrying the given probabilities object."""
    return {"type": answer_type, "choice": OPTION_WORTH_FIXING, "confidence": 0.4, "probabilities": probabilities}


def test_needs_attention_side_wins_on_summed_probability() -> None:
    """worth_fixing plus safe_to_remove clearing the threshold leans needs_attention."""
    answer = _answer({OPTION_WORTH_FIXING: 0.5, OPTION_SAFE_TO_REMOVE: 0.3})
    assert RECIPE.lean(answer) == LEAN_NEEDS_ATTENTION


def test_expected_side_wins_on_its_own_probability() -> None:
    """expected alone clearing the threshold leans expected."""
    answer = _answer({OPTION_EXPECTED: 0.85})
    assert RECIPE.lean(answer) == OPTION_EXPECTED


def test_needs_attention_boundary_at_threshold_with_the_other_option_missing() -> None:
    """Exactly at the threshold, with safe_to_remove missing, still leans needs_attention."""
    answer = _answer({OPTION_WORTH_FIXING: 0.7})
    assert RECIPE.lean(answer) == LEAN_NEEDS_ATTENTION


def test_expected_at_exactly_the_threshold_leans_expected() -> None:
    """expected exactly at the threshold is accepted, not treated as below it."""
    answer = _answer({OPTION_EXPECTED: 0.7})
    assert RECIPE.lean(answer) == OPTION_EXPECTED


def test_expected_one_hair_below_threshold_has_no_lean() -> None:
    """expected one hair below the threshold, with everything else low, leans neither way."""
    answer = _answer({OPTION_EXPECTED: 0.6999, OPTION_WORTH_FIXING: 0.1, OPTION_SAFE_TO_REMOVE: 0.1})
    assert RECIPE.lean(answer) is None


def test_neither_side_clears_has_no_lean() -> None:
    """A genuinely unsure spread with neither side at the threshold leans neither way."""
    answer = _answer({OPTION_EXPECTED: 0.5, OPTION_WORTH_FIXING: 0.3, OPTION_SAFE_TO_REMOVE: 0.1, OPTION_NONE: 0.1})
    assert RECIPE.lean(answer) is None


def test_none_of_these_mass_is_never_read() -> None:
    """A high none_of_these probability, with neither real side clearing, leans neither way."""
    answer = _answer({OPTION_NONE: 0.7, OPTION_WORTH_FIXING: 0.2, OPTION_SAFE_TO_REMOVE: 0.1})
    assert RECIPE.lean(answer) is None


def test_junk_none_of_these_does_not_reject_an_otherwise_clearing_answer() -> None:
    """A malformed none_of_these value never rejects an otherwise valid, clearing answer."""
    answer = _answer({OPTION_NONE: "junk", OPTION_EXPECTED: 0.8})
    assert RECIPE.lean(answer) == OPTION_EXPECTED


def test_missing_options_count_as_zero() -> None:
    """A probabilities dict naming only the clearing option still leans that way."""
    answer = _answer({OPTION_EXPECTED: 0.75})
    assert RECIPE.lean(answer) == OPTION_EXPECTED


def test_both_sides_clearing_has_no_lean() -> None:
    """Both sides clearing at once, a malformed spread summing past 1.0, leans neither way."""
    answer = _answer({OPTION_EXPECTED: 0.8, OPTION_WORTH_FIXING: 0.8})
    assert RECIPE.lean(answer) is None


@pytest.mark.parametrize("probabilities", [[0.8], None, "0.8"])
def test_malformed_probabilities_container_has_no_lean(probabilities: object) -> None:
    """A probabilities value that is not itself a dict never leans."""
    assert RECIPE.lean(_answer(probabilities)) is None


def test_absent_probabilities_key_has_no_lean() -> None:
    """An answer with no probabilities key at all never leans."""
    answer = {"type": "choice", "choice": OPTION_WORTH_FIXING, "confidence": 0.4}
    assert RECIPE.lean(answer) is None


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), float("-inf"), 1.5, -0.1, "0.8", 10**400])
def test_malformed_used_probability_value_has_no_lean(value: object) -> None:
    """A used option's probability outside the API's documented range rejects that side entirely."""
    assert RECIPE.lean(_answer({OPTION_EXPECTED: value})) is None


def test_spread_summing_past_one_has_no_lean() -> None:
    """Probabilities that cannot all be true together never lean, even when one side clears."""
    assert RECIPE.lean(_answer({OPTION_EXPECTED: 0.8, OPTION_WORTH_FIXING: 0.3})) is None


def test_spread_rounded_just_past_one_still_leans() -> None:
    """Two-decimal rounding can push a valid spread slightly over 1; it still leans."""
    answer = _answer({OPTION_EXPECTED: 0.28, OPTION_WORTH_FIXING: 0.43, OPTION_SAFE_TO_REMOVE: 0.30})
    assert RECIPE.lean(answer) == LEAN_NEEDS_ATTENTION


def test_none_answer_has_no_lean() -> None:
    """No answer at all never leans."""
    assert RECIPE.lean(None) is None


def test_string_answer_has_no_lean() -> None:
    """A non-dict answer never leans."""
    assert RECIPE.lean("not-a-dict") is None


def test_score_type_answer_has_no_lean() -> None:
    """A score-type answer never leans, even with otherwise-clearing probabilities: lean is choice-only."""
    answer = _answer({OPTION_EXPECTED: 0.9}, answer_type="score")
    assert RECIPE.lean(answer) is None


def test_noul_type_answer_has_no_lean() -> None:
    """A noul-type answer never leans, even with otherwise-clearing probabilities: lean is choice-only."""
    answer = _answer({OPTION_EXPECTED: 0.9}, answer_type="noul")
    assert RECIPE.lean(answer) is None


def test_classify_marks_lean_only_on_a_clearly_leaning_unsure_entry() -> None:
    """classify() with lean=... marks a clearly leaning unsure entry, and nothing else."""
    subjects = {
        "e0": {"entity_id": "sensor.leans"},
        "e1": {"entity_id": "sensor.flat"},
        "e2": {"entity_id": "sensor.confident"},
    }
    batch = Batch(state={}, questions={}, subjects=subjects)
    response = {
        "model": "jev-latest",
        "answers": {
            "e0": _answer({OPTION_WORTH_FIXING: 0.5, OPTION_SAFE_TO_REMOVE: 0.3}),
            "e1": _answer({OPTION_EXPECTED: 0.5, OPTION_WORTH_FIXING: 0.3}),
            "e2": {
                "type": "choice",
                "choice": OPTION_WORTH_FIXING,
                "confidence": 0.9,
                "probabilities": {OPTION_WORTH_FIXING: 0.9},
            },
        },
        "usage": {"input_tokens": 5, "output_tokens": 0},
    }
    payload = {"state": {}, "model": "jev-latest", "questions": {}}

    result = classify(batch, response, RECIPE.options, payload, RECIPE.gate, lean=RECIPE.lean)  # type: ignore[arg-type]

    unsure_by_id = {item["entity_id"]: item for item in result["unsure"]}
    assert unsure_by_id["sensor.leans"]["lean"] == LEAN_NEEDS_ATTENTION
    assert "lean" not in unsure_by_id["sensor.flat"]
    assert "lean" not in result["items"][OPTION_WORTH_FIXING][0]


def test_classify_without_the_lean_keyword_never_sets_lean() -> None:
    """classify() called without the lean keyword never sets the field, even on a leaning entry."""
    subjects = {"e0": {"entity_id": "sensor.leans"}}
    batch = Batch(state={}, questions={}, subjects=subjects)
    response = {
        "model": "jev-latest",
        "answers": {"e0": _answer({OPTION_WORTH_FIXING: 0.5, OPTION_SAFE_TO_REMOVE: 0.3})},
        "usage": {"input_tokens": 5, "output_tokens": 0},
    }
    payload = {"state": {}, "model": "jev-latest", "questions": {}}

    result = classify(batch, response, RECIPE.options, payload, RECIPE.gate)  # type: ignore[arg-type]

    assert "lean" not in result["unsure"][0]


def test_parse_stored_result_tolerates_an_unsure_item_carrying_lean() -> None:
    """_parse_stored_result accepts a stored unsure item that carries the extra lean field."""
    stored = {
        "last_run": "2026-09-25T00:00:00+00:00",
        "counts": dict.fromkeys(RECIPE.options, 0),
        "items": {option: [] for option in RECIPE.options},
        "unsure": [
            {
                "entity_id": "sensor.leans",
                "registry_id": "reg1",
                "unavailable_for": "1 to 6 days",
                "confidence": 0.4,
                "lean": LEAN_NEEDS_ATTENTION,
            }
        ],
        "last_payload": None,
    }

    parsed = _parse_stored_result(stored, RECIPE.stored_item_keys)

    assert parsed is not None
    result, _last_run = parsed
    assert result["unsure"][0]["lean"] == LEAN_NEEDS_ATTENTION
