"""One stuck-integration-check run at a generous size fits comfortably inside both token limits."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.budget import _estimate, _reservation, estimate_tokens
from custom_components.gutcheck.const import (
    CONFIG_ENTRY_OPTIONS,
    CONFIG_ENTRY_REASON_MAX_CHARS,
    OPTION_DEAD,
    REQUEST_TOKEN_LIMIT,
    STATE_TOKEN_LIMIT,
)

from .conftest import api_response, area_answer, posted_bodies, register_jev_responses

# The budget gate's own estimator undercounts a real payload; every
# comparison here applies this factor before checking a limit, matching
# tests/test_area_size.py.
SAFETY_FACTOR = 1.15

# A generous count for a bad day; the target install has none stuck today.
STUCK_ENTRY_COUNT = 20

_LONG_REASON = "The remote service could not be reached and the connection attempt timed out repeatedly. " * 5


async def test_twenty_full_length_stuck_entries_fit_one_request_with_measured_headroom(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, triage_entry: MockConfigEntry, failing_entry: Any
) -> None:
    """20 stuck entries, each reason at the max length, go out as one request inside both token limits."""
    for index in range(STUCK_ENTRY_COUNT):
        await failing_entry(
            f"very_long_realistic_integration_domain_name_{index:02d}",
            ConfigEntryError(_LONG_REASON),
            title=f"Integration {index}",
            entry_id=f"stuck_entry_{index:02d}",
        )

    answers = {f"c{index}": area_answer(OPTION_DEAD, 0.9, CONFIG_ENTRY_OPTIONS) for index in range(STUCK_ENTRY_COUNT)}
    register_jev_responses(aioclient_mock, [api_response(answers)])
    triage_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(triage_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    bodies = posted_bodies(aioclient_mock)
    assert len(bodies) == 1
    body = bodies[0]
    assert len(body["questions"]) == STUCK_ENTRY_COUNT
    assert len(body["state"]["entries"]) == STUCK_ENTRY_COUNT
    for entry_state in body["state"]["entries"]:
        assert entry_state["reason"] is not None
        assert len(entry_state["reason"]) == CONFIG_ENTRY_REASON_MAX_CHARS

    factored_request = estimate_tokens(body) * SAFETY_FACTOR
    assert factored_request < REQUEST_TOKEN_LIMIT

    state_estimate = _estimate(body["state"])
    longest_question = max(_estimate(question) for question in body["questions"].values())
    factored_state = (state_estimate + longest_question) * SAFETY_FACTOR
    assert factored_state < STATE_TOKEN_LIMIT

    per_entry_factored = factored_request / STUCK_ENTRY_COUNT
    print(
        f"realistic stuck integration check estimate, factored: {per_entry_factored:.0f} tokens per entry; "
        f"run reservation {_reservation(body)}"
    )
