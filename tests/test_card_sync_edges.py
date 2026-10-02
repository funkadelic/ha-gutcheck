"""Card sync edges: sweeping an attempted subject, what counts as kept, held-back restores and card text."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import label_registry as lr

from custom_components.gutcheck.const import (
    CRITICAL_LABEL_ISSUE_PREFIX,
    DEVICE_CLASS_ISSUE_PREFIX,
    DOMAIN,
    HIDE_DIAGNOSTIC_ISSUE_PREFIX,
    ISSUE_DEVICE_CLASS_SUGGESTION,
    ISSUE_HIDE_DIAGNOSTIC_SUGGESTION,
    ITEM_HELD_BACK,
    OPTION_SUGGESTED,
)
from custom_components.gutcheck.recipes.critical_label import CriticalLabelRecipe
from custom_components.gutcheck.recipes.critical_label_cards import sync_critical_label_cards
from custom_components.gutcheck.recipes.device_class import DeviceClassRecipe
from custom_components.gutcheck.recipes.device_class_cards import reject_suggestion as reject_class
from custom_components.gutcheck.recipes.device_class_cards import sync_device_class_cards
from custom_components.gutcheck.recipes.hide_diagnostic import HideDiagnosticRecipe
from custom_components.gutcheck.recipes.hide_diagnostic_cards import reject_suggestion as reject_hide
from custom_components.gutcheck.recipes.hide_diagnostic_cards import sync_hide_diagnostic_cards
from custom_components.gutcheck.recipes.hide_diagnostic_const import MAX_NEW_HIDE_DIAGNOSTIC_CARDS_PER_RUN
from custom_components.gutcheck.recipes.safety import SafetyRules
from custom_components.gutcheck.recipes.suggestion_cards import sync_suggestion_cards
from custom_components.gutcheck.repairs import async_create_ignored_issue

from .conftest import health_result, register_unit_sensor

KEY = "area_suggestion"


def _ignored(hass: HomeAssistant, issue_id: str) -> None:
    """Raise one fixable issue and ignore it, the way a rejected card is left."""
    async_create_ignored_issue(hass, issue_id, KEY, {}, {"k": "v"})


def _issues(hass: HomeAssistant, prefix: str) -> list[str]:
    """Every Gut Check issue id under prefix."""
    return sorted(i for d, i in ir.async_get(hass).issues if d == DOMAIN and i.startswith(prefix))


async def test_an_ignored_card_for_a_subject_this_run_named_is_swept_when_it_no_longer_resolves(hass: HomeAssistant) -> None:
    """The run still answered about the subject, so the old ignore is stale rather than a rejection to keep."""
    _ignored(hass, "pfx_a")
    _ignored(hass, "pfx_b")

    sync_suggestion_cards(hass, "pfx_", KEY, 10, {}, [{"registry_id": "a"}], lambda _issue_id: True)

    assert _issues(hass, "pfx_") == ["pfx_b"]


async def test_only_cards_under_the_recipes_own_prefix_count_as_kept(hass: HomeAssistant, caplog) -> None:
    """An ignored issue under another prefix, or another domain, is not this sync's to keep."""
    caplog.set_level(logging.DEBUG, logger="custom_components.gutcheck.recipes.suggestion_cards")
    _ignored(hass, "other_x")
    ir.async_create_issue(hass, "elsewhere", "pfx_y", is_fixable=False, severity=ir.IssueSeverity.WARNING, translation_key=KEY)
    ir.async_ignore_issue(hass, "elsewhere", "pfx_y", True)

    sync_suggestion_cards(hass, "pfx_", KEY, 10, {}, [], lambda _issue_id: True)

    assert "kept=0" in caplog.text


async def test_with_no_label_configured_critical_label_suggestions_are_ignored_and_ignores_survive(hass: HomeAssistant) -> None:
    """No label means no cards and nothing swept: an ignored card for a qualifying entity stays, even if suggested."""
    valve = register_unit_sensor(hass, "valve", unit=None, domain="valve", name="Valve")
    issue_id = f"{CRITICAL_LABEL_ISSUE_PREFIX}{valve.id}"
    _ignored(hass, issue_id)

    sync_critical_label_cards(hass, SafetyRules(None), [{"registry_id": valve.id, "confidence": 0.9}])

    assert _issues(hass, CRITICAL_LABEL_ISSUE_PREFIX) == [issue_id]


