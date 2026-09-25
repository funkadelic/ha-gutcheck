"""Timeline tests: switching device class suggestions on and off, running on demand, and removing the entry."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import (
    CONF_DAILY_BUDGET,
    CONF_DEVICE_CLASS_ENABLED,
    DEFAULT_DAILY_BUDGET,
    DEVICE_CLASS_ISSUE_PREFIX,
    DOMAIN,
    RECIPE_DEVICE_CLASS,
    STORE_VERSION,
)
from custom_components.gutcheck.recipes.shapes import recipe_store_key

from .conftest import (
    api_response,
    area_answer,
    find_device_class_sensor,
    posted_bodies,
    register_jev_responses,
    register_unit_sensor,
)

BATTERY_CANDIDATES = ["battery", "humidity", "moisture", "power_factor"]


def _device_class_button_entity_id(hass: HomeAssistant, entry: MockConfigEntry) -> str | None:
    """The device class recipe's Run button entity id, or None if it was not created."""
    return er.async_get(hass).async_get_entity_id("button", DOMAIN, f"{entry.entry_id}_{RECIPE_DEVICE_CLASS}_run")


def _device_class_issue_ids(hass: HomeAssistant) -> set[str]:
    """Every device class suggestion issue id currently in the registry."""
    return {
        issue_id
        for domain, issue_id in ir.async_get(hass).issues
        if domain == DOMAIN and issue_id.startswith(DEVICE_CLASS_ISSUE_PREFIX)
    }


async def test_off_by_default_then_disable_reenable_run_and_removal_timeline(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """Off by default; enabling raises cards; disable keeps an ignored one; re-enable, run and removal follow the area recipe."""
    open_sensor = register_unit_sensor(hass, "open", unit="%", name="Open Sensor")
    ignored_sensor = register_unit_sensor(hass, "ignored", unit="%", name="Ignored Sensor")
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert find_device_class_sensor(hass, mock_config_entry) is None
    assert _device_class_button_entity_id(hass, mock_config_entry) is None
    assert posted_bodies(aioclient_mock) == []

    both_confident = api_response(
        {"s0": area_answer("battery", 0.9, BATTERY_CANDIDATES), "s1": area_answer("battery", 0.9, BATTERY_CANDIDATES)}
    )
    register_jev_responses(aioclient_mock, [both_confident])
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_DEVICE_CLASS_ENABLED: True, CONF_DAILY_BUDGET: DEFAULT_DAILY_BUDGET}
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert find_device_class_sensor(hass, mock_config_entry) is not None
    open_issue_id = f"{DEVICE_CLASS_ISSUE_PREFIX}{open_sensor.id}"
    ignored_issue_id = f"{DEVICE_CLASS_ISSUE_PREFIX}{ignored_sensor.id}"
    assert _device_class_issue_ids(hass) == {open_issue_id, ignored_issue_id}
    ir.async_ignore_issue(hass, DOMAIN, ignored_issue_id, True)
    posted_before = len(posted_bodies(aioclient_mock))

    # Disable: the open card is gone, the ignored one stays ignored, nothing is posted.
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_DEVICE_CLASS_ENABLED: False, CONF_DAILY_BUDGET: DEFAULT_DAILY_BUDGET}
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert find_device_class_sensor(hass, mock_config_entry) is None
    assert _device_class_button_entity_id(hass, mock_config_entry) is None
    assert _device_class_issue_ids(hass) == {ignored_issue_id}
    ignored_issue = ir.async_get(hass).async_get_issue(DOMAIN, ignored_issue_id)
    assert ignored_issue is not None
    assert ignored_issue.dismissed_version is not None
    assert len(posted_bodies(aioclient_mock)) == posted_before

    # Re-enable within the cadence window: restored from the Store, no POST, the ignore survives.
    posted_before = len(posted_bodies(aioclient_mock))
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_DEVICE_CLASS_ENABLED: True, CONF_DAILY_BUDGET: DEFAULT_DAILY_BUDGET}
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert find_device_class_sensor(hass, mock_config_entry) is not None
    assert len(posted_bodies(aioclient_mock)) == posted_before
    assert _device_class_issue_ids(hass) == {open_issue_id, ignored_issue_id}
    ignored_issue = ir.async_get(hass).async_get_issue(DOMAIN, ignored_issue_id)
    assert ignored_issue is not None
    assert ignored_issue.dismissed_version is not None

    # Pressing Run device class suggestions posts one request, first question id s0.
    button_entity_id = _device_class_button_entity_id(hass, mock_config_entry)
    assert button_entity_id is not None
    aioclient_mock.clear_requests()
    register_jev_responses(aioclient_mock, [both_confident])
    await hass.services.async_call("button", "press", {"entity_id": button_entity_id}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)

    bodies = posted_bodies(aioclient_mock)
    assert len(bodies) == 1
    assert next(iter(bodies[0]["questions"])) == "s0"

    # Removing the entry deletes every device class card, the ignored one included, and its Store.
    await hass.config_entries.async_remove(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert _device_class_issue_ids(hass) == set()
    stored = await Store(hass, STORE_VERSION, recipe_store_key(RECIPE_DEVICE_CLASS)).async_load()
    assert stored is None
