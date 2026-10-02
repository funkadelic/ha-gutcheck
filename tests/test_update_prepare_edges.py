"""Update recipe selection and notes fetching: entities with no state, the carried count, and the fetch guards."""

from __future__ import annotations

import logging

from homeassistant.components.update import UpdateEntityFeature
from homeassistant.core import HomeAssistant

from custom_components.gutcheck.recipes.update_describe import async_fetch_notes
from custom_components.gutcheck.recipes.updates import UpdateRecipe

from .conftest import FakeUpdateEntity, install_update_entities, register_pending_update


async def test_an_update_entity_with_no_state_is_set_aside_not_a_crash(hass: HomeAssistant) -> None:
    """A registered update entity that has not written a state yet is neither asked about nor an error."""
    entry = register_pending_update(hass, "no_state")
    hass.states.async_remove(entry.entity_id)
    recipe = UpdateRecipe(critical_label=None)

    batch = await recipe.async_prepare(hass)

    assert batch.subjects == {}
    assert recipe._select(hass) == ([], [entry.id])


async def test_the_run_log_counts_what_carried_forward(hass: HomeAssistant, caplog) -> None:
    """With a prior accepted answer for an unchanged update, the log reports one carried and none asked."""
    caplog.set_level(logging.DEBUG, logger="custom_components.gutcheck.recipes.updates")
    register_pending_update(hass, "steady")
    recipe = UpdateRecipe(critical_label=None)
    first = await recipe.async_prepare(hass)
    registry_id = next(iter(first.subjects.values()))["registry_id"]
    previous = {
        "last_run": "",
        "counts": {},
        "items": {
            "routine": [
                {"registry_id": registry_id, "installed_version": "1.0.0", "latest_version": "2.0.0", "skipped_version": None}
            ]
        },
        "unsure": [],
        "last_payload": None,
    }

    await recipe.async_prepare(hass, previous)

    assert "asked=0 carried=1" in caplog.text


async def test_release_notes_are_fetched_only_from_an_available_entity_that_supports_them(hass: HomeAssistant) -> None:
    """A missing entity, an unavailable one and one without the feature each give None without a fetch."""
    absent = register_pending_update(hass, "absent")
    down = register_pending_update(hass, "down")
    plain = register_pending_update(hass, "plain")
    ready = register_pending_update(hass, "ready")
    entities = {
        down.entity_id: FakeUpdateEntity(available=False, notes="down notes"),
        plain.entity_id: FakeUpdateEntity(supported_features=UpdateEntityFeature(0), notes="plain notes"),
        ready.entity_id: FakeUpdateEntity(notes="ready notes"),
    }
    install_update_entities(hass, entities)
    pairs = [(entry, hass.states.get(entry.entity_id)) for entry in (absent, down, plain, ready)]

    notes = await async_fetch_notes(hass, pairs)  # type: ignore[arg-type]

    assert notes == [None, None, None, "ready notes"]
    assert [entities[entry.entity_id].fetches for entry in (down, plain, ready)] == [0, 0, 1]
