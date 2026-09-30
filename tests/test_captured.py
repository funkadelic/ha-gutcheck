"""Tests against real API responses captured from the target install."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from custom_components.gutcheck.client import validate_response
from custom_components.gutcheck.const import (
    OPTION_ROUTINE,
    RELEASE_NOTES_MAX_CHARS,
)
from custom_components.gutcheck.recipes.update_const import UPDATE_CRITERIA, UPDATE_INSTRUCTIONS
from custom_components.gutcheck.recipes.updates import UpdateRecipe

FIXTURES = Path(__file__).parent / "fixtures" / "captured"

# The sentence HACS appends to every integration update's release notes.
HACS_RESTART_FOOTER = "You need to restart Home Assistant manually after updating."


def _load(name: str) -> dict[str, Any]:
    """The captured fixture JSON at `name`, parsed."""
    return json.loads((FIXTURES / name).read_text())


def _update(payload: dict[str, Any], question_id: str) -> dict[str, Any]:
    """The raw update state a captured question id was asked about."""
    return payload["state"]["updates"][int(question_id.removeprefix("u"))]


def test_validate_response_fixture_passes_validate_response() -> None:
    """A real captured noul response satisfies validate_response's documented shape."""
    response = _load("validate_response.json")
    assert validate_response(response) == response


def test_validate_response_answer_is_a_noul_with_no_confidence() -> None:
    """The captured noul answer has no confidence field, matching the noul primitive's documented shape."""
    response = _load("validate_response.json")
    answer = response["answers"]["q"]
    assert answer["type"] == "noul"
    assert isinstance(answer["noul"], float)
    assert "confidence" not in answer


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
    for question_id, question in payload["questions"].items():
        index = int(question_id.removeprefix("u"))
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


@pytest.mark.parametrize("question_id", ["u0", "u3"])
def test_a_restart_footer_in_the_release_notes_still_gates_to_routine(question_id: str) -> None:
    """HACS release notes ending in the manual-restart line still gate to routine."""
    payload = _load("update_payload.json")
    response = _load("update_response.json")
    assert HACS_RESTART_FOOTER in _update(payload, question_id)["release_notes"]
    assert UpdateRecipe(None).gate(response["answers"][question_id]) == OPTION_ROUTINE


def test_an_update_with_no_release_notes_gates_to_routine() -> None:
    """A core update whose release notes and summary are both empty still gates to routine."""
    payload = _load("update_payload.json")
    response = _load("update_response.json")
    update = _update(payload, "u4")
    assert update["release_notes"] == ""
    assert update["release_summary"] == ""
    assert UpdateRecipe(None).gate(response["answers"]["u4"]) == OPTION_ROUTINE


def test_low_confidence_long_release_notes_stay_unsure() -> None:
    """Release notes cut off at the excerpt cap stay unsure."""
    payload = _load("update_payload.json")
    response = _load("update_response.json")
    assert len(_update(payload, "u1")["release_notes"]) == RELEASE_NOTES_MAX_CHARS
    assert UpdateRecipe(None).gate(response["answers"]["u1"]) is None
