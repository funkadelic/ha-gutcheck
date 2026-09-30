"""Selection, the code-decided path, the open-card rule, index alignment and the gate for diagnostic sensor suggestions."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import label_registry as lr
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import (
    ATTR_COUNTS,
    ATTR_UNSURE,
    CONF_AREAS_ENABLED,
    CONF_CONFIG_ENTRIES_ENABLED,
    CONF_CRITICAL_LABEL_ENABLED,
    CONF_DEVICE_CLASS_ENABLED,
    CONF_HEALTH_ENABLED,
    CONF_HIDE_DIAGNOSTIC_ENABLED,
    CONF_UPDATES_ENABLED,
    DEVICE_CLASS_ISSUE_PREFIX,
    DOMAIN,
    HIDE_DIAGNOSTIC_ISSUE_PREFIX,
    OPTION_NONE,
    OPTION_SUGGESTED,
    RECIPE_DEVICE_CLASS,
    RECIPE_HIDE_DIAGNOSTIC,
)
from custom_components.gutcheck.recipes.device_class_cards import sync_device_class_cards
from custom_components.gutcheck.recipes.device_class_describe import candidate_classes
from custom_components.gutcheck.recipes.hide_diagnostic import HideDiagnosticRecipe
from custom_components.gutcheck.recipes.hide_diagnostic_const import OPTION_DIAGNOSTIC, OPTION_PRIMARY
from custom_components.gutcheck.recipes.hide_diagnostic_describe import qualifies
from custom_components.gutcheck.recipes.safety import SafetyRules
from custom_components.gutcheck.recipes.shapes import Batch

from .conftest import (
    api_response,
    area_answer,
    confirm_suggestion_card,
    hide_diagnostic_answer,
    posted_bodies,
    press_recipe_run,
    recipe_sensor_entity_id,
    register_jev_responses_by_question,
    register_unit_sensor,
)


def _asked(batch: Batch) -> set[str]:
    """The entity ids the batch asks a question about."""
    return {str(subject["entity_id"]) for subject in batch.subjects.values()}


def _carried(batch: Batch) -> set[str]:
    """The entity ids the batch decides in code."""
    return {str(item["entity_id"]) for item in batch.carried.get(OPTION_SUGGESTED, [])}


def _issue(hass: HomeAssistant, prefix: str, sensor: er.RegistryEntry) -> ir.IssueEntry | None:
    """The card under this prefix for this sensor, or None."""
    return ir.async_get(hass).async_get_issue(DOMAIN, f"{prefix}{sensor.id}")


async def test_signal_strength_sensors_are_decided_in_code_by_their_effective_class(hass: HomeAssistant) -> None:
    """An original or overridden signal_strength class is carried with no question; an override away from it is not."""
    original = register_unit_sensor(hass, "sig_original", unit="dBm", original_device_class="signal_strength")
    override = register_unit_sensor(hass, "sig_override", unit="dBm", device_class="signal_strength")
    overridden_away = register_unit_sensor(
        hass, "sig_away", unit="dBm", original_device_class="signal_strength", device_class="temperature"
    )

    batch = await HideDiagnosticRecipe(None).async_prepare(hass)

    assert _carried(batch) == {original.entity_id, override.entity_id}
    assert _asked(batch) == set()
    assert overridden_away.entity_id not in _carried(batch)


async def test_only_classless_primary_sensors_are_asked_whatever_their_name(hass: HomeAssistant) -> None:
    """Classless sensors with or without a unit are asked; any other class, domain or a telling name changes nothing."""
    asked = [
        register_unit_sensor(hass, "plain_dbm", unit="dBm"),
        register_unit_sensor(hass, "plain_gb", unit="GB"),
        register_unit_sensor(hass, "plain_none", unit=None),
    ]
    never = [
        register_unit_sensor(hass, f"class_{device_class}", unit=None, name="RSSI", original_device_class=device_class)
        for device_class in ("battery", "timestamp", "data_size", "enum", "temperature")
    ]
    never += [
        register_unit_sensor(hass, "binary_plain", unit=None, domain="binary_sensor"),
        register_unit_sensor(hass, "binary_rssi", unit=None, domain="binary_sensor", name="RSSI"),
        register_unit_sensor(hass, "a_switch", unit=None, domain="switch", name="RSSI"),
        register_unit_sensor(hass, "a_tracker", unit=None, domain="device_tracker", name="RSSI"),
    ]

    batch = await HideDiagnosticRecipe(None).async_prepare(hass)

    assert _asked(batch) == {entry.entity_id for entry in asked}
    assert _carried(batch) == set()
    assert not _asked(batch) & {entry.entity_id for entry in never}


async def test_categorised_hidden_disabled_own_and_critical_sensors_are_never_candidates(hass: HomeAssistant) -> None:
    """Only uncategorised, unhidden, enabled sensors outside the critical label qualify; an unrelated label does not matter."""
    lr.async_get(hass).async_create("Critical")
    lr.async_get(hass).async_create("Other")
    kept = register_unit_sensor(hass, "kept", unit=None, labels=frozenset({"other"}))
    excluded = [
        register_unit_sensor(hass, "cfg", unit=None, entity_category=er.EntityCategory.CONFIG),
        register_unit_sensor(hass, "diag", unit=None, entity_category=er.EntityCategory.DIAGNOSTIC),
        register_unit_sensor(hass, "user_hidden", unit=None, hidden_by=er.RegistryEntryHider.USER),
        register_unit_sensor(hass, "integration_hidden", unit=None, hidden_by=er.RegistryEntryHider.INTEGRATION),
        register_unit_sensor(hass, "disabled", unit=None, disabled_by=er.RegistryEntryDisabler.USER),
        register_unit_sensor(hass, "ours", unit=None, platform=DOMAIN),
        register_unit_sensor(hass, "critical_entity", unit=None, labels=frozenset({"critical"})),
        register_unit_sensor(hass, "critical_device", unit=None, device_name="Panel", device_labels=frozenset({"critical"})),
    ]

    batch = await HideDiagnosticRecipe("critical").async_prepare(hass)

    assert _asked(batch) == {kept.entity_id}
    assert not _asked(batch) & {entry.entity_id for entry in excluded}


async def test_code_decided_sensors_never_shift_a_question_index(hass: HomeAssistant) -> None:
    """With signal_strength sensors interleaved in entity_id order, sensors[k] is always the sensor asked as h<k>."""
    register_unit_sensor(hass, "a_sig", unit="dBm", original_device_class="signal_strength")
    plain_b = register_unit_sensor(hass, "b_plain", unit=None, name="Plain B")
    register_unit_sensor(hass, "c_sig", unit="dBm", original_device_class="signal_strength")
    plain_d = register_unit_sensor(hass, "d_plain", unit=None, name="Plain D")

    batch = await HideDiagnosticRecipe(None).async_prepare(hass)

    assert [item["name"] for item in batch.state["sensors"]] == ["Plain B", "Plain D"]
    assert batch.subjects["h0"]["entity_id"] == plain_b.entity_id
    assert batch.subjects["h1"]["entity_id"] == plain_d.entity_id
    assert set(batch.questions) == {"h0", "h1"}
    assert len(_carried(batch)) == 2


async def test_a_run_with_only_signal_strength_sensors_makes_no_request_and_raises_their_cards(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hide_diagnostic_entry: MockConfigEntry
) -> None:
    """Signal-strength sensors are suggested in code: no POST, a card and a count for each."""
    first = register_unit_sensor(hass, "sig_a", unit="dBm", original_device_class="signal_strength")
    second = register_unit_sensor(hass, "sig_b", unit="dBm", device_class="signal_strength")
    hide_diagnostic_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(hide_diagnostic_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert posted_bodies(aioclient_mock) == []
    state = hass.states.get(recipe_sensor_entity_id(hass, hide_diagnostic_entry, RECIPE_HIDE_DIAGNOSTIC))
    assert state is not None
    assert state.state == "2"
    for sensor in (first, second):
        assert _issue(hass, HIDE_DIAGNOSTIC_ISSUE_PREFIX, sensor) is not None


def _open_device_class_card(hass: HomeAssistant, sensor: er.RegistryEntry) -> None:
    """Raise a device class card for this sensor by hand, the way that recipe's own run would."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"{DEVICE_CLASS_ISSUE_PREFIX}{sensor.id}",
        is_fixable=True,
        is_persistent=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key="device_class_suggestion",
    )


