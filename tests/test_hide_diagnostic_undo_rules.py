"""Every change-back rule for hidden sensors: only Gut Check's own hide is cleared, both kinds share one step."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import label_registry as lr
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import (
    CONF_CRITICAL_LABEL,
    CONF_DAILY_BUDGET,
    CONF_HIDE_DIAGNOSTIC_ENABLED,
    CONF_UNDO_DEVICE_CLASS,
    CONF_UNDO_HIDDEN_SENSORS,
    CONF_UNDO_SENSORS,
    DEFAULT_DAILY_BUDGET,
    DEVICE_CLASS_APPLIED_STORE_KEY,
    DEVICE_CLASS_ISSUE_PREFIX,
    DOMAIN,
    HIDE_DIAGNOSTIC_APPLIED_STORE_KEY,
    HIDE_DIAGNOSTIC_ISSUE_PREFIX,
    RECIPE_HIDE_DIAGNOSTIC,
)
from custom_components.gutcheck.recipes.device_class_cards import sync_device_class_cards
from custom_components.gutcheck.recipes.safety import SafetyRules
from custom_components.gutcheck.recipes.shapes import recipe_store_key

from .conftest import (
    KEPT_DEVICE_CLASS_OPTIONS,
    api_response,
    confirm_suggestion_card,
    hide_diagnostic_answer,
    register_jev_responses_by_question,
    register_unit_sensor,
    setup_and_confirm_device_class,
    setup_and_confirm_hide,
)

BOTH_ON = {**KEPT_DEVICE_CLASS_OPTIONS, CONF_HIDE_DIAGNOSTIC_ENABLED: True}


def _fields(result: dict) -> set[str]:
    """The field names of a form result's schema."""
    return {str(key) for key in result["data_schema"].schema}


def _hidden_by(hass: HomeAssistant, sensor: er.RegistryEntry) -> er.RegistryEntryHider | None:
    """The sensor's live hidden_by."""
    entry = er.async_get(hass).async_get(sensor.entity_id)
    assert entry is not None
    return entry.hidden_by


def _issue(hass: HomeAssistant, prefix: str, sensor: er.RegistryEntry) -> ir.IssueEntry | None:
    """The card of this kind for this sensor, or None."""
    return ir.async_get(hass).async_get_issue(DOMAIN, f"{prefix}{sensor.id}")


