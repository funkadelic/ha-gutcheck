"""Every recipe the integration registers applies the shared safety rules.

These read the recipe list off the config entry's coordinators rather than
naming HealthRecipe, so a recipe added later is covered once it is wired up.
"""

from __future__ import annotations

from homeassistant.const import CONF_API_KEY, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import (
    BLOCKED_DOMAINS,
    CONF_CRITICAL_LABEL,
    DOMAIN,
    OPTION_WORTH_FIXING,
)
from custom_components.gutcheck.recipes.base import Recipe
from custom_components.gutcheck.recipes.health import HealthRecipe

from .conftest import health_item, health_result

CRITICAL = "critical"


def _unavailable(hass: HomeAssistant, domain: str, unique_id: str, **kwargs: object) -> str:
    """Register one entity in the given domain and set it unavailable."""
    entry = er.async_get(hass).async_get_or_create(domain, "test", unique_id, **kwargs)  # type: ignore[arg-type]
    hass.states.async_set(entry.entity_id, STATE_UNAVAILABLE)
    return entry.entity_id


async def _registered_recipes(hass: HomeAssistant) -> list[Recipe]:
    """Set up the integration the way a real install does and return the recipes it registered.

    Set up before any unavailable entity exists, so the first run sends nothing
    and the test never reaches the API.
    """
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: "test-key"}, options={CONF_CRITICAL_LABEL: CRITICAL})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return [coordinator.recipe for coordinator in entry.runtime_data.coordinators.values()]


async def test_no_registered_recipe_selects_a_blocked_or_critical_entity(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A lock, alarm panel, cover, critically labelled entity or critically labelled device is never a subject."""
    recipes = await _registered_recipes(hass)
    assert recipes, "no recipe registered, so this would pass without testing anything"

    ordinary = _unavailable(hass, "sensor", "ordinary")
    forbidden = {
        "lock": _unavailable(hass, "lock", "front_door"),
        "alarm_control_panel": _unavailable(hass, "alarm_control_panel", "house_alarm"),
        "cover": _unavailable(hass, "cover", "garage_door"),
        "labelled entity": _unavailable(hass, "sensor", "labelled"),
    }
    er.async_get(hass).async_update_entity(forbidden["labelled entity"], labels={CRITICAL})

    device_entry = MockConfigEntry(domain="test")
    device_entry.add_to_hass(hass)
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(config_entry_id=device_entry.entry_id, identifiers={("test", "critical")})
    device_registry.async_update_device(device.id, labels={CRITICAL})
    forbidden["labelled device"] = _unavailable(hass, "sensor", "on_critical_device", device_id=device.id)

    # Every blocked domain is named, so a later recipe cannot pass by covering only locks.
    assert set(BLOCKED_DOMAINS) == {"lock", "alarm_control_panel", "cover"}

    selected_anywhere: set[str] = set()
    for recipe in recipes:
        batch = await recipe.async_prepare(hass)
        selected = {str(subject["entity_id"]) for subject in batch.subjects.values()}
        for case, entity_id in forbidden.items():
            assert entity_id not in selected, f"{recipe.recipe_id} selected the {case} entity"
        selected_anywhere |= selected

    # The positive control, proving the entities above were eligible but for the safety rules.
    assert ordinary in selected_anywhere


async def test_restore_drops_a_finding_whose_entity_is_now_in_a_blocked_domain(hass: HomeAssistant) -> None:
    """A stored finding that now resolves to a lock is dropped, not raised again."""
    entity_id = _unavailable(hass, "lock", "now_a_lock")
    recipe = HealthRecipe(critical_label=None)
    result = health_result({OPTION_WORTH_FIXING: [health_item(entity_id, "reg_blocked")]})

    await recipe.restore(hass, result)

    assert result["items"][OPTION_WORTH_FIXING] == []


async def test_restore_drops_a_finding_pointing_at_one_of_our_own_entities(hass: HomeAssistant) -> None:
    """Gut Check's own entities are out of scope on the restore path too, matching selection."""
    entry = er.async_get(hass).async_get_or_create("sensor", DOMAIN, "our_own")
    recipe = HealthRecipe(critical_label=None)
    result = health_result({OPTION_WORTH_FIXING: [health_item(entry.entity_id, "reg_ours")]})

    await recipe.restore(hass, result)

    assert result["items"][OPTION_WORTH_FIXING] == []
