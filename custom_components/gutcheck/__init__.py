"""The Gut Check integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
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
    CONF_CONFIG_ENTRIES_ENABLED,
    CONF_CRITICAL_LABEL,
    CONF_CRITICAL_LABEL_ENABLED,
    CONF_DAILY_BUDGET,
    CONF_DEVICE_CLASS_ENABLED,
    CONF_HEALTH_ENABLED,
    CONF_HIDE_DIAGNOSTIC_ENABLED,
    CONF_UPDATES_ENABLED,
    CONFIG_ENTRY_ISSUE_PREFIX,
    CRITICAL_LABEL_ISSUE_PREFIX,
    DEFAULT_DAILY_BUDGET,
    DEVICE_CLASS_APPLIED_STORE_KEY,
    DEVICE_CLASS_ISSUE_PREFIX,
    DOMAIN,
    HEALTH_ISSUE_PREFIX,
    HIDE_DIAGNOSTIC_APPLIED_STORE_KEY,
    HIDE_DIAGNOSTIC_ISSUE_PREFIX,
    RECIPE_AREAS,
    RECIPE_CONFIG_ENTRIES,
    RECIPE_CRITICAL_LABEL,
    RECIPE_DEVICE_CLASS,
    RECIPE_HEALTH,
    RECIPE_HIDE_DIAGNOSTIC,
    RECIPE_UPDATES,
    STORE_VERSION,
    UPDATES_ISSUE_PREFIX,
)
from .coordinator import RecipeCoordinator
from .recipes.areas import AreaRecipe
from .recipes.config_entries import ConfigEntryRecipe
from .recipes.critical_label import CriticalLabelRecipe
from .recipes.device_class import DeviceClassRecipe
from .recipes.device_class_undo import AppliedClasses
from .recipes.health import HealthRecipe
from .recipes.hide_diagnostic import HideDiagnosticRecipe
from .recipes.hide_diagnostic_undo import async_track_hidden
from .recipes.shapes import recipe_store_key
from .recipes.updates import UpdateRecipe
from .repairs import async_delete_issues
from .teardown import async_disable_recipe

PLATFORMS = [Platform.SENSOR, Platform.BUTTON]


def device_info(entry: ConfigEntry) -> DeviceInfo:
    """The single Gut Check service device every entity attaches to."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Gut Check",
        entry_type=DeviceEntryType.SERVICE,
    )


@dataclass
class GutCheckData:
    """Runtime data for one Gut Check config entry."""

    client: GutCheckClient
    budget: BudgetGate
    coordinators: dict[str, RecipeCoordinator]
    applied: AppliedClasses
    applied_hidden: AppliedClasses


type GutCheckConfigEntry = ConfigEntry[GutCheckData]


