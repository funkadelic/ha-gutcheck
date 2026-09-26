"""Timeline test: switching critical label suggestions on and off, running on demand, and removing the entry."""

from __future__ import annotations

from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import label_registry as lr
from homeassistant.helpers.storage import Store
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import (
    CONF_AREAS_ENABLED,
    CONF_CONFIG_ENTRIES_ENABLED,
    CONF_CRITICAL_LABEL,
    CONF_CRITICAL_LABEL_ENABLED,
    CONF_DAILY_BUDGET,
    CONF_DEVICE_CLASS_ENABLED,
    CONF_HEALTH_ENABLED,
    CONF_UPDATES_ENABLED,
    CRITICAL_LABEL_ISSUE_PREFIX,
    DEFAULT_DAILY_BUDGET,
    DOMAIN,
    RECIPE_CRITICAL_LABEL,
    STORE_VERSION,
)
from custom_components.gutcheck.recipes.shapes import recipe_store_key

from .conftest import (
    api_response,
    critical_label_answer,
    find_recipe_sensor,
    posted_bodies,
    press_recipe_run,
    register_jev_responses,
    register_unit_sensor,
)

_OFF_OPTIONS = {
    CONF_HEALTH_ENABLED: False,
    CONF_UPDATES_ENABLED: False,
    CONF_AREAS_ENABLED: False,
    CONF_DEVICE_CLASS_ENABLED: False,
    CONF_CONFIG_ENTRIES_ENABLED: False,
    CONF_CRITICAL_LABEL_ENABLED: False,
    CONF_DAILY_BUDGET: DEFAULT_DAILY_BUDGET,
}


def _critical_label_button_entity_id(hass: HomeAssistant, entry: MockConfigEntry) -> str | None:
    """The critical label recipe's Run button entity id, or None if it was not created."""
    return er.async_get(hass).async_get_entity_id("button", DOMAIN, f"{entry.entry_id}_{RECIPE_CRITICAL_LABEL}_run")


def _critical_label_issue_ids(hass: HomeAssistant) -> set[str]:
    """Every critical label suggestion issue id currently in the registry."""
    return {
        issue_id
        for domain, issue_id in ir.async_get(hass).issues
        if domain == DOMAIN and issue_id.startswith(CRITICAL_LABEL_ISSUE_PREFIX)
    }


async def _ignore_suggestion_card(hass: HomeAssistant, issue_id: str) -> None:
    """Ignore a confirm/ignore suggestion card through Home Assistant's own repairs flow manager."""
    assert await async_setup_component(hass, "repairs", {})
    manager = repairs_flow_manager(hass)
    assert manager is not None
    result = await manager.async_init(DOMAIN, data={"issue_id": issue_id})
    assert result["type"] is FlowResultType.MENU
    result = await manager.async_configure(result["flow_id"], {"next_step_id": "ignore"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "suggestion_ignored"


async def test_off_by_default_then_disable_reenable_run_and_removal_timeline(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """Off by default; enabling raises cards; disable keeps an ignored one; re-enable, run and removal follow the pattern."""
    label = lr.async_get(hass).async_create("Critical")
    valve_a = register_unit_sensor(hass, "valve_a", unit=None, name="Valve A", domain="valve")
    valve_b = register_unit_sensor(hass, "valve_b", unit=None, name="Valve B", domain="valve")
    ordered = sorted((valve_a, valve_b), key=lambda entry: entry.entity_id)
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert find_recipe_sensor(hass, mock_config_entry, RECIPE_CRITICAL_LABEL) is None
    assert _critical_label_button_entity_id(hass, mock_config_entry) is None
    assert _critical_label_issue_ids(hass) == set()
    assert posted_bodies(aioclient_mock) == []

    both_confident = api_response({f"k{index}": critical_label_answer("critical", 0.9) for index in range(2)})
    register_jev_responses(aioclient_mock, [both_confident])
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {**_OFF_OPTIONS, CONF_CRITICAL_LABEL_ENABLED: True, CONF_CRITICAL_LABEL: label.label_id}
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    sensor_entity_id = find_recipe_sensor(hass, mock_config_entry, RECIPE_CRITICAL_LABEL)
    assert sensor_entity_id is not None
    open_issue_id = f"{CRITICAL_LABEL_ISSUE_PREFIX}{ordered[0].id}"
    ignored_issue_id = f"{CRITICAL_LABEL_ISSUE_PREFIX}{ordered[1].id}"
    assert _critical_label_issue_ids(hass) == {open_issue_id, ignored_issue_id}
    sensor_state = hass.states.get(sensor_entity_id)
    assert sensor_state is not None
    assert sensor_state.state == "2"

    await _ignore_suggestion_card(hass, ignored_issue_id)
    posted_before = len(posted_bodies(aioclient_mock))

    # Disable: sensor and button gone, the open card gone, the ignored one stays ignored, nothing is posted.
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    await hass.config_entries.options.async_configure(result["flow_id"], {**_OFF_OPTIONS, CONF_CRITICAL_LABEL: label.label_id})
    await hass.async_block_till_done(wait_background_tasks=True)

    assert find_recipe_sensor(hass, mock_config_entry, RECIPE_CRITICAL_LABEL) is None
    assert _critical_label_button_entity_id(hass, mock_config_entry) is None
    assert _critical_label_issue_ids(hass) == {ignored_issue_id}
    ignored_issue = ir.async_get(hass).async_get_issue(DOMAIN, ignored_issue_id)
    assert ignored_issue is not None
    assert ignored_issue.dismissed_version is not None
    assert len(posted_bodies(aioclient_mock)) == posted_before

    # Re-enable within the cadence window: restored from the Store, no POST, the open card is back, the ignore survives.
    posted_before = len(posted_bodies(aioclient_mock))
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {**_OFF_OPTIONS, CONF_CRITICAL_LABEL_ENABLED: True, CONF_CRITICAL_LABEL: label.label_id}
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert find_recipe_sensor(hass, mock_config_entry, RECIPE_CRITICAL_LABEL) is not None
    assert len(posted_bodies(aioclient_mock)) == posted_before
    assert _critical_label_issue_ids(hass) == {open_issue_id, ignored_issue_id}
    sensor_state = hass.states.get(sensor_entity_id)
    assert sensor_state is not None
    assert sensor_state.state == "1"
    ignored_issue = ir.async_get(hass).async_get_issue(DOMAIN, ignored_issue_id)
    assert ignored_issue is not None
    assert ignored_issue.dismissed_version is not None

    # Pressing Run critical label suggestions posts one request, first question id k0.
    button_entity_id = _critical_label_button_entity_id(hass, mock_config_entry)
    assert button_entity_id is not None
    aioclient_mock.clear_requests()
    register_jev_responses(aioclient_mock, [both_confident])
    await press_recipe_run(hass, mock_config_entry, RECIPE_CRITICAL_LABEL)

    bodies = posted_bodies(aioclient_mock)
    assert len(bodies) == 1
    assert next(iter(bodies[0]["questions"])) == "k0"

    # Removing the entry deletes every critical label card, the ignored one included, and its Store.
    await hass.config_entries.async_remove(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert _critical_label_issue_ids(hass) == set()
    stored = await Store(hass, STORE_VERSION, recipe_store_key(RECIPE_CRITICAL_LABEL)).async_load()
    assert stored is None


async def test_switched_on_with_no_label_picked_the_sensor_reads_zero_and_sends_nothing(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """Switching the recipe on with no critical label chosen still creates its sensor, reading 0, with no request sent."""
    register_unit_sensor(hass, "valve_a", unit=None, name="Valve A", domain="valve")
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    await hass.config_entries.options.async_configure(result["flow_id"], {**_OFF_OPTIONS, CONF_CRITICAL_LABEL_ENABLED: True})
    await hass.async_block_till_done(wait_background_tasks=True)

    sensor_entity_id = find_recipe_sensor(hass, mock_config_entry, RECIPE_CRITICAL_LABEL)
    assert sensor_entity_id is not None
    sensor_state = hass.states.get(sensor_entity_id)
    assert sensor_state is not None
    assert sensor_state.state == "0"
    assert posted_bodies(aioclient_mock) == []
