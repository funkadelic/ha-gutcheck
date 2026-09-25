"""Run button, one per enabled recipe."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import GutCheckConfigEntry, device_info
from .coordinator import RecipeCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GutCheckConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one Run button per enabled recipe."""
    async_add_entities(RecipeRunButton(coordinator, entry) for coordinator in entry.runtime_data.coordinators.values())


class RecipeRunButton(ButtonEntity):
    """Runs a recipe on demand.

    Deliberately a plain ButtonEntity holding a coordinator reference, not a
    CoordinatorEntity: those turn unavailable after a failed run, and the
    user must still be able to press Run to retry.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: RecipeCoordinator, entry: ConfigEntry) -> None:
        """Bind to the coordinator to run and the shared Gut Check device."""
        self._coordinator = coordinator
        self._attr_translation_key = f"{coordinator.recipe.recipe_id}_run"
        self._attr_unique_id = f"{entry.entry_id}_{coordinator.recipe.recipe_id}_run"
        self._attr_device_info = device_info(entry)

    async def async_press(self) -> None:
        """Force a full re-score, then start the run as a task the entry owns, so unload cancels it."""
        coordinator = self._coordinator
        coordinator.force_full_rescore()
        # A debounced request can defer the run to a timer task that unload never cancels.
        coordinator.config_entry.async_create_background_task(
            self.hass,
            coordinator.async_refresh(),
            f"{coordinator.config_entry.entry_id}_{coordinator.recipe.recipe_id}_run",
        )
