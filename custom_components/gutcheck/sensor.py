"""Recipe summary sensor: one per enabled recipe."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import GutCheckConfigEntry
from .const import ATTR_COUNTS, ATTR_ITEMS, ATTR_LAST_PAYLOAD, ATTR_LAST_RUN, ATTR_UNSURE, DOMAIN
from .recipes.base import RecipeCoordinator, RecipeResult


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GutCheckConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one summary sensor per enabled recipe."""
    async_add_entities(RecipeSummarySensor(coordinator, entry) for coordinator in entry.runtime_data.coordinators.values())


class RecipeSummarySensor(CoordinatorEntity[RecipeCoordinator], SensorEntity):
    """Count in state; items, unsure and the last payload in unrecorded attributes."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _unrecorded_attributes = frozenset({ATTR_ITEMS, ATTR_UNSURE, ATTR_LAST_PAYLOAD})

    def __init__(self, coordinator: RecipeCoordinator, entry: ConfigEntry) -> None:
        """Bind the sensor to its coordinator and the shared Gut Check device."""
        super().__init__(coordinator)
        # homeassistant-stubs types BaseCoordinatorEntity.coordinator as Incomplete
        # (Any); keep our own correctly typed reference instead of relying on it.
        self._recipe_coordinator: RecipeCoordinator = coordinator
        self._attr_translation_key = coordinator.recipe.recipe_id
        self._attr_unique_id = f"{entry.entry_id}_{coordinator.recipe.recipe_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Gut Check",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def _data(self) -> RecipeResult | None:
        return self._recipe_coordinator.data

    @property
    def native_value(self) -> int | None:
        """Sum of every option's count, or None before the first run completes."""
        data = self._data
        if data is None:
            return None
        return sum(data["counts"].values(), start=0)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Counts, items, unsure entries, the last payload and the last run time."""
        data = self._data
        if data is None:
            return None
        return {
            ATTR_COUNTS: data["counts"],
            ATTR_ITEMS: data["items"],
            ATTR_UNSURE: data["unsure"],
            ATTR_LAST_PAYLOAD: data["last_payload"],
            ATTR_LAST_RUN: data["last_run"],
        }
