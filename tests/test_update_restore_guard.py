"""A stored possibly-breaking update item missing latest_version must not crash restore."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import OPTION_POSSIBLY_BREAKING, RECIPE_UPDATES, STORE_VERSION
from custom_components.gutcheck.recipes.shapes import recipe_store_key

from .conftest import api_response, posted_bodies, register_jev_responses, register_pending_update, score_answer

UPDATES_STORE_KEY = recipe_store_key(RECIPE_UPDATES)
RECENT_RUN = (dt_util.utcnow() - timedelta(days=1)).isoformat()


async def test_a_stored_possibly_breaking_item_missing_latest_version_is_treated_as_never_run(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A half-written possibly-breaking entry must not take the config entry down with it.

    UpdateIssueTracker.sync reads item["latest_version"] to build the Repairs
    card placeholder, so a stored item missing it used to raise KeyError out
    of restore, before latest_version joined stored_item_keys.
    """
    hass.set_state(CoreState.not_running)
    entry = register_pending_update(hass)
    hass_storage[UPDATES_STORE_KEY] = {
        "version": STORE_VERSION,
        "minor_version": 1,
        "key": UPDATES_STORE_KEY,
        "data": {
            "last_run": RECENT_RUN,
            "counts": {OPTION_POSSIBLY_BREAKING: 1},
            "items": {OPTION_POSSIBLY_BREAKING: [{"entity_id": entry.entity_id, "registry_id": entry.id}]},
            "unsure": [],
            "last_payload": None,
        },
    }
    register_jev_responses(aioclient_mock, [api_response({"u0": score_answer(2, 0.9)})])
    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(posted_bodies(aioclient_mock)) == 1
