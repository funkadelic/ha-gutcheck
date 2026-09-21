"""Typed request and response shapes for the Jev systemone API."""

from typing import Any, Literal, TypedDict


class NoulQuestion(TypedDict):
    """A yes/no question. Returns a bare probability, never a confidence."""

    type: Literal["noul"]
    instructions: str


class ChoiceQuestion(TypedDict):
    """A fixed-option question. criteria maps each option to its description."""

    type: Literal["choice"]
    instructions: str
    criteria: dict[str, str | None]


class ScoreQuestion(TypedDict):
    """An ordered-level question. criteria lists each level's description, in order."""

    type: Literal["score"]
    instructions: str
    criteria: list[str]


Question = NoulQuestion | ChoiceQuestion | ScoreQuestion


class SystemOneRequest(TypedDict):
    """Body posted to the systemone endpoint."""

    state: dict[str, Any] | str
    model: str
    questions: dict[str, Question]


class NoulAnswer(TypedDict):
    """A noul answer. Deliberately carries no confidence key."""

    type: Literal["noul"]
    noul: float


class ChoiceAnswer(TypedDict):
    """A choice answer."""

    type: Literal["choice"]
    choice: str
    probabilities: dict[str, float]
    confidence: float


class ScoreAnswer(TypedDict):
    """A score answer. score can land between two levels; legend and probabilities are keyed by level number."""

    type: Literal["score"]
    score: float
    legend: dict[str, str]
    probabilities: dict[str, float]
    confidence: float


class Usage(TypedDict):
    """Token usage reported on every response."""

    input_tokens: int
    output_tokens: int


class SystemOneResponse(TypedDict):
    """Body returned by the systemone endpoint.

    answers is untyped object per key: every answer is untrusted until the
    gate validates its shape.
    """

    model: str
    answers: dict[str, object]
    usage: Usage