async def async_setup_entry(hass: HomeAssistant, entry: GutCheckConfigEntry) -> bool:
    """Set up Gut Check from a config entry."""
    api_key = entry.data.get(CONF_API_KEY)
    if not isinstance(api_key, str) or not api_key.strip():
        raise ConfigEntryAuthFailed("stored api key is missing or blank")

    client = GutCheckClient(async_get_clientsession(hass), api_key)
    budget = BudgetGate(hass, client, entry.options.get(CONF_DAILY_BUDGET, DEFAULT_DAILY_BUDGET))
    await budget.async_load()
    # Loaded whether or not the matching recipe is on: a class set or a sensor
    # hidden before the recipe was switched off must still be changeable back.
    applied = AppliedClasses(hass)
    await applied.async_load()
    applied_hidden = AppliedClasses(hass, HIDE_DIAGNOSTIC_APPLIED_STORE_KEY)
    await applied_hidden.async_load()
    entry.async_on_unload(async_track_hidden(hass, applied_hidden))

    coordinators: dict[str, RecipeCoordinator] = {}
    if entry.options.get(CONF_HEALTH_ENABLED, True):
        health_recipe = HealthRecipe(entry.options.get(CONF_CRITICAL_LABEL))
        coordinators[health_recipe.recipe_id] = RecipeCoordinator(hass, entry, budget, health_recipe)
        entry.async_on_unload(health_recipe.shutdown)
    else:
        async_disable_recipe(hass, entry, HEALTH_ISSUE_PREFIX, RECIPE_HEALTH)

    if entry.options.get(CONF_UPDATES_ENABLED, True):
        updates_recipe = UpdateRecipe(entry.options.get(CONF_CRITICAL_LABEL))
        coordinators[updates_recipe.recipe_id] = RecipeCoordinator(hass, entry, budget, updates_recipe)
        entry.async_on_unload(updates_recipe.shutdown)
    else:
        async_disable_recipe(hass, entry, UPDATES_ISSUE_PREFIX, RECIPE_UPDATES)

    if entry.options.get(CONF_AREAS_ENABLED, True):
        area_recipe = AreaRecipe(entry.options.get(CONF_CRITICAL_LABEL))
        coordinators[area_recipe.recipe_id] = RecipeCoordinator(hass, entry, budget, area_recipe)
    else:
        async_disable_recipe(hass, entry, AREA_ISSUE_PREFIX, RECIPE_AREAS, keep_ignored=True)

    # Off by default, unlike the other three: an upgraded install must not
    # start raising device class cards unasked.
    if entry.options.get(CONF_DEVICE_CLASS_ENABLED, False):
        device_class_recipe = DeviceClassRecipe(entry.options.get(CONF_CRITICAL_LABEL))
        coordinators[device_class_recipe.recipe_id] = RecipeCoordinator(hass, entry, budget, device_class_recipe)
    else:
        async_disable_recipe(hass, entry, DEVICE_CLASS_ISSUE_PREFIX, RECIPE_DEVICE_CLASS, keep_ignored=True)

    # Off by default, like device class suggestions: an upgraded install must
    # not start raising stuck-integration cards unasked.
    if entry.options.get(CONF_CONFIG_ENTRIES_ENABLED, False):
        config_entry_recipe = ConfigEntryRecipe()
        coordinators[config_entry_recipe.recipe_id] = RecipeCoordinator(hass, entry, budget, config_entry_recipe)
        entry.async_on_unload(config_entry_recipe.shutdown)
    else:
        # Advisory-only cards, like the update review: no keep_ignored, since
        # only the two suggestion recipes keep an ignore as rejection memory.
        async_disable_recipe(hass, entry, CONFIG_ENTRY_ISSUE_PREFIX, RECIPE_CONFIG_ENTRIES)

    # Off by default, like the other later recipes: an upgraded install must
    # not start raising critical label cards unasked.
    if entry.options.get(CONF_CRITICAL_LABEL_ENABLED, False):
        critical_label_recipe = CriticalLabelRecipe(entry.options.get(CONF_CRITICAL_LABEL))
        coordinators[critical_label_recipe.recipe_id] = RecipeCoordinator(hass, entry, budget, critical_label_recipe)
    else:
        async_disable_recipe(hass, entry, CRITICAL_LABEL_ISSUE_PREFIX, RECIPE_CRITICAL_LABEL, keep_ignored=True)

    # Off by default, like the other later recipes. Registered last so its
    # restore reads device class cards the device class restore already re-synced.
    if entry.options.get(CONF_HIDE_DIAGNOSTIC_ENABLED, False):
        hide_diagnostic_recipe = HideDiagnosticRecipe(entry.options.get(CONF_CRITICAL_LABEL))
        coordinators[hide_diagnostic_recipe.recipe_id] = RecipeCoordinator(hass, entry, budget, hide_diagnostic_recipe)
    else:
        async_disable_recipe(hass, entry, HIDE_DIAGNOSTIC_ISSUE_PREFIX, RECIPE_HIDE_DIAGNOSTIC, keep_ignored=True)

    entry.runtime_data = GutCheckData(
        client=client, budget=budget, coordinators=coordinators, applied=applied, applied_hidden=applied_hidden
    )
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

    Flushes both applied records before unloading, so a reload right after a
    confirm never loads an older record.
    """
    await entry.runtime_data.applied.async_flush()
    await entry.runtime_data.applied_hidden.async_flush()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: GutCheckConfigEntry) -> None:
    """Delete every Gut Check Repairs issue and the persisted budget, applied-record and recipe Stores."""
    async_delete_issues(hass)
    for key in (BUDGET_STORE_KEY, DEVICE_CLASS_APPLIED_STORE_KEY, HIDE_DIAGNOSTIC_APPLIED_STORE_KEY):
        await Store(hass, STORE_VERSION, key).async_remove()
    for recipe_id in ALL_RECIPE_IDS:
        await Store(hass, STORE_VERSION, recipe_store_key(recipe_id)).async_remove()
