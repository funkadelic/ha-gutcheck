"""Confidence gate and answer classification. Never guesses."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import TYPE_CHECKING

from homeassistant.util import dt as dt_util

from ..models import Question, SystemOneResponse

if TYPE_CHECKING:
    from .shapes import Batch, Item, LastPayload, RecipeResult


def gate_choice(answer: object, allowed: tuple[str, ...], threshold: float) -> str | None:
    """Return the answer's choice only when it is a confident, expected choice."""
    if not isinstance(answer, dict) or answer.get("type") != "choice":
        return None
    choice = answer.get("choice")
    if choice not in allowed:
        return None
    confidence = _raw_confidence(answer)
    if confidence is None:
        return None
    return choice if confidence >= threshold else None


def gate_score(answer: object, allowed: tuple[str, ...], threshold: float) -> str | None:
    """Return the allowed option at the answer's rounded level, only when confident enough.

    Never reads CHOICE_CONFIDENCE_THRESHOLD: a score recipe's threshold is its
    own constant, tuned independently of any choice recipe's.
    """
    if not isinstance(answer, dict) or answer.get("type") != "score":
        return None
    score = _raw_score(answer)
    if score is None:
        return None
    confidence = _raw_confidence(answer)
    if confidence is None or confidence < threshold:
        return None
    # floor(score + 0.5), not round(): a value exactly halfway always rounds up, never to the even level.
    level = math.floor(score + 0.5)
    if not 0 <= level < len(allowed):
        return None
    return allowed[level]


def unit_interval(value: object) -> float | None:
    """value, but only as a real number in [0, 1] the API contract allows.

    Compare before converting: float() on an oversized int raises; the range check also rejects NaN and both infinities.
    """
    if not isinstance(value, bool) and isinstance(value, int | float) and 0.0 <= value <= 1.0:
        return float(value)
    return None


def _raw_confidence(answer: object) -> float | None:
    """The answer's confidence, but only as a real number the API contract allows."""
    if isinstance(answer, dict):
        return unit_interval(answer.get("confidence"))
    return None


def _raw_score(answer: object) -> float | None:
    """The answer's raw score, but only as a real, finite number the API contract allows."""
    if isinstance(answer, dict):
        score = answer.get("score")
        if not isinstance(score, bool) and isinstance(score, int | float):
            try:
                score = float(score)
            except OverflowError:
                return None
            if math.isfinite(score):
                return score
    return None


def _asked_choice(question: Question, answer: object) -> str | None:
    """The answer's choice, but only when the question asked was itself a choice question.

    The str type is checked first: an unhashable choice such as a list would otherwise raise in the dict lookup.
    """
    if question.get("type") != "choice":
        return None
    if not isinstance(answer, dict):
        return None
    choice = answer.get("choice")
    if not isinstance(choice, str):
        return None
    criteria = question.get("criteria")
    if not isinstance(criteria, dict) or choice not in criteria:
        return None
    return choice


def _off_criteria(question: Question | None, answer: object) -> bool:
    """Whether a choice answer names something outside its own question's criteria.

    Wins over the recipe's own gate: a per-question criteria set can be
    narrower than the recipe's overall allow-list.
    """
    if question is None or question.get("type") != "choice" or not isinstance(answer, dict) or answer.get("type") != "choice":
        return False
    choice = answer.get("choice")
    if not isinstance(choice, str):
        return False
    criteria = question.get("criteria")
    return not (isinstance(criteria, dict) and choice in criteria)


def _seed(batch: Batch, allowed: tuple[str, ...]) -> tuple[dict[str, int], dict[str, list[Item]]]:
    """Counts and items, seeded from the batch's carried field.

    The unsure list always starts empty, carried or not: an unsure verdict
    is never carried forward, so there is nothing to seed it with.
    """
    items: dict[str, list[Item]] = {option: list(batch.carried.get(option, [])) for option in allowed}
    counts: dict[str, int] = {option: len(bucket) for option, bucket in items.items()}
    return counts, items


def carry_forward(batch: Batch, allowed: tuple[str, ...], payload: LastPayload | None) -> RecipeResult:
    """Build a RecipeResult from the batch's carried field alone, for a run with nothing to ask.

    payload is the prior result's last_payload: no request was made this
    run, so the sensor keeps showing what was genuinely last sent instead
    of reading as freshly empty.
    """
    counts, items = _seed(batch, allowed)
    return {
        "last_run": dt_util.utcnow().isoformat(),
        "counts": counts,
        "items": items,
        "unsure": [],
        "last_payload": payload,
    }


def _mark_lean(entry: Item, answer: object, lean: Callable[[object], str | None] | None) -> None:
    """Set entry's lean field from the recipe's lean callable, only when offered and it returns one."""
    if lean is None:
        return
    result = lean(answer)
    if result is not None:
        entry["lean"] = result


def classify(
    batch: Batch,
    response: SystemOneResponse,
    allowed: tuple[str, ...],
    payload: LastPayload,
    gate: Callable[[object], str | None],
    *,
    lean: Callable[[object], str | None] | None = None,
) -> RecipeResult:
    """Turn a response into a RecipeResult, gating every answer through the recipe's own gate.

    lean, when the recipe offers one, marks an unsure entry only: it never
    changes which bucket or count an answer lands in.
    """
    counts, items = _seed(batch, allowed)
    unsure: list[Item] = []
    answers = response["answers"]

    for question_id, subject in batch.subjects.items():
        answer = answers.get(question_id)
        confidence = _raw_confidence(answer)
        score = _raw_score(answer)
        entry: Item = {**subject, "confidence": confidence}
        if score is not None:
            entry["score"] = score
        question = batch.questions.get(question_id)
        if question is not None:
            choice = _asked_choice(question, answer)
            if choice is not None:
                entry["choice"] = choice
        chosen = None if _off_criteria(question, answer) else gate(answer)
        if chosen is not None:
            items[chosen].append(entry)
            counts[chosen] += 1
        else:
            _mark_lean(entry, answer, lean)
            unsure.append(entry)

    return {
        "last_run": dt_util.utcnow().isoformat(),
        "counts": counts,
        "items": items,
        "unsure": unsure,
        "last_payload": payload,
    }
