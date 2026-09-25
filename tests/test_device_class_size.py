"""One device class run at the target install's real counts fits one request with measured headroom."""

from __future__ import annotations

from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.budget import _estimate, _reservation, estimate_tokens
from custom_components.gutcheck.const import (
    CONF_AREAS_ENABLED,
    CONF_DEVICE_CLASS_ENABLED,
    CONF_HEALTH_ENABLED,
    CONF_UPDATES_ENABLED,
    DEVICE_TEXT_MAX_CHARS,
    DOMAIN,
    REQUEST_TOKEN_LIMIT,
    STATE_TOKEN_LIMIT,
)

from .conftest import (
    api_response,
    area_answer,
    device_class_sensor_entity_id,
    posted_bodies,
    register_jev_responses,
    register_unit_sensor,
)

# The budget gate's own estimator undercounts a real payload; every comparison here applies this factor before checking a limit.
SAFETY_FACTOR = 1.15

# The target install's own real counts (see the phase research addendum, folded
# in with the three units that used to narrow to exactly one class, since every
# qualifying sensor is asked now): 58 sensors are asked, and 25 sit on a unit no
# device class accepts.
ASKED_UNIT_COUNTS = {
    "%": 35,
    "gal": 10,
    "m/s": 2,
    "°F": 2,
    "hPa": 1,
    "ppb": 1,
    "ppm": 1,
    "μg/m³": 1,
    "m": 3,
    "dBm": 1,
    "s": 1,
}
assert sum(ASKED_UNIT_COUNTS.values()) == 58

UNMATCHED_COUNT = 25

PERCENT_CANDIDATES = ["battery", "humidity", "moisture", "power_factor"]

_LONG_NAME = ("Sensor Display Name Chosen By The User " * 3)[:DEVICE_TEXT_MAX_CHARS]
_LONG_DEVICE_NAME = ("Device Display Name Chosen By The Maker " * 3)[:DEVICE_TEXT_MAX_CHARS]
_LONG_MANUFACTURER = ("Acme Consumer Electronics Manufacturing " * 3)[:DEVICE_TEXT_MAX_CHARS]
_LONG_MODEL = ("Ultra Premium Smart Sensor Model XJ-2000 Pro " * 3)[:DEVICE_TEXT_MAX_CHARS]


def _build_realistic_sensors(hass: HomeAssistant) -> None:
    """58 asked and 25 unmatched sensors at the target install's real unit mix."""
    index = 0
    for unit, count in ASKED_UNIT_COUNTS.items():
        for _ in range(count):
            register_unit_sensor(
                hass,
                f"realistic_{index:04d}",
                unit=unit,
                name=_LONG_NAME,
                device_name=_LONG_DEVICE_NAME,
                manufacturer=_LONG_MANUFACTURER,
                model=_LONG_MODEL,
            )
            index += 1
    for _ in range(UNMATCHED_COUNT):
        register_unit_sensor(
            hass,
            f"realistic_{index:04d}",
            unit="pages",
            name=_LONG_NAME,
            device_name=_LONG_DEVICE_NAME,
            manufacturer=_LONG_MANUFACTURER,
            model=_LONG_MODEL,
        )
        index += 1


async def test_one_realistic_device_class_run_is_one_request_with_measured_headroom(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """58 asked and 25 unmatched sensors send one request, comfortably inside both token limits."""
    _build_realistic_sensors(hass)
    answers = {f"s{index}": area_answer("battery", 0.9, PERCENT_CANDIDATES) for index in range(58)}
    register_jev_responses(aioclient_mock, [api_response(answers)])

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_API_KEY: "test-key"},
        options={
            CONF_HEALTH_ENABLED: False,
            CONF_UPDATES_ENABLED: False,
            CONF_AREAS_ENABLED: False,
            CONF_DEVICE_CLASS_ENABLED: True,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    bodies = posted_bodies(aioclient_mock)
    assert len(bodies) == 1
    body = bodies[0]
    assert len(body["questions"]) == 58
    assert len(body["state"]["sensors"]) == 58

    factored_request = estimate_tokens(body) * SAFETY_FACTOR
    assert factored_request < REQUEST_TOKEN_LIMIT

    state_estimate = _estimate(body["state"])
    longest_question = max(_estimate(question) for question in body["questions"].values())
    factored_state = (state_estimate + longest_question) * SAFETY_FACTOR
    assert factored_state < STATE_TOKEN_LIMIT

    per_sensor_factored = factored_request / 58
    print(
        f"realistic device class estimate, factored: {per_sensor_factored:.0f} tokens per asked sensor; "
        f"reservation={_reservation(body)}"
    )

    state = hass.states.get(device_class_sensor_entity_id(hass, entry))
    assert state is not None
