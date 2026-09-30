"""Token estimates for a request: what it reserves against the budget and whether it fits one call."""

from __future__ import annotations

import json
import math
from typing import Any

from .const import BUDGET_CHARS_PER_TOKEN, CHARS_PER_TOKEN, REQUEST_TOKEN_LIMIT, STATE_TOKEN_LIMIT
from .models import SystemOneRequest


def estimate(value: Any) -> int:
    """Estimate a JSON-serializable value's token cost from its character count."""
    return math.ceil(len(json.dumps(value)) / CHARS_PER_TOKEN)


def estimate_tokens(payload: SystemOneRequest) -> int:
    """Estimate a whole request's token cost from its serialized character count."""
    return estimate(payload)


def reservation(payload: SystemOneRequest) -> int:
    """What a request holds against the daily budget until the API reports its real usage."""
    return math.ceil(len(json.dumps(payload)) / BUDGET_CHARS_PER_TOKEN)


def sizes(payload: SystemOneRequest) -> tuple[int, int]:
    """The whole request's estimate, and its state's estimate plus its longest question's."""
    longest_question = max((estimate(question) for question in payload["questions"].values()), default=0)
    return estimate_tokens(payload), estimate(payload["state"]) + longest_question


def request_fits(payload: SystemOneRequest) -> bool:
    """Whether one request is within both the per-request and the per-state token cap."""
    estimate, state_plus_longest = sizes(payload)
    return estimate <= REQUEST_TOKEN_LIMIT and state_plus_longest <= STATE_TOKEN_LIMIT
