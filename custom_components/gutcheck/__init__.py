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
    BUDGET_STORE_KEY,
    CONF_CRITICAL_LABEL,
    CONF_DAILY_BUDGET,
    DEFAULT_DAILY_BUDGET,
    DEVICE_CLASS_APPLIED_STORE_KEY,
    DOMAIN,
    HIDE_DIAGNOSTIC_APPLIED_STORE_KEY,
    STORE_VERSION,
)
from .coordinator import RecipeCoordinator
from .recipes.device_class_undo import AppliedClasses
from .recipes.hide_diagnostic_undo import async_track_hidden
from .recipes.registry import RECIPE_SPECS
from .recipes.shapes import recipe_store_key
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

    critical_label = entry.options.get(CONF_CRITICAL_LABEL)
    coordinators: dict[str, RecipeCoordinator] = {}
    for spec in RECIPE_SPECS:
        if not entry.options.get(spec.option_key, spec.default):
            async_disable_recipe(hass, entry, spec.issue_prefix, spec.recipe_id, keep_ignored=spec.keep_ignored)
            continue
        recipe = spec.factory(critical_label)
        coordinators[recipe.recipe_id] = RecipeCoordinator(hass, entry, budget, recipe)
        if (shutdown := getattr(recipe, "shutdown", None)) is not None:
            entry.async_on_unload(shutdown)

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
