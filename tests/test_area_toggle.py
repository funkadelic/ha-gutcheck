"""Timeline tests: switching area suggestions off and on, running on demand, and removing the entry."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import (
    AREA_ISSUE_PREFIX,
    CONF_AREAS_ENABLED,
    CONF_DAILY_BUDGET,
    CONF_HEALTH_ENABLED,
    DEFAULT_DAILY_BUDGET,
    DOMAIN,
    HEALTH_ISSUE_PREFIX,
    OPTION_WORTH_FIXING,
    RECIPE_AREAS,
    STORE_VERSION,
)
from custom_components.gutcheck.recipes.shapes import recipe_store_key

from .conftest import (
    api_response,
    area_answer,
    choice_answer,
    create_areas,
    find_recipe_sensor,
    posted_bodies,
    register_area_device,
    register_jev_responses,
    register_unavailable_entity,
)


def _area_button_entity_id(hass: HomeAssistant, entry: MockConfigEntry) -> str | None:
    """The area recipe's Run button entity id, or None if it was not created."""
    return er.async_get(hass).async_get_entity_id("button", DOMAIN, f"{entry.entry_id}_{RECIPE_AREAS}_run")


def _area_issue_ids(hass: HomeAssistant) -> set[str]:
    """Every area-suggestion issue id currently in the registry."""
    return {
        issue_id for domain, issue_id in ir.async_get(hass).issues if domain == DOMAIN and issue_id.startswith(AREA_ISSUE_PREFIX)
    }


async def test_disable_reenable_run_and_removal_timeline(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """Disable keeps an ignored card; re-enable restores for free; the button runs on demand; removal clears everything."""
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
    assert _area_issue_ids(hass) == {open_issue_id, ignored_issue_id}
    ir.async_ignore_issue(hass, DOMAIN, ignored_issue_id, True)
    posted_before = len(posted_bodies(aioclient_mock))

    # Disable: the open card is gone, the ignored one stays ignored, nothing is posted.
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_AREAS_ENABLED: False, CONF_DAILY_BUDGET: DEFAULT_DAILY_BUDGET}
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert find_recipe_sensor(hass, mock_config_entry, RECIPE_AREAS) is None
    assert _area_button_entity_id(hass, mock_config_entry) is None
    assert _area_issue_ids(hass) == {ignored_issue_id}
    ignored_issue = ir.async_get(hass).async_get_issue(DOMAIN, ignored_issue_id)
    assert ignored_issue is not None
    assert ignored_issue.dismissed_version is not None
    assert len(posted_bodies(aioclient_mock)) == posted_before

    # Re-enable within the cadence window: restored from the Store, no POST, the ignore survives.
    posted_before = len(posted_bodies(aioclient_mock))
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_AREAS_ENABLED: True, CONF_DAILY_BUDGET: DEFAULT_DAILY_BUDGET}
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert find_recipe_sensor(hass, mock_config_entry, RECIPE_AREAS) is not None
    assert len(posted_bodies(aioclient_mock)) == posted_before
    assert _area_issue_ids(hass) == {open_issue_id, ignored_issue_id}
    ignored_issue = ir.async_get(hass).async_get_issue(DOMAIN, ignored_issue_id)
    assert ignored_issue is not None
    assert ignored_issue.dismissed_version is not None

    # Pressing Run area suggestions posts one request, first question id d0.
    button_entity_id = _area_button_entity_id(hass, mock_config_entry)
    assert button_entity_id is not None
    aioclient_mock.clear_requests()
    register_jev_responses(
        aioclient_mock,
        [api_response({"d0": area_answer("Kitchen", 0.9, list(areas)), "d1": area_answer("Kitchen", 0.9, list(areas))})],
    )
    await hass.services.async_call("button", "press", {"entity_id": button_entity_id}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)

    bodies = posted_bodies(aioclient_mock)
    assert len(bodies) == 1
    assert next(iter(bodies[0]["questions"])) == "d0"

    # Removing the entry deletes every area card, the ignored one included, and its Store.
    await hass.config_entries.async_remove(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert _area_issue_ids(hass) == set()
    stored = await Store(hass, STORE_VERSION, recipe_store_key(RECIPE_AREAS)).async_load()
    assert stored is None


async def test_an_ignored_health_card_does_not_survive_switching_the_health_check_off(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """keep_ignored stays scoped to the area recipe: an ignored health card is still deleted on disable."""
    entity_id = register_unavailable_entity(hass)
    entry = er.async_get(hass).async_get(entity_id)
    assert entry is not None
    issue_id = f"{HEALTH_ISSUE_PREFIX}{entry.id}"
    register_jev_responses(aioclient_mock, [api_response({"e0": choice_answer(OPTION_WORTH_FIXING, 0.9)})])
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    ir.async_ignore_issue(hass, DOMAIN, issue_id, True)

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_HEALTH_ENABLED: False, CONF_DAILY_BUDGET: DEFAULT_DAILY_BUDGET}
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
