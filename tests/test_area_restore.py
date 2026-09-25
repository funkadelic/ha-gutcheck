"""Timeline tests: a free restore reproduces cards, and drops devices that stopped qualifying."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import (
    AREA_ISSUE_PREFIX,
    CONF_AREAS_ENABLED,
    CONF_DAILY_BUDGET,
    DEFAULT_DAILY_BUDGET,
    DOMAIN,
)

from .conftest import (
    api_response,
    area_answer,
    areas_sensor_entity_id,
    create_areas,
    find_areas_sensor,
    posted_bodies,
    register_area_device,
    register_jev_responses,
)


async def test_restart_within_a_week_restores_the_sensor_and_cards_with_no_request(
    hass: HomeAssistant, freezer: Any, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """A restart 3 days after the last run restores the sensor and every card for free, ignore included."""
    freezer.move_to("2026-01-01T00:00:00-08:00")
    areas = create_areas(hass, "Kitchen", "Garage")
    device = register_area_device(hass, "a", name="Device A", entities=["sensor"])
    register_jev_responses(aioclient_mock, [api_response({"d0": area_answer("Kitchen", 0.9, list(areas))})])
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(posted_bodies(aioclient_mock)) == 1

    issue_id = f"{AREA_ISSUE_PREFIX}{device.id}"
    ir.async_ignore_issue(hass, DOMAIN, issue_id, True)

    freezer.move_to("2026-01-04T00:00:00-08:00")
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(posted_bodies(aioclient_mock)) == 1
    state = hass.states.get(areas_sensor_entity_id(hass, mock_config_entry))
    assert state is not None
    assert state.state == "0"
    assert len(state.attributes["items"]["suggested"]) == 1
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.dismissed_version is not None


async def test_restore_drops_a_device_given_an_area_or_disabled_from_every_bucket(
    hass: HomeAssistant, freezer: Any, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """restore filters both suggested and unsure, rewrites their counts, and deletes the dropped card."""
    freezer.move_to("2026-01-01T00:00:00-08:00")
    areas = create_areas(hass, "Kitchen", "Garage")
    placed_by_hand = register_area_device(hass, "x", name="Device X", entities=["sensor"])
    turned_disabled = register_area_device(hass, "y", name="Device Y", entities=["sensor"])
    untouched = register_area_device(hass, "z", name="Device Z", entities=["sensor"])
    confidence_by_id = {placed_by_hand.id: 0.9, turned_disabled.id: 0.2, untouched.id: 0.9}
    ordered = sorted((placed_by_hand, turned_disabled, untouched), key=lambda device: device.id)
    answers = {
        f"d{index}": area_answer("Kitchen", confidence_by_id[device.id], list(areas)) for index, device in enumerate(ordered)
    }
    register_jev_responses(aioclient_mock, [api_response(answers)])
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    dropped_issue_id = f"{AREA_ISSUE_PREFIX}{placed_by_hand.id}"
    kept_issue_id = f"{AREA_ISSUE_PREFIX}{untouched.id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, dropped_issue_id) is not None
    assert ir.async_get(hass).async_get_issue(DOMAIN, kept_issue_id) is not None

    device_registry = dr.async_get(hass)
    device_registry.async_update_device(placed_by_hand.id, area_id=areas["Kitchen"])
    device_registry.async_update_device(turned_disabled.id, disabled_by=dr.DeviceEntryDisabler.USER)

    posted_before = len(posted_bodies(aioclient_mock))
    freezer.move_to("2026-01-04T00:00:00-08:00")
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(posted_bodies(aioclient_mock)) == posted_before
    state = hass.states.get(areas_sensor_entity_id(hass, mock_config_entry))
    assert state is not None
    suggested = state.attributes["items"]["suggested"]
    unsure = state.attributes["unsure"]
    assert {item["registry_id"] for item in suggested} == {untouched.id}
    assert {item["registry_id"] for item in unsure} == set()
    assert state.state == str(len(suggested))
    assert ir.async_get(hass).async_get_issue(DOMAIN, dropped_issue_id) is None
    assert ir.async_get(hass).async_get_issue(DOMAIN, kept_issue_id) is not None


async def test_restore_drops_a_removed_device_and_deletes_its_card(
    hass: HomeAssistant, freezer: Any, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """A device removed from the registry since the run drops out of the stored result on restore too."""
    freezer.move_to("2026-01-01T00:00:00-08:00")
    areas = create_areas(hass, "Kitchen")
    device = register_area_device(hass, "a", name="Device A", entities=["sensor"])
    register_jev_responses(aioclient_mock, [api_response({"d0": area_answer("Kitchen", 0.9, list(areas))})])
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    issue_id = f"{AREA_ISSUE_PREFIX}{device.id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    dr.async_get(hass).async_remove_device(device.id)

    freezer.move_to("2026-01-04T00:00:00-08:00")
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    state = hass.states.get(areas_sensor_entity_id(hass, mock_config_entry))
    assert state is not None
    assert state.attributes["items"]["suggested"] == []
    assert state.state == "0"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_disabling_and_reenabling_within_the_week_restores_the_open_card_and_keeps_the_ignored_one(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """Re-enabling within the cadence window restores for free and brings the open card back."""
    areas = create_areas(hass, "Kitchen")
    open_device = register_area_device(hass, "open", name="Open Plug", entities=["sensor"])
    ignored_device = register_area_device(hass, "ignored", name="Ignored Plug", entities=["sensor"])
    register_jev_responses(
        aioclient_mock,
        [api_response({"d0": area_answer("Kitchen", 0.9, list(areas)), "d1": area_answer("Kitchen", 0.9, list(areas))})],
    )
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    open_issue_id = f"{AREA_ISSUE_PREFIX}{open_device.id}"
    ignored_issue_id = f"{AREA_ISSUE_PREFIX}{ignored_device.id}"
    ir.async_ignore_issue(hass, DOMAIN, ignored_issue_id, True)

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_AREAS_ENABLED: False, CONF_DAILY_BUDGET: DEFAULT_DAILY_BUDGET}
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert find_areas_sensor(hass, mock_config_entry) is None
    assert ir.async_get(hass).async_get_issue(DOMAIN, open_issue_id) is None

    posted_before = len(posted_bodies(aioclient_mock))
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_AREAS_ENABLED: True, CONF_DAILY_BUDGET: DEFAULT_DAILY_BUDGET}
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(posted_bodies(aioclient_mock)) == posted_before
    assert ir.async_get(hass).async_get_issue(DOMAIN, open_issue_id) is not None
    ignored_issue = ir.async_get(hass).async_get_issue(DOMAIN, ignored_issue_id)
    assert ignored_issue is not None
    assert ignored_issue.dismissed_version is not None


async def test_restore_keeps_an_item_whose_area_no_longer_resolves_in_the_sensor_but_raises_no_card(
    hass: HomeAssistant, freezer: Any, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """A renamed or deleted area drops the card on restore but leaves the sensor's own item list alone."""
    freezer.move_to("2026-01-01T00:00:00-08:00")
    areas = create_areas(hass, "Kitchen")
    device = register_area_device(hass, "a", name="Device A", entities=["sensor"])
    register_jev_responses(aioclient_mock, [api_response({"d0": area_answer("Kitchen", 0.9, list(areas))})])
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    issue_id = f"{AREA_ISSUE_PREFIX}{device.id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    ar.async_get(hass).async_delete(areas["Kitchen"])

    freezer.move_to("2026-01-04T00:00:00-08:00")
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    state = hass.states.get(areas_sensor_entity_id(hass, mock_config_entry))
    assert state is not None
    suggested = state.attributes["items"]["suggested"]
    assert len(suggested) == 1
    assert suggested[0]["registry_id"] == device.id
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