async def test_a_sensor_with_an_open_device_class_card_gets_no_hide_card(hass: HomeAssistant) -> None:
    """While the class card is open the sensor is held back from a hide suggestion."""
    sensor = register_unit_sensor(hass, "wifi", unit=None, name="Wi-Fi")
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"{DEVICE_CLASS_ISSUE_PREFIX}{sensor.id}",
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_DEVICE_CLASS_SUGGESTION,
    )

    sync_hide_diagnostic_cards(hass, SafetyRules(None), [{"registry_id": sensor.id, "confidence": 0.9}])

    assert _issues(hass, HIDE_DIAGNOSTIC_ISSUE_PREFIX) == []


async def test_a_restore_raises_every_card_the_run_raised_beyond_one_runs_cap(hass: HomeAssistant) -> None:
    """Restored findings are not cut to the per-run cap, and the one the run held back stays held back."""
    count = MAX_NEW_HIDE_DIAGNOSTIC_CARDS_PER_RUN + 2
    sensors = [register_unit_sensor(hass, f"s{i:02d}", unit=None, name=f"Sensor {i}") for i in range(count)]
    items = [{"registry_id": sensor.id, "entity_id": sensor.entity_id, "confidence": 0.9} for sensor in sensors]
    items[-1][ITEM_HELD_BACK] = True
    result = health_result({OPTION_SUGGESTED: items})

    await HideDiagnosticRecipe(None).restore(hass, result)

    assert len(_issues(hass, HIDE_DIAGNOSTIC_ISSUE_PREFIX)) == count - 1


async def test_recipes_act_and_restore_on_a_result_without_the_suggested_bucket(hass: HomeAssistant) -> None:
    """An empty items map syncs an empty set for every card recipe, with no error."""
    label = lr.async_get(hass).async_create("Critical")
    for recipe in (HideDiagnosticRecipe(None), CriticalLabelRecipe(label.label_id), DeviceClassRecipe(None)):
        await recipe.async_act(hass, health_result({}))
        await recipe.restore(hass, health_result({}))


async def test_a_rejection_card_carries_its_text_key_and_data(hass: HomeAssistant) -> None:
    """The ignored card left by a change-back is a real suggestion card with its fix data."""
    classed = register_unit_sensor(hass, "battery", unit="%", name="Battery")
    hidden = register_unit_sensor(hass, "wifi", unit=None, name="Wi-Fi")

    reject_class(hass, SafetyRules(None), {}, classed.id, "battery")
    reject_hide(hass, SafetyRules(None), hidden.id)

    registry = ir.async_get(hass)
    class_card = registry.async_get_issue(DOMAIN, f"{DEVICE_CLASS_ISSUE_PREFIX}{classed.id}")
    hide_card = registry.async_get_issue(DOMAIN, f"{HIDE_DIAGNOSTIC_ISSUE_PREFIX}{hidden.id}")
    assert class_card is not None
    assert class_card.translation_key == ISSUE_DEVICE_CLASS_SUGGESTION
    assert class_card.data == {"registry_id": classed.id, "device_class": "battery"}
    assert hide_card is not None
    assert hide_card.translation_key == ISSUE_HIDE_DIAGNOSTIC_SUGGESTION
    assert hide_card.data == {"registry_id": hidden.id}


async def test_a_device_class_card_carries_the_suggestion_text_key(hass: HomeAssistant) -> None:
    """A run's card is raised under the device class suggestion key."""
    sensor = register_unit_sensor(hass, "battery", unit="%", name="Battery")

    sync_device_class_cards(
        hass, SafetyRules(None), [{"registry_id": sensor.id, "choice": "battery", "confidence": 0.9}], {"battery": "Battery"}
    )

    card = ir.async_get(hass).async_get_issue(DOMAIN, f"{DEVICE_CLASS_ISSUE_PREFIX}{sensor.id}")
    assert card is not None
    assert card.translation_key == ISSUE_DEVICE_CLASS_SUGGESTION
