"""Selection, carry, index alignment, gate edges and the no-label cases for critical label suggestions."""

from __future__ import annotations

from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import label_registry as lr
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import (
    CONF_AREAS_ENABLED,
    CONF_CONFIG_ENTRIES_ENABLED,
    CONF_CRITICAL_LABEL_ENABLED,
    CONF_DEVICE_CLASS_ENABLED,
    CONF_HEALTH_ENABLED,
    CONF_UPDATES_ENABLED,
    CRITICAL_LABEL_ISSUE_PREFIX,
    DOMAIN,
    RECIPE_CRITICAL_LABEL,
)
from custom_components.gutcheck.recipes.critical_label_const import OPTION_CRITICAL, OPTION_NOT_CRITICAL

from .conftest import (
    api_response,
    critical_label_answer,
    posted_bodies,
    press_recipe_run,
    recipe_sensor_entity_id,
    register_jev_responses_by_question,
    register_unit_sensor,
)


async def test_code_decided_binary_sensors_carry_with_no_request_and_still_raise_cards(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, critical_label_entry: MockConfigEntry
) -> None:
    """Smoke, CO and gas binary sensors carry with no question; a run with only these makes no POST."""
    lr.async_get(hass).async_create("Critical")
    smoke = register_unit_sensor(hass, "smoke1", domain="binary_sensor", unit=None, original_device_class="smoke")
    co = register_unit_sensor(hass, "co1", domain="binary_sensor", unit=None, original_device_class="carbon_monoxide")
    gas = register_unit_sensor(hass, "gas1", domain="binary_sensor", unit=None, original_device_class="gas")
    critical_label_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(critical_label_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert posted_bodies(aioclient_mock) == []
    state = hass.states.get(recipe_sensor_entity_id(hass, critical_label_entry, RECIPE_CRITICAL_LABEL))
    assert state is not None
    assert state.state == "3"
    for entry in (smoke, co, gas):
        issue_id = f"{CRITICAL_LABEL_ISSUE_PREFIX}{entry.id}"
        assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None


async def test_config_or_diagnostic_smoke_sensor_gets_no_card(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, critical_label_entry: MockConfigEntry
) -> None:
    """A config or diagnostic smoke sensor is not a candidate, even though smoke is decided in code."""
    lr.async_get(hass).async_create("Critical")
    diagnostic = register_unit_sensor(
        hass,
        "smoke_diag",
        domain="binary_sensor",
        unit=None,
        original_device_class="smoke",
        entity_category=er.EntityCategory.DIAGNOSTIC,
    )
    config = register_unit_sensor(
        hass,
        "smoke_config",
        domain="binary_sensor",
        unit=None,
        original_device_class="smoke",
        entity_category=er.EntityCategory.CONFIG,
    )
    critical_label_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(critical_label_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert posted_bodies(aioclient_mock) == []
    for entry in (diagnostic, config):
        assert ir.async_get(hass).async_get_issue(DOMAIN, f"{CRITICAL_LABEL_ISSUE_PREFIX}{entry.id}") is None


async def test_moisture_binary_sensor_is_asked_not_code_decided(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, critical_label_entry: MockConfigEntry
) -> None:
    """A moisture binary sensor (rain or leak alike) is asked like a valve, switch or siren, not decided in code."""
    lr.async_get(hass).async_create("Critical")
    rain = register_unit_sensor(hass, "rain1", domain="binary_sensor", unit=None, original_device_class="moisture", name="Rain")
    register_jev_responses_by_question(
        aioclient_mock, {"k0": api_response({"k0": critical_label_answer(OPTION_NOT_CRITICAL, 0.9)})}
    )
    critical_label_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(critical_label_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    bodies = posted_bodies(aioclient_mock)
    assert len(bodies) == 1
    assert bodies[0]["state"]["entities"][0]["name"] == "Rain"
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, f"{CRITICAL_LABEL_ISSUE_PREFIX}{rain.id}") is None


async def test_config_and_diagnostic_entity_category_are_never_asked(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, critical_label_entry: MockConfigEntry
) -> None:
    """A config or diagnostic switch is never asked; a plain switch and a valve still are."""
    lr.async_get(hass).async_create("Critical")
    config_switch = register_unit_sensor(
        hass, "config_switch", domain="switch", unit=None, name="Post data", entity_category=er.EntityCategory.CONFIG
    )
    diagnostic_switch = register_unit_sensor(
        hass, "diag_switch", domain="switch", unit=None, name="Signal strength", entity_category=er.EntityCategory.DIAGNOSTIC
    )
    plain_switch = register_unit_sensor(hass, "plain_switch", domain="switch", unit=None, name="Lamp")
    valve = register_unit_sensor(hass, "main_valve", domain="valve", unit=None, name="Main valve")
    register_jev_responses_by_question(
        aioclient_mock,
        {
            "k0": api_response(
                {"k0": critical_label_answer(OPTION_NOT_CRITICAL, 0.9), "k1": critical_label_answer(OPTION_NOT_CRITICAL, 0.9)}
            )
        },
    )
    critical_label_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(critical_label_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    bodies = posted_bodies(aioclient_mock)
    assert len(bodies) == 1
    asked_names = {item["name"] for item in bodies[0]["state"]["entities"]}
    assert asked_names == {"Lamp", "Main valve"}
    registry = ir.async_get(hass)
    for entry in (config_switch, diagnostic_switch):
        assert registry.async_get_issue(DOMAIN, f"{CRITICAL_LABEL_ISSUE_PREFIX}{entry.id}") is None
    for entry in (plain_switch, valve):
        assert registry.async_get_issue(DOMAIN, f"{CRITICAL_LABEL_ISSUE_PREFIX}{entry.id}") is None


async def test_user_override_wins_over_original_device_class(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, critical_label_entry: MockConfigEntry
) -> None:
    """A door override on an originally smoke sensor is not a candidate; a smoke override on an originally door sensor is."""
    lr.async_get(hass).async_create("Critical")
    overridden_away = register_unit_sensor(
        hass, "was_smoke", domain="binary_sensor", unit=None, original_device_class="smoke", device_class="door"
    )
    overridden_in = register_unit_sensor(
        hass, "was_door", domain="binary_sensor", unit=None, original_device_class="door", device_class="smoke"
    )
    critical_label_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(critical_label_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert posted_bodies(aioclient_mock) == []
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, f"{CRITICAL_LABEL_ISSUE_PREFIX}{overridden_away.id}") is None
    assert registry.async_get_issue(DOMAIN, f"{CRITICAL_LABEL_ISSUE_PREFIX}{overridden_in.id}") is not None


async def test_door_motion_and_unclassed_binary_sensors_are_never_candidates(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, critical_label_entry: MockConfigEntry
) -> None:
    """A door, motion, or unclassed binary sensor never becomes a candidate."""
    lr.async_get(hass).async_create("Critical")
    door = register_unit_sensor(hass, "door1", domain="binary_sensor", unit=None, original_device_class="door")
    motion = register_unit_sensor(hass, "motion1", domain="binary_sensor", unit=None, original_device_class="motion")
    unclassed = register_unit_sensor(hass, "unclassed1", domain="binary_sensor", unit=None)
    critical_label_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(critical_label_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert posted_bodies(aioclient_mock) == []
    registry = ir.async_get(hass)
    for entry in (door, motion, unclassed):
        assert registry.async_get_issue(DOMAIN, f"{CRITICAL_LABEL_ISSUE_PREFIX}{entry.id}") is None
    state = hass.states.get(recipe_sensor_entity_id(hass, critical_label_entry, RECIPE_CRITICAL_LABEL))
    assert state is not None
    assert state.state == "0"


async def test_switches_are_asked_by_domain_alone_never_by_name(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, critical_label_entry: MockConfigEntry
) -> None:
    """A switch named Water shutoff and one named Desk lamp are both asked; a sensor or light never is, whatever its name."""
    lr.async_get(hass).async_create("Critical")
    shutoff = register_unit_sensor(hass, "shutoff", domain="switch", unit=None, name="Water shutoff")
    lamp = register_unit_sensor(hass, "lamp", domain="switch", unit=None, name="Desk lamp")
    smoke_named_sensor = register_unit_sensor(hass, "smoke_name", domain="sensor", unit="ppm", name="Smoke")
    leak_named_light = register_unit_sensor(hass, "leak_name", domain="light", unit=None, name="Leak")
    register_jev_responses_by_question(
        aioclient_mock,
        {
            "k0": api_response(
                {"k0": critical_label_answer(OPTION_NOT_CRITICAL, 0.9), "k1": critical_label_answer(OPTION_NOT_CRITICAL, 0.9)}
            )
        },
    )
    critical_label_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(critical_label_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    bodies = posted_bodies(aioclient_mock)
    assert len(bodies) == 1
    asked_names = {item["name"] for item in bodies[0]["state"]["entities"]}
    assert asked_names == {"Water shutoff", "Desk lamp"}
    registry = ir.async_get(hass)
    for entry in (smoke_named_sensor, leak_named_light):
        assert registry.async_get_issue(DOMAIN, f"{CRITICAL_LABEL_ISSUE_PREFIX}{entry.id}") is None
    for entry in (shutoff, lamp):
        assert registry.async_get_issue(DOMAIN, f"{CRITICAL_LABEL_ISSUE_PREFIX}{entry.id}") is None


async def test_blocked_domains_disabled_and_labelled_entities_are_never_candidates(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, critical_label_entry: MockConfigEntry
) -> None:
    """Disabled, own-platform, lock, alarm panel, cover, and label-carrying entities are never candidates."""
    lr.async_get(hass).async_create("Critical")
    lr.async_get(hass).async_create("Plumbing")
    disabled = register_unit_sensor(hass, "disabled_valve", domain="valve", unit=None, disabled_by=er.RegistryEntryDisabler.USER)
    own_platform = register_unit_sensor(hass, "own_valve", domain="valve", unit=None, platform=DOMAIN)
    lock = register_unit_sensor(hass, "lock1", domain="lock", unit=None)
    alarm = register_unit_sensor(hass, "alarm1", domain="alarm_control_panel", unit=None)
    cover = register_unit_sensor(hass, "cover1", domain="cover", unit=None)
    self_labelled = register_unit_sensor(hass, "self_labelled", domain="valve", unit=None, labels=frozenset({"critical"}))
    device_labelled = register_unit_sensor(
        hass, "device_labelled", domain="valve", unit=None, device_name="Labelled device", device_labels=frozenset({"critical"})
    )
    other_labelled = register_unit_sensor(hass, "other_labelled", domain="valve", unit=None, labels=frozenset({"plumbing"}))
    register_jev_responses_by_question(
        aioclient_mock, {"k0": api_response({"k0": critical_label_answer(OPTION_NOT_CRITICAL, 0.9)})}
    )
    critical_label_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(critical_label_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    bodies = posted_bodies(aioclient_mock)
    assert len(bodies) == 1
    assert len(bodies[0]["state"]["entities"]) == 1
    registry = ir.async_get(hass)
    for entry in (disabled, own_platform, lock, alarm, cover, self_labelled, device_labelled):
        assert registry.async_get_issue(DOMAIN, f"{CRITICAL_LABEL_ISSUE_PREFIX}{entry.id}") is None
    assert registry.async_get_issue(DOMAIN, f"{CRITICAL_LABEL_ISSUE_PREFIX}{other_labelled.id}") is None


async def test_code_decided_entries_never_shift_question_indices(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, critical_label_entry: MockConfigEntry
) -> None:
    """Two code-decided binary sensors ahead of three asked entities in sorted order leave question indices untouched."""
    lr.async_get(hass).async_create("Critical")
    co = register_unit_sensor(hass, "co1", domain="binary_sensor", unit=None, original_device_class="carbon_monoxide")
    smoke = register_unit_sensor(hass, "smoke1", domain="binary_sensor", unit=None, original_device_class="smoke")
    siren = register_unit_sensor(hass, "s1", domain="siren", unit=None, name="Siren")
    switch = register_unit_sensor(hass, "sw1", domain="switch", unit=None, name="Switch")
    valve = register_unit_sensor(hass, "v1", domain="valve", unit=None, name="Valve")
    register_jev_responses_by_question(
        aioclient_mock,
        {
            "k0": api_response(
                {
                    "k0": critical_label_answer(OPTION_CRITICAL, 0.9),
                    "k1": critical_label_answer(OPTION_NOT_CRITICAL, 0.9),
                    "k2": critical_label_answer(OPTION_CRITICAL, 0.9),
                }
            )
        },
    )
    critical_label_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(critical_label_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    bodies = posted_bodies(aioclient_mock)
    assert len(bodies) == 1
    entities = bodies[0]["state"]["entities"]
    assert [item["domain"] for item in entities] == ["siren", "switch", "valve"]

    state = hass.states.get(recipe_sensor_entity_id(hass, critical_label_entry, RECIPE_CRITICAL_LABEL))
    assert state is not None
    suggested_ids = {item["registry_id"] for item in state.attributes["items"]["suggested"]}
    assert suggested_ids == {siren.id, valve.id, co.id, smoke.id}
    not_critical_ids = {item["registry_id"] for item in state.attributes["items"]["not_critical"]}
    assert not_critical_ids == {switch.id}


async def test_gate_edges_land_in_unsure_or_not_critical_with_no_card(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, critical_label_entry: MockConfigEntry
) -> None:
    """Confident not_critical gets its own bucket; low confidence, none of these, off-criteria and malformed land in unsure."""
    lr.async_get(hass).async_create("Critical")
    # Registered in a fixed set so entity_id sort order (and so question id) is
    # predictable: low_conf(k0) < malformed(k1) < none_of_these(k2) < not_critical(k3) < off_criteria(k4).
    low_confidence = register_unit_sensor(hass, "low_conf", domain="valve", unit=None)
    malformed = register_unit_sensor(hass, "malformed", domain="valve", unit=None)
    none_of_these = register_unit_sensor(hass, "none_of_these", domain="valve", unit=None)
    not_critical = register_unit_sensor(hass, "not_critical", domain="valve", unit=None)
    off_criteria = register_unit_sensor(hass, "off_criteria", domain="valve", unit=None)
    register_jev_responses_by_question(
        aioclient_mock,
        {
            "k0": api_response(
                {
                    "k0": critical_label_answer(OPTION_CRITICAL, 0.2),
                    "k1": "not-a-dict",
                    "k2": critical_label_answer("none_of_these", 0.9),
                    "k3": critical_label_answer("not_critical", 0.9),
                    "k4": {"type": "choice", "choice": "garbage", "probabilities": {}, "confidence": 0.9},
                }
            )
        },
    )
    critical_label_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(critical_label_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    state = hass.states.get(recipe_sensor_entity_id(hass, critical_label_entry, RECIPE_CRITICAL_LABEL))
    assert state is not None
    assert state.attributes["items"]["suggested"] == []
    not_critical_ids = {item["registry_id"] for item in state.attributes["items"]["not_critical"]}
    assert not_critical_ids == {not_critical.id}
    unsure_ids = {item["registry_id"] for item in state.attributes["unsure"]}
    assert unsure_ids == {low_confidence.id, none_of_these.id, off_criteria.id, malformed.id}
    registry = ir.async_get(hass)
    for entry in (not_critical, low_confidence, none_of_these, off_criteria, malformed):
        assert registry.async_get_issue(DOMAIN, f"{CRITICAL_LABEL_ISSUE_PREFIX}{entry.id}") is None


async def test_no_label_configured_makes_no_request(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """With no critical label configured, a run makes no request and reads 0, even with candidates registered."""
    register_unit_sensor(hass, "any_valve", domain="valve", unit=None)
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
        },
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert posted_bodies(aioclient_mock) == []
    state = hass.states.get(recipe_sensor_entity_id(hass, entry, RECIPE_CRITICAL_LABEL))
    assert state is not None
    assert state.state == "0"


async def test_deleting_the_configured_label_clears_open_cards_and_keeps_ignored_ones(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, critical_label_entry: MockConfigEntry
) -> None:
    """A run with the configured label deleted from the label registry makes no request, drops open cards, keeps ignored ones."""
    lr.async_get(hass).async_create("Critical")
    open_candidate = register_unit_sensor(hass, "open_valve", domain="valve", unit=None)
    ignored_candidate = register_unit_sensor(hass, "ignored_valve", domain="valve", unit=None)
    register_jev_responses_by_question(
        aioclient_mock,
        {
            "k0": api_response(
                {"k0": critical_label_answer(OPTION_CRITICAL, 0.9), "k1": critical_label_answer(OPTION_CRITICAL, 0.9)}
            )
        },
    )
    critical_label_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(critical_label_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    registry = ir.async_get(hass)
    open_issue_id = f"{CRITICAL_LABEL_ISSUE_PREFIX}{open_candidate.id}"
    ignored_issue_id = f"{CRITICAL_LABEL_ISSUE_PREFIX}{ignored_candidate.id}"
    assert registry.async_get_issue(DOMAIN, open_issue_id) is not None
    assert registry.async_get_issue(DOMAIN, ignored_issue_id) is not None
    ir.async_ignore_issue(hass, DOMAIN, ignored_issue_id, True)

    lr.async_get(hass).async_delete("critical")
    await press_recipe_run(hass, critical_label_entry, RECIPE_CRITICAL_LABEL)

    assert registry.async_get_issue(DOMAIN, open_issue_id) is None
    kept = registry.async_get_issue(DOMAIN, ignored_issue_id)
    assert kept is not None
    assert kept.dismissed_version is not None
