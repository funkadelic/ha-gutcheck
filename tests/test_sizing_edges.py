"""Edges of the request size check: a payload with no questions, and the state cap itself."""

from __future__ import annotations

from custom_components.gutcheck.const import MODEL, STATE_TOKEN_LIMIT
from custom_components.gutcheck.models import SystemOneRequest
from custom_components.gutcheck.sizing import estimate, request_fits, sizes

QUESTION = {"type": "noul", "instructions": "?"}


def _request(state_chars: int) -> SystemOneRequest:
    """A one-question request whose state is a string of state_chars characters."""
    return {"state": "x" * state_chars, "model": MODEL, "questions": {"q": QUESTION}}


def test_a_request_without_questions_adds_nothing_for_a_longest_question() -> None:
    """The state-plus-longest figure is just the state's estimate when there is no question."""
    payload: SystemOneRequest = {"state": "abc", "model": MODEL, "questions": {}}
    assert sizes(payload)[1] == estimate("abc")


def test_state_plus_longest_question_exactly_at_the_cap_fits() -> None:
    """Equal to STATE_TOKEN_LIMIT is allowed; one more state token is not."""
    # Four characters per token, and json.dumps adds two quote characters.
    at_cap = _request(4 * (STATE_TOKEN_LIMIT - estimate(QUESTION)) - 2)
    assert sizes(at_cap)[1] == STATE_TOKEN_LIMIT
    assert request_fits(at_cap)
    assert not request_fits(_request(4 * (STATE_TOKEN_LIMIT - estimate(QUESTION)) - 1))
