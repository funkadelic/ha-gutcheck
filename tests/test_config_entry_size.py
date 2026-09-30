"""One stuck-integration-check run at a generous size goes out as capped requests, each comfortably inside both token limits."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.recipes.config_entry_const import CONFIG_ENTRY_OPTIONS, CONFIG_ENTRY_REASON_MAX_CHARS, OPTION_DEAD

from .conftest import area_answer, assert_capped_run_fits, posted_bodies, register_jev_answers

# A generous count for a bad day; the target install has none stuck today.
STUCK_ENTRY_COUNT = 20

_LONG_REASON = "The remote service could not be reached and the connection attempt timed out repeatedly. " * 5


async def test_twenty_full_length_stuck_entries_go_out_as_capped_requests_with_measured_headroom(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, triage_entry: MockConfigEntry, failing_entry: Any
) -> None:
    """20 stuck entries, each reason at the max length, go out ten to a request, each inside both token limits."""
    for index in range(STUCK_ENTRY_COUNT):
        await failing_entry(
            f"very_long_realistic_integration_domain_name_{index:02d}",
            ConfigEntryError(_LONG_REASON),
            title=f"Integration {index}",
            entry_id=f"stuck_entry_{index:02d}",
        )

    answers = {f"c{index}": area_answer(OPTION_DEAD, 0.9, CONFIG_ENTRY_OPTIONS) for index in range(STUCK_ENTRY_COUNT)}
    register_jev_answers(aioclient_mock, answers)
    triage_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(triage_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    bodies = posted_bodies(aioclient_mock)
    for entry_state in (item for body in bodies for item in body["state"]["entries"]):
        assert entry_state["reason"] is not None
        assert len(entry_state["reason"]) == CONFIG_ENTRY_REASON_MAX_CHARS

    total = assert_capped_run_fits(bodies, "entries", STUCK_ENTRY_COUNT)
    print(f"realistic stuck integration check estimate, factored: {total / STUCK_ENTRY_COUNT:.0f} tokens per entry")
