"""The Gut Check integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.start import async_at_started

from .budget import BudgetGate
from .client import GutCheckClient
from .const import CONF_CRITICAL_LABEL, CONF_DAILY_BUDGET, DEFAULT_DAILY_BUDGET
from .recipes.base import RecipeCoordinator
from .recipes.health import HealthRecipe

PLATFORMS = [Platform.SENSOR]


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
    budget = BudgetGate(client, entry.options.get(CONF_DAILY_BUDGET, DEFAULT_DAILY_BUDGET))
    health_recipe = HealthRecipe(entry.options.get(CONF_CRITICAL_LABEL))
    health_coordinator = RecipeCoordinator(hass, entry, budget, health_recipe)

    entry.runtime_data = GutCheckData(
        client=client,
        budget=budget,
        coordinators={health_recipe.recipe_id: health_coordinator},
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    @callback
    def _start_first_run(_hass: HomeAssistant) -> None:
        entry.async_create_background_task(
            hass,
            health_coordinator.async_refresh(),
            f"{entry.entry_id}_health_first_refresh",
        )

    entry.async_on_unload(async_at_started(hass, _start_first_run))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: GutCheckConfigEntry) -> bool:
    """Unload a Gut Check config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
