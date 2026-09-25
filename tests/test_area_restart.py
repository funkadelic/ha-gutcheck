"""Timeline test: an unanswered area card kept through an unsure run survives a real restart."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry, flush_store
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import AREA_ISSUE_PREFIX, DOMAIN, RECIPE_AREAS

from .conftest import (
    api_response,
    area_answer,
    areas_sensor_entity_id,
    create_areas,
    posted_bodies,
    register_area_device,
    register_jev_responses,
)


async def _restart(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Unload, reload the issue registry from storage the way HA startup does, then set up again."""
    assert await hass.config_entries.async_unload(entry.entry_id)
    registry = ir.async_get(hass)
    await flush_store(registry._store)
    await ir.async_load(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_an_open_card_kept_through_an_unsure_run_is_still_shown_and_counted_after_a_restart(
    hass: HomeAssistant, freezer: Any, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """Run 1 suggests the device, run 2 is unsure, then a restart restores, and the card stays active and counted."""
    freezer.move_to("2026-01-01T00:00:00-08:00")
    areas = create_areas(hass, "Kitchen", "Garage")
    device = register_area_device(hass, "a", name="Device A", entities=["sensor"])
    register_jev_responses(
        aioclient_mock,
        [
            api_response({"d0": area_answer("Kitchen", 0.9, list(areas))}),
            api_response({"d0": area_answer("Kitchen", 0.2, list(areas))}),
        ],
    )
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    issue_id = f"{AREA_ISSUE_PREFIX}{device.id}"
    before = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert before is not None
    assert before.active

    await mock_config_entry.runtime_data.coordinators[RECIPE_AREAS].async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    sensor_id = areas_sensor_entity_id(hass, mock_config_entry)
    state = hass.states.get(sensor_id)
    assert state is not None
    assert state.attributes["items"]["suggested"] == []
    assert [item["registry_id"] for item in state.attributes["unsure"]] == [device.id]
    assert state.state == "1"

    freezer.move_to("2026-01-04T00:00:00-08:00")
    await _restart(hass, mock_config_entry)

    assert len(posted_bodies(aioclient_mock)) == 2
    after = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert after is not None
    assert after.active
    assert after.dismissed_version is None
    assert after.translation_placeholders == before.translation_placeholders
    assert after.data == before.data
    state = hass.states.get(sensor_id)
    assert state is not None
    assert state.state == "1"
