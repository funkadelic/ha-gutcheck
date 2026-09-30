"""One critical label suggestions run at the target install's real asked count goes out capped, with headroom."""

from __future__ import annotations

from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.helpers import label_registry as lr
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import (
    CONF_AREAS_ENABLED,
    CONF_CONFIG_ENTRIES_ENABLED,
    CONF_CRITICAL_LABEL,
    CONF_CRITICAL_LABEL_ENABLED,
    CONF_DEVICE_CLASS_ENABLED,
    CONF_HEALTH_ENABLED,
    CONF_UPDATES_ENABLED,
    DEVICE_TEXT_MAX_CHARS,
    DOMAIN,
    RECIPE_CRITICAL_LABEL,
)
from custom_components.gutcheck.recipes.critical_label_const import OPTION_NOT_CRITICAL

from .conftest import (
    assert_capped_run_fits,
    critical_label_answer,
    posted_bodies,
    recipe_sensor_entity_id,
    register_jev_answers,
    register_unit_sensor,
)

# The target install's own real asked count from the live capture: 2 moisture
# binary sensors, 55 switches and 2 valves, 59 in all. Registered here as
# switches, the plainest of the three asked domains: token cost tracks text
# length and field count, not the domain name.
ASKED_COUNT = 59

_LONG_NAME = ("Switch Display Name Chosen By The User " * 3)[:DEVICE_TEXT_MAX_CHARS]
_LONG_DEVICE_NAME = ("Device Display Name Chosen By The Maker " * 3)[:DEVICE_TEXT_MAX_CHARS]
_LONG_MANUFACTURER = ("Acme Consumer Electronics Manufacturing " * 3)[:DEVICE_TEXT_MAX_CHARS]
_LONG_MODEL = ("Ultra Premium Smart Switch Model XJ-2000 Pro " * 3)[:DEVICE_TEXT_MAX_CHARS]


def _build_realistic_switches(hass: HomeAssistant) -> None:
    """ASKED_COUNT switches, each with name and device fields at DEVICE_TEXT_MAX_CHARS."""
    for index in range(ASKED_COUNT):
        register_unit_sensor(
            hass,
            f"critical_realistic_{index:04d}",
            domain="switch",
            unit=None,
            name=_LONG_NAME,
            device_name=_LONG_DEVICE_NAME,
            manufacturer=_LONG_MANUFACTURER,
            model=_LONG_MODEL,
        )


async def test_one_realistic_critical_label_run_fits_the_token_limits_and_default_budget(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """59 asked switches at the real target-install count go out ten to a request, inside both token limits and the budget."""
    lr.async_get(hass).async_create("Critical")
    _build_realistic_switches(hass)
    answers = {f"k{index}": critical_label_answer(OPTION_NOT_CRITICAL, 0.9) for index in range(ASKED_COUNT)}
    register_jev_answers(aioclient_mock, answers)

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_API_KEY: "test-key"},
        options={
            CONF_HEALTH_ENABLED: False,
            CONF_UPDATES_ENABLED: False,
            CONF_AREAS_ENABLED: False,
            CONF_DEVICE_CLASS_ENABLED: False,
            CONF_CONFIG_ENTRIES_ENABLED: False,
            CONF_CRITICAL_LABEL_ENABLED: True,
            CONF_CRITICAL_LABEL: "critical",
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    total = assert_capped_run_fits(posted_bodies(aioclient_mock), "entities", ASKED_COUNT)
    print(f"realistic critical label estimate, factored: {total / ASKED_COUNT:.0f} tokens per asked entity")

    state = hass.states.get(recipe_sensor_entity_id(hass, entry, RECIPE_CRITICAL_LABEL))
    assert state is not None