async def test_an_open_device_class_card_holds_a_sensor_back_until_it_is_ignored(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hide_diagnostic_entry: MockConfigEntry
) -> None:
    """A sensor with an open device class card is not asked or carried; ignoring that card frees it on the next run."""
    hide_diagnostic_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(hide_diagnostic_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    plain = register_unit_sensor(hass, "plain_pct", unit="%", name="Plain")
    signal = register_unit_sensor(hass, "sig", unit="dBm", original_device_class="signal_strength")
    sync_device_class_cards(hass, SafetyRules(None), [{"registry_id": plain.id, "choice": "battery"}], {"battery": "Battery"})
    _open_device_class_card(hass, signal)

    await press_recipe_run(hass, hide_diagnostic_entry, RECIPE_HIDE_DIAGNOSTIC)

    assert posted_bodies(aioclient_mock) == []
    assert _issue(hass, HIDE_DIAGNOSTIC_ISSUE_PREFIX, plain) is None
    assert _issue(hass, HIDE_DIAGNOSTIC_ISSUE_PREFIX, signal) is None

    for sensor in (plain, signal):
        ir.async_ignore_issue(hass, DOMAIN, f"{DEVICE_CLASS_ISSUE_PREFIX}{sensor.id}", True)
    register_jev_responses_by_question(
        aioclient_mock, {"h0": api_response({"h0": hide_diagnostic_answer(OPTION_DIAGNOSTIC, 0.9)})}
    )
    await press_recipe_run(hass, hide_diagnostic_entry, RECIPE_HIDE_DIAGNOSTIC)

    bodies = posted_bodies(aioclient_mock)
    assert len(bodies) == 1
    assert [item["name"] for item in bodies[0]["state"]["sensors"]] == ["Plain"]
    assert _issue(hass, HIDE_DIAGNOSTIC_ISSUE_PREFIX, plain) is not None
    assert _issue(hass, HIDE_DIAGNOSTIC_ISSUE_PREFIX, signal) is not None


async def test_an_inactive_device_class_card_does_not_hold_a_sensor_back(hass: HomeAssistant) -> None:
    """A card that is not showing in Repairs (inactive, as after a restart before its recipe reruns) is not an open card."""
    sensor = register_unit_sensor(hass, "plain_pct", unit="%", name="Plain")
    _open_device_class_card(hass, sensor)
    assert not qualifies(hass, SafetyRules(None), sensor)

    registry = ir.async_get(hass)
    key = (DOMAIN, f"{DEVICE_CLASS_ISSUE_PREFIX}{sensor.id}")
    registry.issues[key] = replace(registry.issues[key], active=False)

    assert qualifies(hass, SafetyRules(None), sensor)


async def test_an_ignored_hide_card_survives_a_later_open_device_class_card(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hide_diagnostic_entry: MockConfigEntry
) -> None:
    """A rejection is kept even when an open device class card now holds the sensor back from new suggestions."""
    plain = register_unit_sensor(hass, "plain_pct", unit="%", name="Plain")
    register_jev_responses_by_question(
        aioclient_mock, {"h0": api_response({"h0": hide_diagnostic_answer(OPTION_DIAGNOSTIC, 0.9)})}
    )
    hide_diagnostic_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(hide_diagnostic_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    issue_id = f"{HIDE_DIAGNOSTIC_ISSUE_PREFIX}{plain.id}"
    ir.async_ignore_issue(hass, DOMAIN, issue_id, True)
    sync_device_class_cards(hass, SafetyRules(None), [{"registry_id": plain.id, "choice": "battery"}], {"battery": "Battery"})

    await press_recipe_run(hass, hide_diagnostic_entry, RECIPE_HIDE_DIAGNOSTIC)

    assert len(posted_bodies(aioclient_mock)) == 1
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.dismissed_version is not None


async def test_a_sensor_the_device_class_recipe_set_to_signal_strength_is_suggested_with_no_hide_request(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Confirming a signal_strength device class card sends the next hide run a code-decided card and no h-question."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"api_key": "test-key"},
        options={
            CONF_HEALTH_ENABLED: False,
            CONF_UPDATES_ENABLED: False,
            CONF_AREAS_ENABLED: False,
            CONF_CONFIG_ENTRIES_ENABLED: False,
            CONF_CRITICAL_LABEL_ENABLED: False,
            CONF_DEVICE_CLASS_ENABLED: True,
            CONF_HIDE_DIAGNOSTIC_ENABLED: True,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    sensor = register_unit_sensor(hass, "wifi_dbm", unit="dBm", name="Signal")
    answer = area_answer("signal_strength", 0.9, candidate_classes("dBm"))
    register_jev_responses_by_question(aioclient_mock, {"s0": api_response({"s0": answer})})

    await press_recipe_run(hass, entry, RECIPE_DEVICE_CLASS)
    await confirm_suggestion_card(hass, f"{DEVICE_CLASS_ISSUE_PREFIX}{sensor.id}")
    assert _issue(hass, HIDE_DIAGNOSTIC_ISSUE_PREFIX, sensor) is None
    await press_recipe_run(hass, entry, RECIPE_HIDE_DIAGNOSTIC)

    assert _issue(hass, HIDE_DIAGNOSTIC_ISSUE_PREFIX, sensor) is not None
    assert all(set(body["questions"]) == {"s0"} for body in posted_bodies(aioclient_mock))


def _choice(choice: str, confidence: float) -> dict[str, Any]:
    """A choice answer with the given choice, whatever the recipe allows."""
    return {"type": "choice", "choice": choice, "probabilities": {choice: confidence}, "confidence": confidence}


async def test_only_a_confident_diagnostic_answer_raises_a_card(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hide_diagnostic_entry: MockConfigEntry
) -> None:
    """Primary lands in its bucket with no card; low confidence, none of these, off-criteria and malformed answers are unsure."""
    sensors = [register_unit_sensor(hass, f"s{index}", unit=None, name=f"Sensor {index}") for index in range(6)]
    answers = {
        "h0": hide_diagnostic_answer(OPTION_PRIMARY, 0.9),
        "h1": hide_diagnostic_answer(OPTION_DIAGNOSTIC, 0.3),
        "h2": hide_diagnostic_answer(OPTION_NONE, 0.9),
        "h3": _choice("weather", 0.9),
        "h4": "garbage",
        "h5": hide_diagnostic_answer(OPTION_DIAGNOSTIC, 0.9),
    }
    register_jev_responses_by_question(aioclient_mock, {"h0": api_response(answers)})
    hide_diagnostic_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(hide_diagnostic_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    state = hass.states.get(recipe_sensor_entity_id(hass, hide_diagnostic_entry, RECIPE_HIDE_DIAGNOSTIC))
    assert state is not None
    assert state.attributes[ATTR_COUNTS] == {OPTION_SUGGESTED: 1, OPTION_PRIMARY: 1}
    assert len(state.attributes[ATTR_UNSURE]) == 4
    assert [_issue(hass, HIDE_DIAGNOSTIC_ISSUE_PREFIX, sensor) is not None for sensor in sensors] == [
        False,
        False,
        False,
        False,
        False,
        True,
    ]