async def _open_step(hass: HomeAssistant, entry: MockConfigEntry, options: dict) -> dict:
    """Tick the change-back checkbox on the init form and return the step's result."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    return await hass.config_entries.options.async_configure(result["flow_id"], {**options, CONF_UNDO_DEVICE_CLASS: True})


async def _change_back(hass: HomeAssistant, entry: MockConfigEntry, options: dict, picks: dict[str, list[str]]) -> None:
    """Open the change-back step, submit the given picks per field, and let the reload settle."""
    result = await _open_step(hass, entry, options)
    assert result["step_id"] == "undo_device_class"
    result = await hass.config_entries.options.async_configure(result["flow_id"], picks)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done(wait_background_tasks=True)


async def _both_kinds(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> tuple[er.RegistryEntry, er.RegistryEntry]:
    """A confirmed device class on one sensor, then a confirmed hide on another, both recipes on; returns (classed, hidden)."""
    classed = register_unit_sensor(hass, "battery_pct", unit="%", name="Battery")
    hidden = register_unit_sensor(hass, "phone_wifi", unit=None, name="Wi-Fi connection")
    await setup_and_confirm_device_class(hass, aioclient_mock, entry, classed)
    aioclient_mock.clear_requests()
    register_jev_responses_by_question(aioclient_mock, {"h0": api_response({"h0": hide_diagnostic_answer("diagnostic", 0.9)})})
    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(result["flow_id"], BOTH_ON)
    await hass.async_block_till_done(wait_background_tasks=True)
    await confirm_suggestion_card(hass, f"{HIDE_DIAGNOSTIC_ISSUE_PREFIX}{hidden.id}")
    return classed, hidden


async def test_one_submit_changes_back_both_kinds(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """The step shows a field per kind; picking both clears the class and the hide, ignores both cards, forgets both records."""
    classed, hidden = await _both_kinds(hass, aioclient_mock, device_class_entry)
    assert device_class_entry.runtime_data.applied.get(classed.id) == "battery"
    assert device_class_entry.runtime_data.applied_hidden.get(hidden.id) == "user"

    result = await _open_step(hass, device_class_entry, BOTH_ON)
    assert _fields(result) == {CONF_UNDO_SENSORS, CONF_UNDO_HIDDEN_SENSORS}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_UNDO_SENSORS: [classed.id], CONF_UNDO_HIDDEN_SENSORS: [hidden.id]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done(wait_background_tasks=True)

    updated = er.async_get(hass).async_get(classed.entity_id)
    assert updated is not None
    assert updated.device_class is None
    assert _hidden_by(hass, hidden) is None
    for prefix, sensor in ((DEVICE_CLASS_ISSUE_PREFIX, classed), (HIDE_DIAGNOSTIC_ISSUE_PREFIX, hidden)):
        card = _issue(hass, prefix, sensor)
        assert card is not None
        assert card.dismissed_version is not None
    assert device_class_entry.runtime_data.applied.get(classed.id) is None
    assert device_class_entry.runtime_data.applied_hidden.get(hidden.id) is None


async def test_the_step_has_no_hidden_field_when_only_device_classes_are_recorded(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """A kind with no records gets no field."""
    sensor = register_unit_sensor(hass, "battery_pct", unit="%", name="Battery")
    await setup_and_confirm_device_class(hass, aioclient_mock, device_class_entry, sensor)

    result = await _open_step(hass, device_class_entry, KEPT_DEVICE_CLASS_OPTIONS)

    assert _fields(result) == {CONF_UNDO_SENSORS}


async def test_the_only_recorded_sensor_removed_after_the_form_saves_without_a_step(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hide_diagnostic_entry: MockConfigEntry
) -> None:
    """Nothing left to pick after the init form rendered: the held options are saved, no empty form."""
    sensor = register_unit_sensor(hass, "phone_wifi", unit=None, name="Wi-Fi connection")
    await setup_and_confirm_hide(hass, aioclient_mock, hide_diagnostic_entry, sensor)
    options = {**hide_diagnostic_entry.options, CONF_DAILY_BUDGET: DEFAULT_DAILY_BUDGET}
    result = await hass.config_entries.options.async_init(hide_diagnostic_entry.entry_id)
    assert CONF_UNDO_DEVICE_CLASS in _fields(result)
    er.async_get(hass).async_remove(sensor.entity_id)

    result = await hass.config_entries.options.async_configure(result["flow_id"], {**options, CONF_UNDO_DEVICE_CLASS: True})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == options


async def test_a_removed_recorded_sensor_is_dropped_on_the_next_save_picked_or_not(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry
) -> None:
    """The removed sensor's field entry is gone, and saving a pick for the other kind drops it from its record."""
    classed, hidden = await _both_kinds(hass, aioclient_mock, device_class_entry)
    result = await hass.config_entries.options.async_init(device_class_entry.entry_id)
    er.async_get(hass).async_remove(hidden.entity_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {**BOTH_ON, CONF_UNDO_DEVICE_CLASS: True})
    assert _fields(result) == {CONF_UNDO_SENSORS}

    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_UNDO_SENSORS: [classed.id]})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done(wait_background_tasks=True)

    assert device_class_entry.runtime_data.applied_hidden.get(hidden.id) is None
    assert device_class_entry.runtime_data.applied_hidden.ids() == ()


async def test_a_sensor_with_an_open_device_class_card_is_cleared_and_still_rejected(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hide_diagnostic_entry: MockConfigEntry
) -> None:
    """An open device class card never stops the rejection being recorded."""
    sensor = register_unit_sensor(hass, "phone_battery", unit="%", name="Phone battery")
    await setup_and_confirm_hide(hass, aioclient_mock, hide_diagnostic_entry, sensor)
    sync_device_class_cards(hass, SafetyRules(None), [{"registry_id": sensor.id, "choice": "battery"}], {"battery": "Battery"})
    device_class_card = _issue(hass, DEVICE_CLASS_ISSUE_PREFIX, sensor)
    assert device_class_card is not None
    assert device_class_card.dismissed_version is None

    # Saving unchanged options: a reload would sweep the open device class card, since that recipe is off.
    options = {**hide_diagnostic_entry.options, CONF_DAILY_BUDGET: DEFAULT_DAILY_BUDGET}
    hass.config_entries.async_update_entry(hide_diagnostic_entry, options=options)
    await _change_back(hass, hide_diagnostic_entry, options, {CONF_UNDO_HIDDEN_SENSORS: [sensor.id]})

    assert _hidden_by(hass, sensor) is None
    card = _issue(hass, HIDE_DIAGNOSTIC_ISSUE_PREFIX, sensor)
    assert card is not None
    assert card.dismissed_version is not None
    still_open = _issue(hass, DEVICE_CLASS_ISSUE_PREFIX, sensor)
    assert still_open is not None
    assert still_open.dismissed_version is None


