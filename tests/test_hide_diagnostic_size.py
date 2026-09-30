"""One diagnostic sensor suggestions run at the target install's real asked count goes out capped, with headroom."""

from __future__ import annotations

from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import (
    CONF_AREAS_ENABLED,
    CONF_CONFIG_ENTRIES_ENABLED,
    CONF_CRITICAL_LABEL_ENABLED,
    CONF_DEVICE_CLASS_ENABLED,
    CONF_HEALTH_ENABLED,
    CONF_HIDE_DIAGNOSTIC_ENABLED,
    CONF_UPDATES_ENABLED,
    DEVICE_TEXT_MAX_CHARS,
    DOMAIN,
    RECIPE_HIDE_DIAGNOSTIC,
)
from custom_components.gutcheck.recipes.hide_diagnostic_const import OPTION_PRIMARY

from .conftest import (
    assert_capped_run_fits,
    hide_diagnostic_answer,
    posted_bodies,
    recipe_sensor_entity_id,
    register_jev_answers,
    register_unit_sensor,
)

# The target install's own real asked count from the live run: 122 primary
# sensors with no device class, from 20 integrations.
ASKED_COUNT = 122

_LONG_NAME = ("Sensor Display Name Chosen By The User " * 3)[:DEVICE_TEXT_MAX_CHARS]
_LONG_DEVICE_NAME = ("Device Display Name Chosen By The Maker " * 3)[:DEVICE_TEXT_MAX_CHARS]
_LONG_MANUFACTURER = ("Acme Consumer Electronics Manufacturing " * 3)[:DEVICE_TEXT_MAX_CHARS]
_LONG_MODEL = ("Ultra Premium Smart Sensor Model XJ-2000 Pro " * 3)[:DEVICE_TEXT_MAX_CHARS]
_LONG_UNIT = ("Very Long Unit Of Measurement " * 3)[:DEVICE_TEXT_MAX_CHARS]


def _build_realistic_sensors(hass: HomeAssistant) -> None:
    """ASKED_COUNT classless sensors, each with name, device fields and unit at DEVICE_TEXT_MAX_CHARS."""
    for index in range(ASKED_COUNT):
        register_unit_sensor(
            hass,
            f"hide_realistic_{index:04d}",
            unit=_LONG_UNIT,
            name=_LONG_NAME,
            device_name=_LONG_DEVICE_NAME,
            manufacturer=_LONG_MANUFACTURER,
            model=_LONG_MODEL,
            state_class="measurement",
        )


async def test_one_realistic_hide_diagnostic_run_fits_the_token_limits_and_default_budget(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """122 asked sensors at the real target-install count go out ten to a request, inside both token limits and the budget."""
    _build_realistic_sensors(hass)
    answers = {f"h{index}": hide_diagnostic_answer(OPTION_PRIMARY, 0.9) for index in range(ASKED_COUNT)}
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
            CONF_CRITICAL_LABEL_ENABLED: False,
            CONF_HIDE_DIAGNOSTIC_ENABLED: True,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    total = assert_capped_run_fits(posted_bodies(aioclient_mock), "sensors", ASKED_COUNT)
    print(f"realistic diagnostic sensor estimate, factored: {total / ASKED_COUNT:.0f} tokens per asked sensor")

    state = hass.states.get(recipe_sensor_entity_id(hass, entry, RECIPE_HIDE_DIAGNOSTIC))
    assert state is not None
