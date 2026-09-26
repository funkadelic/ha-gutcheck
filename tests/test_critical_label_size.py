"""One critical label suggestions run at the target install's real asked count fits one request with measured headroom."""

from __future__ import annotations

from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.helpers import label_registry as lr
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.budget import _estimate, _reservation, estimate_tokens
from custom_components.gutcheck.const import (
    CONF_AREAS_ENABLED,
    CONF_CONFIG_ENTRIES_ENABLED,
    CONF_CRITICAL_LABEL,
    CONF_CRITICAL_LABEL_ENABLED,
    CONF_DEVICE_CLASS_ENABLED,
    CONF_HEALTH_ENABLED,
    CONF_UPDATES_ENABLED,
    DEFAULT_DAILY_BUDGET,
    DEVICE_TEXT_MAX_CHARS,
    DOMAIN,
    RECIPE_CRITICAL_LABEL,
    REQUEST_TOKEN_LIMIT,
    STATE_TOKEN_LIMIT,
)
from custom_components.gutcheck.recipes.critical_label_const import OPTION_NOT_CRITICAL

from .conftest import (
    api_response,
    critical_label_answer,
    posted_bodies,
    recipe_sensor_entity_id,
    register_jev_responses,
    register_unit_sensor,
)

# The budget gate's own estimator undercounts a real payload; every comparison here applies this factor before checking a limit.
SAFETY_FACTOR = 1.15

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
    """59 asked switches at the real target-install count send one request, inside both token limits and the daily budget."""
    lr.async_get(hass).async_create("Critical")
    _build_realistic_switches(hass)
    answers = {f"k{index}": critical_label_answer(OPTION_NOT_CRITICAL, 0.9) for index in range(ASKED_COUNT)}
    register_jev_responses(aioclient_mock, [api_response(answers)])

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

    bodies = posted_bodies(aioclient_mock)
    assert len(bodies) == 1
    body = bodies[0]
    assert len(body["questions"]) == ASKED_COUNT
    assert len(body["state"]["entities"]) == ASKED_COUNT

    factored_request = estimate_tokens(body) * SAFETY_FACTOR
    assert factored_request < REQUEST_TOKEN_LIMIT

    state_estimate = _estimate(body["state"])
    longest_question = max(_estimate(question) for question in body["questions"].values())
    factored_state = (state_estimate + longest_question) * SAFETY_FACTOR
    assert factored_state < STATE_TOKEN_LIMIT

    reservation = _reservation(body)
    assert reservation < DEFAULT_DAILY_BUDGET, (
        f"a run at the target install's real asked count would reserve {reservation} tokens against a "
        f"default daily budget of {DEFAULT_DAILY_BUDGET}: raise the default budget, cap asks per run, or "
        "leave out config and diagnostic switches"
    )

    per_entity_factored = factored_request / ASKED_COUNT
    print(
        f"realistic critical label estimate, factored: {per_entity_factored:.0f} tokens per asked entity; "
        f"reservation={reservation}"
    )

    state = hass.states.get(recipe_sensor_entity_id(hass, entry, RECIPE_CRITICAL_LABEL))
    assert state is not None