async def test_a_sensor_labelled_critical_in_the_same_save_is_cleared_and_gets_no_card(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hide_diagnostic_entry: MockConfigEntry
) -> None:
    """The critical label saved with the change-back is the one the rejection checks."""
    label = lr.async_get(hass).async_create("Critical")
    sensor = register_unit_sensor(hass, "phone_wifi", unit=None, name="Wi-Fi connection")
    await setup_and_confirm_hide(hass, aioclient_mock, hide_diagnostic_entry, sensor)
    er.async_get(hass).async_update_entity(sensor.entity_id, labels={label.label_id})
    options = {**hide_diagnostic_entry.options, CONF_CRITICAL_LABEL: label.label_id}

    await _change_back(hass, hide_diagnostic_entry, options, {CONF_UNDO_HIDDEN_SENSORS: [sensor.id]})

    assert _hidden_by(hass, sensor) is None
    assert _issue(hass, HIDE_DIAGNOSTIC_ISSUE_PREFIX, sensor) is None
    assert hide_diagnostic_entry.runtime_data.applied_hidden.get(sensor.id) is None


async def test_the_hide_recorded_still_changes_back_with_the_recipe_switched_off(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hide_diagnostic_entry: MockConfigEntry
) -> None:
    """Switching the recipe off keeps the record loaded, the checkbox shown, and the change-back working."""
    sensor = register_unit_sensor(hass, "phone_wifi", unit=None, name="Wi-Fi connection")
    await setup_and_confirm_hide(hass, aioclient_mock, hide_diagnostic_entry, sensor)
    off = {**hide_diagnostic_entry.options, CONF_HIDE_DIAGNOSTIC_ENABLED: False}
    result = await hass.config_entries.options.async_init(hide_diagnostic_entry.entry_id)
    await hass.config_entries.options.async_configure(result["flow_id"], off)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hide_diagnostic_entry.runtime_data.applied_hidden.get(sensor.id) == "user"
    result = await hass.config_entries.options.async_init(hide_diagnostic_entry.entry_id)
    assert CONF_UNDO_DEVICE_CLASS in _fields(result)

    await _change_back(hass, hide_diagnostic_entry, off, {CONF_UNDO_HIDDEN_SENSORS: [sensor.id]})

    assert _hidden_by(hass, sensor) is None
    assert hide_diagnostic_entry.runtime_data.applied_hidden.get(sensor.id) is None


async def test_both_records_are_written_on_unload_and_deleted_with_the_entry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, device_class_entry: MockConfigEntry, hass_storage: dict
) -> None:
    """Unload writes both Stores; removing the entry deletes them, the recipe's Store and every hide card."""
    classed, hidden = await _both_kinds(hass, aioclient_mock, device_class_entry)
    assert await hass.config_entries.async_unload(device_class_entry.entry_id)
    assert hass_storage[DEVICE_CLASS_APPLIED_STORE_KEY]["data"] == {classed.id: "battery"}
    assert hass_storage[HIDE_DIAGNOSTIC_APPLIED_STORE_KEY]["data"] == {hidden.id: "user"}
    assert await hass.config_entries.async_setup(device_class_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    await _change_back(hass, device_class_entry, BOTH_ON, {CONF_UNDO_HIDDEN_SENSORS: [hidden.id]})
    assert _issue(hass, HIDE_DIAGNOSTIC_ISSUE_PREFIX, hidden) is not None
    assert recipe_store_key(RECIPE_HIDE_DIAGNOSTIC) in hass_storage

    await hass.config_entries.async_remove(device_class_entry.entry_id)
    await hass.async_block_till_done()

    for key in (DEVICE_CLASS_APPLIED_STORE_KEY, HIDE_DIAGNOSTIC_APPLIED_STORE_KEY, recipe_store_key(RECIPE_HIDE_DIAGNOSTIC)):
        assert key not in hass_storage
    assert _issue(hass, HIDE_DIAGNOSTIC_ISSUE_PREFIX, hidden) is None
