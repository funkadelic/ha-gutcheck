"""The Gut Check integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.storage import Store

from .budget import BudgetGate
from .client import GutCheckClient
from .const import (
    BUDGET_STORE_KEY,
    CONF_CRITICAL_LABEL,
    CONF_DAILY_BUDGET,
    CONF_HEALTH_ENABLED,
    DEFAULT_DAILY_BUDGET,
    DOMAIN,
    HEALTH_ISSUE_PREFIX,
    RECIPE_HEALTH,
    STORE_VERSION,
)
from .recipes.base import RecipeCoordinator, recipe_store_key
from .recipes.health import HealthRecipe
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
    for entity_entry in list(er.async_entries_for_config_entry(registry, entry.entry_id)):
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
    client = GutCheckClient(async_get_clientsession(hass), entry.data[CONF_API_KEY])
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
    await Store(hass, STORE_VERSION, recipe_store_key(RECIPE_HEALTH)).async_remove()
