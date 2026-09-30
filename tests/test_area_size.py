"""One area run goes out as capped requests with measured headroom, and all three recipes fit the daily budget."""

from __future__ import annotations

import pytest
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.budget import _reservation
from custom_components.gutcheck.const import (
    DEFAULT_DAILY_BUDGET,
    DEVICE_TEXT_MAX_CHARS,
    DOMAIN,
    RECIPE_AREAS,
)
from custom_components.gutcheck.recipes.areas import AreaRecipe
from custom_components.gutcheck.split import split_batch

from .conftest import (
    AreaEntitySpec,
    area_answer,
    assert_capped_run_fits,
    create_areas,
    load_fixture,
    posted_bodies,
    register_area_device,
    register_jev_answers,
)

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


async def test_one_realistic_area_run_goes_out_as_capped_requests_with_measured_headroom(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """18 areas and 60 full-size devices go out ten to a request, each request comfortably inside both token limits."""
    areas = _build_realistic_devices(hass)
    answers = {f"d{index}": area_answer(_AREA_NAMES[0], 0.9, list(areas)) for index in range(REALISTIC_AREA_COUNT)}
    register_jev_answers(aioclient_mock, answers)

    entry = MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: "test-key"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    total = assert_capped_run_fits(posted_bodies(aioclient_mock), "devices", REALISTIC_AREA_COUNT)
    print(f"realistic area device estimate, factored: {total / REALISTIC_AREA_COUNT:.0f} tokens per device")


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


async def test_the_captured_installs_first_day_runs_reserved_at_once_fit_the_default_budget(
    hass: HomeAssistant,
) -> None:
    """The captured install's health, update and area runs, admitted concurrently, still fit day one."""
    health_payload = load_fixture("captured", "health_payload.json")
    health_reserved = _reservation(health_payload)

    update_payload = load_fixture("captured", "update_payload.json")
    update_reserved = _reservation(update_payload)

    _build_realistic_devices(hass)
    recipe = AreaRecipe(critical_label=None)
    batch = await recipe.async_prepare(hass)
    area_reserved = sum(_reservation(request) for request in split_batch(batch))

    total = health_reserved + update_reserved + area_reserved
    print(f"first-day total, reserved: health={health_reserved} update={update_reserved} area={area_reserved} total={total}")
    assert total <= DEFAULT_DAILY_BUDGET
