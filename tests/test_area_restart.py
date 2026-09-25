"""Timeline tests: area cards across a real restart, and a restore raises only the run's own cards."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry, flush_store
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import (
    AREA_ISSUE_PREFIX,
    CONF_AREAS_ENABLED,
    CONF_DAILY_BUDGET,
    DEFAULT_DAILY_BUDGET,
    DOMAIN,
    MAX_NEW_AREA_CARDS_PER_RUN,
    RECIPE_AREAS,
)

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


def _area_card_ids(hass: HomeAssistant) -> set[str]:
    """Every area card in the issue registry."""
    return {
        issue_id for domain, issue_id in ir.async_get(hass).issues if domain == DOMAIN and issue_id.startswith(AREA_ISSUE_PREFIX)
    }


async def _switch_areas(hass: HomeAssistant, entry: MockConfigEntry, enabled: bool) -> None:
    """Turn area suggestions on or off through the options flow."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_AREAS_ENABLED: enabled, CONF_DAILY_BUDGET: DEFAULT_DAILY_BUDGET}
    )
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_a_restore_raises_no_card_the_run_held_back_and_every_card_the_run_raised(
    hass: HomeAssistant, freezer: Any, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """A restart adds none of the two held back; once run 2 opens all twelve, switching off and on brings all twelve back."""
    freezer.move_to("2026-01-01T00:00:00-08:00")
    create_areas(hass, "Kitchen")
    for index in range(12):
        register_area_device(hass, f"d{index}", name=f"Device {index}", entities=["sensor"])
    answers = api_response({f"d{index}": area_answer("Kitchen", 0.9, ["Kitchen"]) for index in range(12)})
    register_jev_responses(aioclient_mock, [answers, answers])
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    run_cards = _area_card_ids(hass)
    assert len(run_cards) == MAX_NEW_AREA_CARDS_PER_RUN

    freezer.move_to("2026-01-02T00:00:00-08:00")
    await _restart(hass, mock_config_entry)
    assert _area_card_ids(hass) == run_cards

    await mock_config_entry.runtime_data.coordinators[RECIPE_AREAS].async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(_area_card_ids(hass)) == 12

    await _switch_areas(hass, mock_config_entry, False)
    assert _area_card_ids(hass) == set()
    await _switch_areas(hass, mock_config_entry, True)
    assert len(_area_card_ids(hass)) == 12
    assert len(posted_bodies(aioclient_mock)) == 2
