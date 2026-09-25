"""The Gut Check integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.storage import Store

from .budget import BudgetGate
from .client import GutCheckClient
from .const import (
    ALL_RECIPE_IDS,
    AREA_ISSUE_PREFIX,
    BUDGET_STORE_KEY,
    CONF_AREAS_ENABLED,
    CONF_CRITICAL_LABEL,
    CONF_DAILY_BUDGET,
    CONF_DEVICE_CLASS_ENABLED,
    CONF_HEALTH_ENABLED,
    CONF_UPDATES_ENABLED,
    DEFAULT_DAILY_BUDGET,
    DEVICE_CLASS_ISSUE_PREFIX,
    DOMAIN,
    HEALTH_ISSUE_PREFIX,
    RECIPE_AREAS,
    RECIPE_DEVICE_CLASS,
    RECIPE_HEALTH,
    RECIPE_UPDATES,
    STORE_VERSION,
    UPDATES_ISSUE_PREFIX,
)
from .coordinator import RecipeCoordinator
from .recipes.areas import AreaRecipe
from .recipes.device_class import DeviceClassRecipe
from .recipes.health import HealthRecipe
from .recipes.shapes import recipe_store_key
from .recipes.updates import UpdateRecipe
from .repairs import async_delete_issues

PLATFORMS = [Platform.SENSOR, Platform.BUTTON]


def device_info(entry: ConfigEntry) -> DeviceInfo:
    """The single Gut Check service device every entity attaches to."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Gut Check",
        entry_type=DeviceEntryType.SERVICE,
    )


def _async_remove_recipe_entities(hass: HomeAssistant, entry: ConfigEntry, recipe_id: str) -> None:
    """Remove every entity this entry registered for a now-disabled recipe."""
    registry = er.async_get(hass)
    prefix = f"{entry.entry_id}_{recipe_id}"
    for entity_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity_entry.unique_id == prefix or entity_entry.unique_id.startswith(f"{prefix}_"):
            registry.async_remove(entity_entry.entity_id)


@dataclass
class GutCheckData:
    """Runtime data for one Gut Check config entry."""

    client: GutCheckClient
    budget: BudgetGate
    coordinators: dict[str, RecipeCoordinator]


type GutCheckConfigEntry = ConfigEntry[GutCheckData]


async def async_setup_entry(hass: HomeAssistant, entry: GutCheckConfigEntry) -> bool:
    """Set up Gut Check from a config entry."""
    api_key = entry.data.get(CONF_API_KEY)
    if not isinstance(api_key, str) or not api_key.strip():
        raise ConfigEntryAuthFailed("stored api key is missing or blank")

    client = GutCheckClient(async_get_clientsession(hass), api_key)
    budget = BudgetGate(hass, client, entry.options.get(CONF_DAILY_BUDGET, DEFAULT_DAILY_BUDGET))
    await budget.async_load()

    coordinators: dict[str, RecipeCoordinator] = {}
    if entry.options.get(CONF_HEALTH_ENABLED, True):
        health_recipe = HealthRecipe(entry.options.get(CONF_CRITICAL_LABEL))
        coordinators[health_recipe.recipe_id] = RecipeCoordinator(hass, entry, budget, health_recipe)
        entry.async_on_unload(health_recipe.shutdown)
    else:
        async_delete_issues(hass, HEALTH_ISSUE_PREFIX)
        _async_remove_recipe_entities(hass, entry, RECIPE_HEALTH)

    if entry.options.get(CONF_UPDATES_ENABLED, True):
        updates_recipe = UpdateRecipe(entry.options.get(CONF_CRITICAL_LABEL))
        coordinators[updates_recipe.recipe_id] = RecipeCoordinator(hass, entry, budget, updates_recipe)
        entry.async_on_unload(updates_recipe.shutdown)
    else:
        async_delete_issues(hass, UPDATES_ISSUE_PREFIX)
        _async_remove_recipe_entities(hass, entry, RECIPE_UPDATES)

    if entry.options.get(CONF_AREAS_ENABLED, True):
        area_recipe = AreaRecipe(entry.options.get(CONF_CRITICAL_LABEL))
        coordinators[area_recipe.recipe_id] = RecipeCoordinator(hass, entry, budget, area_recipe)
    else:
        # An ignored area card is the one record that the user rejected that
        # suggestion, and switching the recipe off and on must not bring it back.
        async_delete_issues(hass, AREA_ISSUE_PREFIX, keep_ignored=True)
        _async_remove_recipe_entities(hass, entry, RECIPE_AREAS)

    # Off by default, unlike the other three: an upgraded install must not
    # start raising device class cards unasked (DCLS-05).
    if entry.options.get(CONF_DEVICE_CLASS_ENABLED, False):
        device_class_recipe = DeviceClassRecipe(entry.options.get(CONF_CRITICAL_LABEL))
        coordinators[device_class_recipe.recipe_id] = RecipeCoordinator(hass, entry, budget, device_class_recipe)
    else:
        # An ignored device class card is the one record that the user
        # rejected that suggestion, and switching the recipe off and on must
        # not bring it back.
        async_delete_issues(hass, DEVICE_CLASS_ISSUE_PREFIX, keep_ignored=True)
        _async_remove_recipe_entities(hass, entry, RECIPE_DEVICE_CLASS)

    entry.runtime_data = GutCheckData(client=client, budget=budget, coordinators=coordinators)
    entry.async_on_unload(budget.async_start())

    for coordinator in coordinators.values():
        await coordinator.async_restore_or_schedule()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: GutCheckConfigEntry) -> bool:
    """Unload a Gut Check config entry.

    Deliberately does not delete any Repairs issue: unload also runs on every
    reload (an options save, a reauth), and deleting there would discard the
    user's ignores. Issues are only swept on removal, in async_remove_entry.
    """
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: GutCheckConfigEntry) -> None:
    """Delete every Gut Check Repairs issue and the persisted budget and recipe Stores."""
    async_delete_issues(hass)
    await Store(hass, STORE_VERSION, BUDGET_STORE_KEY).async_remove()
    for recipe_id in ALL_RECIPE_IDS:
        await Store(hass, STORE_VERSION, recipe_store_key(recipe_id)).async_remove()
