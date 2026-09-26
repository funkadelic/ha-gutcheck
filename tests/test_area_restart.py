"""Timeline tests: area cards across a real restart, and a restore raises only the run's own cards."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import (
    AREA_ISSUE_PREFIX,
    CONF_AREAS_ENABLED,
    CONF_DAILY_BUDGET,
    DEFAULT_DAILY_BUDGET,
    DOMAIN,
    RECIPE_AREAS,
)
from custom_components.gutcheck.recipes.area_const import MAX_NEW_AREA_CARDS_PER_RUN

from .conftest import (
    api_response,
    area_answer,
    create_areas,
    posted_bodies,
    recipe_sensor_entity_id,
    register_area_device,
    register_jev_responses,
    restart_config_entry,
)


async def test_an_unsure_run_clears_the_open_card_and_keeps_the_ignored_one_across_a_restart(
    hass: HomeAssistant, freezer: Any, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """Run 1 suggests both devices, run 2 is unsure for both: the open card clears, the ignored one survives a restart."""
    freezer.move_to("2026-01-01T00:00:00-08:00")
    areas = create_areas(hass, "Kitchen", "Garage")
    device_a = register_area_device(hass, "a", name="Device A", entities=["sensor"])
    device_b = register_area_device(hass, "b", name="Device B", entities=["sensor"])
    run1_answers = {f"d{index}": area_answer("Kitchen", 0.9, list(areas)) for index in range(2)}
    run2_answers = {f"d{index}": area_answer("Kitchen", 0.2, list(areas)) for index in range(2)}
    register_jev_responses(aioclient_mock, [api_response(run1_answers), api_response(run2_answers)])
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    issue_id_a = f"{AREA_ISSUE_PREFIX}{device_a.id}"
    issue_id_b = f"{AREA_ISSUE_PREFIX}{device_b.id}"
    for issue_id in (issue_id_a, issue_id_b):
        issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
        assert issue is not None
        assert issue.active

    ir.async_ignore_issue(hass, DOMAIN, issue_id_b, True)
    before_b = ir.async_get(hass).async_get_issue(DOMAIN, issue_id_b)
    assert before_b is not None

    await mock_config_entry.runtime_data.coordinators[RECIPE_AREAS].async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)

    sensor_id = recipe_sensor_entity_id(hass, mock_config_entry, RECIPE_AREAS)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id_a) is None
    after_run2_b = ir.async_get(hass).async_get_issue(DOMAIN, issue_id_b)
    assert after_run2_b is not None
    assert after_run2_b.dismissed_version is not None
    assert after_run2_b.translation_placeholders == before_b.translation_placeholders
    assert after_run2_b.data == before_b.data
    state = hass.states.get(sensor_id)
    assert state is not None
    assert state.attributes["items"]["suggested"] == []
    assert {item["registry_id"] for item in state.attributes["unsure"]} == {device_a.id, device_b.id}
    assert state.state == "0"

    freezer.move_to("2026-01-04T00:00:00-08:00")
    await restart_config_entry(hass, mock_config_entry)

    assert len(posted_bodies(aioclient_mock)) == 2
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id_a) is None
    after_restart_b = ir.async_get(hass).async_get_issue(DOMAIN, issue_id_b)
    assert after_restart_b is not None
    assert after_restart_b.dismissed_version is not None
    assert after_restart_b.translation_placeholders == before_b.translation_placeholders
    assert after_restart_b.data == before_b.data
    state = hass.states.get(sensor_id)
    assert state is not None
    assert state.state == "0"


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
    await restart_config_entry(hass, mock_config_entry)
    assert _area_card_ids(hass) == run_cards

    await mock_config_entry.runtime_data.coordinators[RECIPE_AREAS].async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(_area_card_ids(hass)) == 12

    await _switch_areas(hass, mock_config_entry, False)
    assert _area_card_ids(hass) == set()
    await _switch_areas(hass, mock_config_entry, True)
    assert len(_area_card_ids(hass)) == 12
    assert len(posted_bodies(aioclient_mock)) == 2
