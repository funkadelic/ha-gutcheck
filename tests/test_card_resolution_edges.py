"""Resolving suggestions into cards: skipped ones do not stop the rest, and each card carries its own subject and names."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import label_registry as lr

from custom_components.gutcheck.const import (
    CRITICAL_LABEL_ISSUE_PREFIX,
    DEVICE_CLASS_ISSUE_PREFIX,
    HIDE_DIAGNOSTIC_ISSUE_PREFIX,
)
from custom_components.gutcheck.recipes.critical_label_cards import _resolve as resolve_critical
from custom_components.gutcheck.recipes.device_class_cards import _resolve as resolve_device_class
from custom_components.gutcheck.recipes.hide_diagnostic_cards import _resolve as resolve_hide
from custom_components.gutcheck.recipes.safety import SafetyRules

from .conftest import register_unit_sensor


async def test_critical_label_resolution_skips_a_gone_entity_and_keeps_the_next(hass: HomeAssistant) -> None:
    """A removed entity is passed over; the card for the next one carries that entity's own id as subject."""
    label = lr.async_get(hass).async_create("Critical")
    valve = register_unit_sensor(hass, "valve", unit=None, domain="valve", name="Valve")

    resolved = resolve_critical(hass, SafetyRules(label.label_id), label, [{"registry_id": "gone"}, {"registry_id": valve.id}])

    assert list(resolved) == [f"{CRITICAL_LABEL_ISSUE_PREFIX}{valve.id}"]
    assert resolved[f"{CRITICAL_LABEL_ISSUE_PREFIX}{valve.id}"].subject_id == valve.id


async def test_hide_resolution_skips_a_gone_sensor_and_keeps_the_next(hass: HomeAssistant) -> None:
    """A removed sensor is passed over; the next card's subject is that sensor's registry id."""
    sensor = register_unit_sensor(hass, "wifi", unit=None, name="Wi-Fi")

    resolved = resolve_hide(hass, SafetyRules(None), [{"registry_id": "gone"}, {"registry_id": sensor.id}])

    assert list(resolved) == [f"{HIDE_DIAGNOSTIC_ISSUE_PREFIX}{sensor.id}"]
    assert resolved[f"{HIDE_DIAGNOSTIC_ISSUE_PREFIX}{sensor.id}"].subject_id == sensor.id


async def test_device_class_resolution_skips_gone_sensors_and_unfitting_classes(hass: HomeAssistant) -> None:
    """A removed sensor and a class the unit does not fit are passed over; a name missing from the map falls back to the class."""
    sensor = register_unit_sensor(hass, "battery", unit="%", name="Battery")
    suggested = [
        {"registry_id": "gone", "choice": "battery"},
        {"registry_id": sensor.id, "choice": "temperature"},
        {"registry_id": sensor.id, "choice": "battery"},
    ]

    resolved = resolve_device_class(hass, SafetyRules(None), {}, suggested)

    assert list(resolved) == [f"{DEVICE_CLASS_ISSUE_PREFIX}{sensor.id}"]
    card = resolved[f"{DEVICE_CLASS_ISSUE_PREFIX}{sensor.id}"]
    assert card.placeholders["class_name"] == "battery"
    assert card.subject_id == sensor.id
