"""Split a recipe run too large for one request into several, and merge their answers."""

from __future__ import annotations

import logging
from bisect import bisect_left

from .budget import request_fits
from .const import MODEL
from .models import Question, SystemOneRequest, SystemOneResponse
from .recipes.shapes import Batch

_LOGGER = logging.getLogger(__name__)


def _request(batch: Batch, ids: list[str], start: int, end: int) -> SystemOneRequest:
    """The request for subjects [start:end], each question re-indexed to its place in this slice."""
    questions: dict[str, Question] = {}
    for local, question_id in enumerate(ids[start:end]):
        question = batch.questions[question_id].copy()
        question["instructions"] = batch.template.format(index=local)
        questions[question_id] = question
    return {"state": {batch.list_key: batch.state[batch.list_key][start:end]}, "model": MODEL, "questions": questions}


def _end(batch: Batch, ids: list[str], start: int) -> int:
    """Where the request starting at start ends: as many subjects as fit, never fewer than one."""
    ends = range(start + 1, len(ids) + 1)
    fitting = bisect_left(ends, True, key=lambda end: not request_fits(_request(batch, ids, start, end)))
    return start + max(1, fitting)


def split_batch(batch: Batch) -> list[SystemOneRequest]:
    """The run as one request when it fits, else as consecutive slices that each fit.

    A lone subject too large for any request still goes out alone, so the
    budget gate's own size check refuses it. So does a batch that never said
    how to split, rather than failing on a missing state key.
    """
    whole: SystemOneRequest = {"state": batch.state, "model": MODEL, "questions": batch.questions}
    if len(batch.questions) < 2 or not (batch.list_key and batch.template) or request_fits(whole):
        return [whole]
    ids = list(batch.questions)
    payloads: list[SystemOneRequest] = []
    start = 0
    while start < len(ids):
        end = _end(batch, ids, start)
        payloads.append(_request(batch, ids, start, end))
        start = end
    _LOGGER.debug("run split subjects=%s requests=%s", len(ids), len(payloads))
    return payloads


def merge(payloads: list[SystemOneRequest], responses: list[SystemOneResponse]) -> SystemOneResponse:
    """One response holding each request's own answers, with usage summed across requests."""
    answers: dict[str, object] = {}
    input_tokens = output_tokens = 0
    for payload, response in zip(payloads, responses, strict=True):
        own = response["answers"]
        answers |= {question_id: own[question_id] for question_id in payload["questions"] if question_id in own}
        input_tokens += response["usage"]["input_tokens"]
        # The client never validates output_tokens, and nothing reads it but this sum.
        output = response["usage"].get("output_tokens")
        output_tokens += output if isinstance(output, int) else 0
    return {
        "model": responses[0].get("model", MODEL),
        "answers": answers,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }
