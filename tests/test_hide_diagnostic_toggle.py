"""Timeline test: switching diagnostic sensor suggestions on and off, running on demand, and removing the entry."""

from __future__ import annotations

from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import (
    CONF_AREAS_ENABLED,
    CONF_CONFIG_ENTRIES_ENABLED,
    CONF_CRITICAL_LABEL_ENABLED,
    CONF_DAILY_BUDGET,
    CONF_DEVICE_CLASS_ENABLED,
    CONF_HEALTH_ENABLED,
    CONF_HIDE_DIAGNOSTIC_ENABLED,
    CONF_UPDATES_ENABLED,
    DEFAULT_DAILY_BUDGET,
    DOMAIN,
    HIDE_DIAGNOSTIC_ISSUE_PREFIX,
    RECIPE_HIDE_DIAGNOSTIC,
    STORE_VERSION,
)
from custom_components.gutcheck.recipes.shapes import recipe_store_key

from .conftest import (
    api_response,
    find_recipe_sensor,
    hide_diagnostic_answer,
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
    CONF_HIDE_DIAGNOSTIC_ENABLED: False,
    CONF_DAILY_BUDGET: DEFAULT_DAILY_BUDGET,
}


def _button_entity_id(hass: HomeAssistant, entry: MockConfigEntry) -> str | None:
    """The recipe's Run button entity id, or None if it was not created."""
    return er.async_get(hass).async_get_entity_id("button", DOMAIN, f"{entry.entry_id}_{RECIPE_HIDE_DIAGNOSTIC}_run")


def _hide_issue_ids(hass: HomeAssistant) -> set[str]:
    """Every diagnostic sensor card issue id currently in the registry."""
    return {
        issue_id
        for domain, issue_id in ir.async_get(hass).issues
        if domain == DOMAIN and issue_id.startswith(HIDE_DIAGNOSTIC_ISSUE_PREFIX)
    }


async def _set_enabled(hass: HomeAssistant, entry: MockConfigEntry, enabled: bool) -> None:
    """Save the options with only this recipe's switch changed."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(result["flow_id"], {**_OFF_OPTIONS, CONF_HIDE_DIAGNOSTIC_ENABLED: enabled})
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_off_by_default_then_disable_reenable_run_and_removal_timeline(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """Off by default; on raises cards; off keeps an ignored one; on restores free; run posts once; removal wipes it all."""
    first = register_unit_sensor(hass, "net_a", unit=None, name="Network A")
    second = register_unit_sensor(hass, "net_b", unit=None, name="Network B")
    ordered = sorted((first, second), key=lambda entry: entry.entity_id)
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert find_recipe_sensor(hass, mock_config_entry, RECIPE_HIDE_DIAGNOSTIC) is None
    assert _button_entity_id(hass, mock_config_entry) is None
    assert _hide_issue_ids(hass) == set()
    assert posted_bodies(aioclient_mock) == []

    both = api_response({f"h{index}": hide_diagnostic_answer("diagnostic", 0.9) for index in range(2)})
    register_jev_responses(aioclient_mock, [both, both])
    await _set_enabled(hass, mock_config_entry, True)

    sensor_entity_id = find_recipe_sensor(hass, mock_config_entry, RECIPE_HIDE_DIAGNOSTIC)
    assert sensor_entity_id is not None
    open_issue_id = f"{HIDE_DIAGNOSTIC_ISSUE_PREFIX}{ordered[0].id}"
    ignored_issue_id = f"{HIDE_DIAGNOSTIC_ISSUE_PREFIX}{ordered[1].id}"
    assert _hide_issue_ids(hass) == {open_issue_id, ignored_issue_id}
    assert await async_setup_component(hass, "repairs", {})
    manager = repairs_flow_manager(hass)
    assert manager is not None
    result = await manager.async_init(DOMAIN, data={"issue_id": ignored_issue_id})
    result = await manager.async_configure(result["flow_id"], {"next_step_id": "ignore"})
    assert result["type"] is FlowResultType.ABORT
    posted_before = len(posted_bodies(aioclient_mock))

    await _set_enabled(hass, mock_config_entry, False)

    assert find_recipe_sensor(hass, mock_config_entry, RECIPE_HIDE_DIAGNOSTIC) is None
    assert _button_entity_id(hass, mock_config_entry) is None
    assert _hide_issue_ids(hass) == {ignored_issue_id}
    ignored = ir.async_get(hass).async_get_issue(DOMAIN, ignored_issue_id)
    assert ignored is not None
    assert ignored.dismissed_version is not None
    assert len(posted_bodies(aioclient_mock)) == posted_before

    await _set_enabled(hass, mock_config_entry, True)

    assert find_recipe_sensor(hass, mock_config_entry, RECIPE_HIDE_DIAGNOSTIC) is not None
    assert len(posted_bodies(aioclient_mock)) == posted_before
    assert _hide_issue_ids(hass) == {open_issue_id, ignored_issue_id}
    state = hass.states.get(sensor_entity_id)
    assert state is not None
    assert state.state == "1"
    ignored = ir.async_get(hass).async_get_issue(DOMAIN, ignored_issue_id)
    assert ignored is not None
    assert ignored.dismissed_version is not None

    assert _button_entity_id(hass, mock_config_entry) is not None
    await press_recipe_run(hass, mock_config_entry, RECIPE_HIDE_DIAGNOSTIC)
    bodies = posted_bodies(aioclient_mock)
    assert len(bodies) == posted_before + 1
    assert next(iter(bodies[-1]["questions"])) == "h0"

    await hass.config_entries.async_remove(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert _hide_issue_ids(hass) == set()
    stored = await Store(hass, STORE_VERSION, recipe_store_key(RECIPE_HIDE_DIAGNOSTIC)).async_load()
    assert stored is None
