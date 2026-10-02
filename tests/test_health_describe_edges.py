"""Describing an unavailable entity: sibling availability and the day count behind the leftover rule."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.const import ATTR_RESTORED, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.gutcheck.recipes.health_const import HEALTH_LEFTOVER_DAYS
from custom_components.gutcheck.recipes.health_describe import describe

from .conftest import register_area_device


def _device_entries(hass: HomeAssistant, count: int) -> list[er.RegistryEntry]:
    """The entities of one fresh device with count plain sensors, ordered by entity id."""
    device = register_area_device(hass, "sib", entities=["sensor"] * count)
    return sorted(er.async_entries_for_device(er.async_get(hass), device.id), key=lambda entry: entry.entity_id)


def _siblings_available(hass: HomeAssistant, target: er.RegistryEntry) -> Any:
    """The device_other_entities_available field describe reports for the target."""
    state = hass.states.get(target.entity_id)
    assert state is not None
    return describe(hass, er.async_get(hass), target, state, None)[0]["device_other_entities_available"]


async def test_a_sibling_that_reports_makes_the_device_look_alive(hass: HomeAssistant) -> None:
    """One available sibling is enough, whatever the others say."""
    target, available, down = _device_entries(hass, 3)
    hass.states.async_set(target.entity_id, STATE_UNAVAILABLE)
    hass.states.async_set(available.entity_id, STATE_ON)
    hass.states.async_set(down.entity_id, STATE_UNAVAILABLE)

    assert _siblings_available(hass, target) is True


async def test_siblings_that_are_unavailable_or_have_no_state_do_not(hass: HomeAssistant) -> None:
    """An unavailable sibling and a sibling that never wrote a state both read as not reporting."""
    target, down, silent = _device_entries(hass, 3)
    hass.states.async_set(target.entity_id, STATE_UNAVAILABLE)
    hass.states.async_set(down.entity_id, STATE_UNAVAILABLE)
    assert hass.states.get(silent.entity_id) is None

    assert _siblings_available(hass, target) is False


async def test_a_lone_entity_on_its_device_has_no_available_sibling(hass: HomeAssistant) -> None:
    """With only itself on the device, nothing else is reporting."""
    (target,) = _device_entries(hass, 1)
    hass.states.async_set(target.entity_id, STATE_UNAVAILABLE)

    assert _siblings_available(hass, target) is False


async def test_a_restored_outage_of_exactly_the_leftover_age_is_a_leftover(hass: HomeAssistant, freezer) -> None:
    """Dated by the recorder to exactly HEALTH_LEFTOVER_DAYS ago, a restored entity is decided in code."""
    freezer.move_to("2026-03-01T00:00:00+00:00")
    entry = er.async_get(hass).async_get_or_create("sensor", "test", "gone")
    hass.states.async_set(entry.entity_id, STATE_UNAVAILABLE, {ATTR_RESTORED: True})
    state = hass.states.get(entry.entity_id)
    assert state is not None
    since = dt_util.utcnow() - timedelta(days=HEALTH_LEFTOVER_DAYS)

    _item, subject, leftover = describe(
        hass, er.async_get(hass), entry, state, (HEALTH_LEFTOVER_DAYS * 2, {entry.entity_id: since})
    )

    assert leftover is True
    assert subject["unavailable_for"] == "more than 4 weeks"
