"""How the integration's entities attach to the Gut Check device and which events redraw them."""

from __future__ import annotations

import pytest
from homeassistant.const import EVENT_STATE_CHANGED, EVENT_STATE_REPORTED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.device_registry import DeviceEntryType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.client import GutCheckClient
from custom_components.gutcheck.const import (
    DOMAIN,
    HEALTH_ISSUE_PREFIX,
    OPTION_WORTH_FIXING,
    RECIPE_HEALTH,
    UPDATES_ISSUE_PREFIX,
)

from .conftest import (
    api_response,
    choice_answer,
    recipe_sensor_entity_id,
    register_jev_responses,
    register_unavailable_entity,
)


async def _set_up(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry) -> None:
    """Set up the entry with one unavailable entity and one confident answer."""
    register_unavailable_entity(hass)
    register_jev_responses(aioclient_mock, [api_response({"e0": choice_answer(OPTION_WORTH_FIXING, 0.9)})])
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_every_entity_sits_on_the_one_named_service_device(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """The recipe sensor, usage sensors and run button share one device called Gut Check."""
    await _set_up(hass, aioclient_mock, mock_config_entry)
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, mock_config_entry.entry_id), mock_config_entry.entry_id)
    assert device is not None
    assert device.name == "Gut Check"
    assert device.entry_type is DeviceEntryType.SERVICE

    entries = er.async_entries_for_config_entry(er.async_get(hass), mock_config_entry.entry_id)
    assert {entry.domain for entry in entries} == {"sensor", "button"}
    assert {entry.device_id for entry in entries} == {device.id}
    assert {entry.translation_key for entry in entries if entry.unique_id.endswith(RECIPE_HEALTH)} == {RECIPE_HEALTH}
    assert isinstance(mock_config_entry.runtime_data.client, GutCheckClient)


async def test_a_press_during_a_run_names_the_integration_in_its_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """The refusal is translated with this integration's domain, so the UI finds its string."""
    await _set_up(hass, aioclient_mock, mock_config_entry)
    button_id = er.async_get(hass).async_get_entity_id("button", DOMAIN, f"{mock_config_entry.entry_id}_{RECIPE_HEALTH}_run")
    assert button_id is not None
    mock_config_entry.runtime_data.coordinators[RECIPE_HEALTH].running = True

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call("button", "press", {"entity_id": button_id}, blocking=True)
    assert err.value.translation_domain == DOMAIN


async def test_the_sensor_redraws_only_for_its_own_recipes_issues(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """A health card redraws the health sensor; another recipe's card and another domain's issue do not."""
    await _set_up(hass, aioclient_mock, mock_config_entry)
    sensor_id = recipe_sensor_entity_id(hass, mock_config_entry, RECIPE_HEALTH)
    redraws: list[Event] = []

    @callback
    def _note(event: Event) -> None:
        """Count one state write for the health sensor."""
        redraws.append(event)

    for event_type in (EVENT_STATE_CHANGED, EVENT_STATE_REPORTED):
        hass.bus.async_listen(event_type, _note, event_filter=callback(lambda data: data["entity_id"] == sensor_id))

    def _raise(domain: str, issue_id: str) -> None:
        """Raise one issue in the registry."""
        ir.async_create_issue(
            hass, domain, issue_id, is_fixable=False, severity=ir.IssueSeverity.WARNING, translation_key="unavailable_entity"
        )

    _raise(DOMAIN, f"{UPDATES_ISSUE_PREFIX}other")
    _raise("other_domain", f"{HEALTH_ISSUE_PREFIX}foreign")
    await hass.async_block_till_done()
    assert len(redraws) == 0

    _raise(DOMAIN, f"{HEALTH_ISSUE_PREFIX}own")
    await hass.async_block_till_done()
    assert len(redraws) == 1
