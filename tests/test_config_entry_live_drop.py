"""Timeline tests: a stuck entry that recovers between runs drops from the published result live."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed, flush_store
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import (
    ATTR_COUNTS,
    ATTR_ITEMS,
    ATTR_LAST_RUN,
    ATTR_UNSURE,
    DOMAIN,
    RECIPE_CONFIG_ENTRIES,
)
from custom_components.gutcheck.recipes.config_entry_const import OPTION_DEAD

from .conftest import posted_bodies, restart_config_entry, triage_sensor_entity_id
from .test_config_entry_restore import TRIAGE_STORE_KEY, _issue_id, _seed_store, _stored_item


def _ids(items: list[dict[str, Any]]) -> set[str]:
    """The entry_id of each item."""
    return {item["entry_id"] for item in items}


async def test_an_unsure_entry_kept_by_restore_drops_from_sensor_and_store_once_it_loads(
    hass: HomeAssistant,
    freezer: Any,
    hass_storage: dict[str, Any],
    aioclient_mock: AiohttpClientMocker,
    triage_entry: MockConfigEntry,
    failing_entry: Any,
) -> None:
    """Loading a not-yet-set-up entry removes it from unsure and the Store, leaving a still-stuck one and last_run alone."""
    freezer.move_to("2026-01-01T00:00:00-08:00")
    recovering = await failing_entry("late_hub", None, title="Late Hub", entry_id="late_entry", setup=False)
    stuck = await failing_entry("stuck_hub", ConfigEntryError("device offline"), title="Stuck Hub", entry_id="stuck_entry")

    now = dt_util.utcnow().isoformat()
    _seed_store(
        hass_storage,
        now,
        {OPTION_DEAD: [_stored_item(stuck.entry_id, integration="stuck_hub", first_seen=now)]},
        unsure=[_stored_item(recovering.entry_id, integration="late_hub", first_seen=now)],
    )

    triage_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(triage_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    sensor_id = triage_sensor_entity_id(hass, triage_entry)
    state = hass.states.get(sensor_id)
    assert state is not None
    assert _ids(state.attributes[ATTR_UNSURE]) == {recovering.entry_id}
    last_run = state.attributes[ATTR_LAST_RUN]

    assert await hass.config_entries.async_setup(recovering.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert recovering.state is ConfigEntryState.LOADED
    state = hass.states.get(sensor_id)
    assert state is not None
    assert state.attributes[ATTR_UNSURE] == []
    assert _ids(state.attributes[ATTR_ITEMS][OPTION_DEAD]) == {stuck.entry_id}
    assert state.attributes[ATTR_COUNTS][OPTION_DEAD] == 1
    assert state.attributes[ATTR_LAST_RUN] == last_run
    assert posted_bodies(aioclient_mock) == []

    coordinator = triage_entry.runtime_data.coordinators[RECIPE_CONFIG_ENTRIES]
    await flush_store(coordinator._store)
    stored = hass_storage[TRIAGE_STORE_KEY]["data"]
    assert stored["unsure"] == []
    assert _ids(stored["items"][OPTION_DEAD]) == {stuck.entry_id}

    # Back to not_loaded is not "recovered", so only the Store write keeps it gone.
    assert await hass.config_entries.async_unload(recovering.entry_id)
    await restart_config_entry(hass, triage_entry)

    state = hass.states.get(sensor_id)
    assert state is not None
    assert state.attributes[ATTR_UNSURE] == []
    assert _ids(state.attributes[ATTR_ITEMS][OPTION_DEAD]) == {stuck.entry_id}
    assert ir.async_get(hass).async_get_issue(DOMAIN, _issue_id(recovering.entry_id)) is None
    assert posted_bodies(aioclient_mock) == []


async def test_a_setup_retry_entry_drops_from_its_bucket_card_and_store_once_a_retry_succeeds(
    hass: HomeAssistant,
    freezer: Any,
    hass_storage: dict[str, Any],
    aioclient_mock: AiohttpClientMocker,
    triage_entry: MockConfigEntry,
    failing_entry: Any,
) -> None:
    """A retry that finally loads the entry empties its bucket, count, card and stored item, with no API call."""
    freezer.move_to("2026-01-01T00:00:00-08:00")
    retrying = await failing_entry(
        "retry_hub", ConfigEntryNotReady("still offline"), None, title="Retry Hub", entry_id="retry_entry"
    )
    assert retrying.state is ConfigEntryState.SETUP_RETRY

    now = dt_util.utcnow().isoformat()
    _seed_store(hass_storage, now, {OPTION_DEAD: [_stored_item(retrying.entry_id, integration="retry_hub", first_seen=now)]})

    triage_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(triage_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    sensor_id = triage_sensor_entity_id(hass, triage_entry)
    registry = ir.async_get(hass)
    state = hass.states.get(sensor_id)
    assert state is not None
    assert _ids(state.attributes[ATTR_ITEMS][OPTION_DEAD]) == {retrying.entry_id}
    assert state.attributes[ATTR_COUNTS][OPTION_DEAD] == 1
    assert registry.async_get_issue(DOMAIN, _issue_id(retrying.entry_id)) is not None

    for seconds in (60, 120):
        if retrying.state is ConfigEntryState.LOADED:
            break
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
        await hass.async_block_till_done(wait_background_tasks=True)

    assert retrying.state is ConfigEntryState.LOADED
    state = hass.states.get(sensor_id)
    assert state is not None
    assert state.attributes[ATTR_ITEMS][OPTION_DEAD] == []
    assert state.attributes[ATTR_COUNTS][OPTION_DEAD] == 0
    assert registry.async_get_issue(DOMAIN, _issue_id(retrying.entry_id)) is None
    assert posted_bodies(aioclient_mock) == []

    await flush_store(triage_entry.runtime_data.coordinators[RECIPE_CONFIG_ENTRIES]._store)
    stored = hass_storage[TRIAGE_STORE_KEY]["data"]
    assert stored["items"][OPTION_DEAD] == []
    assert stored["counts"][OPTION_DEAD] == 0
