"""Tests for update entity selection and the question shape built for each one."""

from __future__ import annotations

from homeassistant.const import STATE_OFF
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.gutcheck.const import DOMAIN
from custom_components.gutcheck.recipes.updates import UpdateRecipe

from .conftest import register_pending_update


async def test_pending_update_entity_is_selected(hass: HomeAssistant) -> None:
    """An update entity in the "on" (pending) state is selected."""
    register_pending_update(hass)

    batch = await UpdateRecipe(critical_label=None).async_prepare(hass)

    assert len(batch.subjects) == 1


async def test_update_entity_in_the_off_state_is_not_selected(hass: HomeAssistant) -> None:
    """An update entity that is not pending (state off) is not selected."""
    entry = register_pending_update(hass)
    hass.states.async_set(entry.entity_id, STATE_OFF)

    batch = await UpdateRecipe(critical_label=None).async_prepare(hass)

    assert batch.subjects == {}


async def test_disabled_update_entity_is_not_selected(hass: HomeAssistant) -> None:
    """A disabled registry entry is not selected, even while pending."""
    register_pending_update(hass, disabled_by=er.RegistryEntryDisabler.USER)

    batch = await UpdateRecipe(critical_label=None).async_prepare(hass)

    assert batch.subjects == {}


async def test_critical_labelled_update_entity_is_not_selected(hass: HomeAssistant) -> None:
    """An update entity carrying the configured critical label is never selected."""
    entry = register_pending_update(hass)
    er.async_get(hass).async_update_entity(entry.entity_id, labels={"critical"})

    batch = await UpdateRecipe(critical_label="critical").async_prepare(hass)

    assert batch.subjects == {}


async def test_critical_labelled_device_excludes_its_update_entity(hass: HomeAssistant) -> None:
    """A critical label on the device excludes its update entity too, not just a labelled entity."""
    config_entry = MockConfigEntry(domain="test")
    config_entry.add_to_hass(hass)
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(config_entry_id=config_entry.entry_id, identifiers={("test", "dev1")})
    device_registry.async_update_device(device.id, labels={"critical"})
    register_pending_update(hass, device_id=device.id)

    batch = await UpdateRecipe(critical_label="critical").async_prepare(hass)

    assert batch.subjects == {}


async def test_gutcheck_own_update_entity_is_not_selected(hass: HomeAssistant) -> None:
    """This integration's own entities are out of every recipe's reach, update included."""
    register_pending_update(hass, platform=DOMAIN)

    batch = await UpdateRecipe(critical_label=None).async_prepare(hass)

    assert batch.subjects == {}


async def test_non_update_entity_is_not_selected(hass: HomeAssistant) -> None:
    """A non-update entity, however it is shaped, is never selected by the update recipe."""
    entry = er.async_get(hass).async_get_or_create("sensor", "test", "unique_sensor")
    hass.states.async_set(entry.entity_id, "on")

    batch = await UpdateRecipe(critical_label=None).async_prepare(hass)

    assert batch.subjects == {}


async def test_every_question_built_is_a_score_question_with_three_criteria(hass: HomeAssistant) -> None:
    """Each built question is a score question with exactly the three ordered levels."""
    register_pending_update(hass)

    batch = await UpdateRecipe(critical_label=None).async_prepare(hass)

    assert len(batch.questions) == 1
    question = next(iter(batch.questions.values()))
    assert question["type"] == "score"
    assert len(question["criteria"]) == 3
