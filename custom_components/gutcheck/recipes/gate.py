"""Confidence gate and answer classification. Never guesses."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.util import dt as dt_util

from ..const import CHOICE_CONFIDENCE_THRESHOLD
from ..models import SystemOneRequest, SystemOneResponse

if TYPE_CHECKING:
    from .base import Batch, Item, RecipeResult


def gate_choice(answer: object, allowed: tuple[str, ...], threshold: float) -> str | None:
    """Return the answer's choice only when it is a confident, expected choice."""
    if not isinstance(answer, dict) or answer.get("type") != "choice":
        return None
    choice = answer.get("choice")
    if choice not in allowed:
        return None
    confidence = answer.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, int | float):
        return None
    return choice if confidence >= threshold else None


def _raw_confidence(answer: object) -> float | None:
    if isinstance(answer, dict):
        confidence = answer.get("confidence")
        if not isinstance(confidence, bool) and isinstance(confidence, int | float):
            return float(confidence)
    return None


def classify(
    batch: Batch,
    response: SystemOneResponse,
    allowed: tuple[str, ...],
    payload: SystemOneRequest,
) -> RecipeResult:
    """Turn a response into a RecipeResult, gating every answer on the way."""
    counts: dict[str, int] = dict.fromkeys(allowed, 0)
    items: dict[str, list[Item]] = {option: [] for option in allowed}
    unsure: list[Item] = []
    answers = response["answers"]

    for question_id, subject in batch.subjects.items():
        answer = answers.get(question_id)
        confidence = _raw_confidence(answer)
        choice = gate_choice(answer, allowed, CHOICE_CONFIDENCE_THRESHOLD)
        entry: Item = {**subject, "confidence": confidence}
        if choice is not None:
            items[choice].append(entry)
            counts[choice] += 1
        else:
            unsure.append(entry)

    return {
        "last_run": dt_util.utcnow().isoformat(),
        "counts": counts,
        "items": items,
        "unsure": unsure,
        "last_payload": payload,
    }
