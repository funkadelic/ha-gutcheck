"""One area run fits one request with measured headroom, and all three recipes fit the daily budget."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.budget import _estimate, estimate_tokens
from custom_components.gutcheck.const import (
    DEFAULT_DAILY_BUDGET,
    DEVICE_TEXT_MAX_CHARS,
    DOMAIN,
    MAX_UPDATES_PER_RUN,
    MODEL,
    RECIPE_AREAS,
    RELEASE_NOTES_MAX_CHARS,
    REQUEST_TOKEN_LIMIT,
    STATE_TOKEN_LIMIT,
    UPDATE_CRITERIA,
    UPDATE_INSTRUCTIONS,
    VERSION_JUMP_MAJOR,
)
from custom_components.gutcheck.recipes.areas import AreaRecipe

from .conftest import (
    AreaEntitySpec,
    api_response,
    area_answer,
    create_areas,
    load_fixture,
    posted_bodies,
    register_area_device,
    register_jev_responses,
)

# The budget gate's own estimator undercounts a real payload by about 12%,
# so every comparison here applies this factor before checking a limit.
SAFETY_FACTOR = 1.15

# The target install's own counts as of 2026-09-23 (see the phase context):
# 18 areas, about 60 real candidate devices.
REALISTIC_AREA_COUNT = 60
_AREA_NAMES = (
    "Kitchen",
    "Living Room",
    "Garage",
    "Primary Bedroom",
    "Bathroom",
    "Office",
    "Basement",
    "Attic",
    "Hallway",
    "Dining Room",
    "Laundry Room",
    "Guest Room",
    "Family Room",
    "Patio",
    "Deck",
    "Workshop",
    "Pantry",
    "Mudroom",
)
assert len(_AREA_NAMES) == 18

_LONG_NAME = ("Kitchen North Corner Smart Plug Outlet Adapter " * 3)[:DEVICE_TEXT_MAX_CHARS]
_LONG_MANUFACTURER = ("Acme Consumer Electronics Manufacturing Company " * 2)[:DEVICE_TEXT_MAX_CHARS]
_LONG_MODEL = ("Ultra Premium Smart Home Device Model XJ-2000 Pro " * 2)[:DEVICE_TEXT_MAX_CHARS]


def _build_realistic_devices(hass: HomeAssistant) -> dict[str, str]:
    """18 ordinary-named areas and 60 qualifying devices at the target install's real counts."""
    areas = create_areas(hass, *_AREA_NAMES)
    for index in range(REALISTIC_AREA_COUNT):
        register_area_device(
            hass,
            f"realistic_{index:04d}",
            name=_LONG_NAME,
            manufacturer=_LONG_MANUFACTURER,
            model=_LONG_MODEL,
            entities=[
                AreaEntitySpec(domain="sensor", device_class="temperature"),
                AreaEntitySpec(domain="binary_sensor", device_class="motion"),
            ],
        )
    return areas


async def test_one_realistic_area_run_is_one_request_with_measured_headroom(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """18 areas and 60 full-size devices send one request, comfortably inside both token limits."""
    areas = _build_realistic_devices(hass)
    answers = {f"d{index}": area_answer(_AREA_NAMES[0], 0.9, list(areas)) for index in range(REALISTIC_AREA_COUNT)}
    register_jev_responses(aioclient_mock, [api_response(answers)])

    entry = MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: "test-key"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    bodies = posted_bodies(aioclient_mock)
    assert len(bodies) == 1
    body = bodies[0]
    assert len(body["questions"]) == REALISTIC_AREA_COUNT
    assert len(body["state"]["devices"]) == REALISTIC_AREA_COUNT

    factored_request = estimate_tokens(body) * SAFETY_FACTOR
    assert factored_request < REQUEST_TOKEN_LIMIT

    state_estimate = _estimate(body["state"])
    longest_question = max(_estimate(question) for question in body["questions"].values())
    factored_state = (state_estimate + longest_question) * SAFETY_FACTOR
    assert factored_state < STATE_TOKEN_LIMIT

    per_device_factored = factored_request / REALISTIC_AREA_COUNT
    ceiling = int(REQUEST_TOKEN_LIMIT // per_device_factored)
    print(
        f"realistic area device estimate, factored: {per_device_factored:.0f} tokens per device; "
        f"REQUEST_TOKEN_LIMIT trips past {ceiling} devices"
    )


async def test_an_oversized_area_request_is_refused_with_no_partial_result(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run too large to send is refused loudly rather than posting anything."""
    monkeypatch.setattr("custom_components.gutcheck.budget.REQUEST_TOKEN_LIMIT", 100)
    create_areas(hass, "Kitchen")
    register_area_device(hass, "a", name="Device A", entities=["sensor"])

    entry = MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: "test-key"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert posted_bodies(aioclient_mock) == []
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{RECIPE_AREAS}")
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "unavailable"
    tokens_entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_tokens_today")
    assert tokens_entity_id is not None
    tokens_state = hass.states.get(tokens_entity_id)
    assert tokens_state is not None
    assert tokens_state.state == "0"


def _full_size_update_body() -> dict[str, Any]:
    """A synthetic full request body for MAX_UPDATES_PER_RUN full-size update items."""
    updates = [
        {
            "integration": "homeassistant_supervisor",
            "installed_version": "2026.9.1",
            "latest_version": "2026.10.0",
            "version_jump": VERSION_JUMP_MAJOR,
            "title": "Home Assistant Supervisor 2026.10.0",
            "release_summary": "x" * 255,
            "release_notes": "x" * RELEASE_NOTES_MAX_CHARS,
        }
        for _ in range(MAX_UPDATES_PER_RUN)
    ]
    questions = {
        f"u{index}": {"type": "score", "instructions": UPDATE_INSTRUCTIONS.format(index=index), "criteria": UPDATE_CRITERIA}
        for index in range(MAX_UPDATES_PER_RUN)
    }
    return {"state": {"updates": updates}, "model": MODEL, "questions": questions}


async def test_a_health_run_a_full_update_run_and_a_realistic_area_run_together_fit_the_default_budget(
    hass: HomeAssistant,
) -> None:
    """The real captured health payload, a full-size update run and a realistic area run together fit day one."""
    health_payload = load_fixture("captured", "health_payload.json")
    health_factored = estimate_tokens(health_payload) * SAFETY_FACTOR

    update_factored = estimate_tokens(_full_size_update_body()) * SAFETY_FACTOR

    _build_realistic_devices(hass)
    recipe = AreaRecipe(critical_label=None)
    batch = await recipe.async_prepare(hass)
    area_body = {"state": batch.state, "model": MODEL, "questions": batch.questions}
    area_factored = estimate_tokens(area_body) * SAFETY_FACTOR

    total = health_factored + update_factored + area_factored
    print(
        f"first-day total, factored: health={health_factored:.0f} update={update_factored:.0f} "
        f"area={area_factored:.0f} total={total:.0f}"
    )
    assert total <= DEFAULT_DAILY_BUDGET
