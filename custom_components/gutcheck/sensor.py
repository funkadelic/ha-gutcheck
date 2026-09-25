"""Usage sensors and one recipe summary sensor per enabled recipe."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import GutCheckConfigEntry, device_info
from .budget import BudgetGate
from .const import (
    ATTR_COUNTS,
    ATTR_DAILY_BUDGET,
    ATTR_ITEMS,
    ATTR_LAST_PAYLOAD,
    ATTR_LAST_RUN,
    ATTR_REMAINING,
    ATTR_UNSURE,
    DOMAIN,
    PRICE_PER_MTOK_USD,
    SIGNAL_BUDGET_UPDATED,
)
from .coordinator import RecipeCoordinator
from .recipes.shapes import RecipeResult


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GutCheckConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the usage sensors and one summary sensor per enabled recipe."""
    data = entry.runtime_data
    entities: list[SensorEntity] = [
        TokensTodaySensor(entry, data.budget),
        CostTodaySensor(entry, data.budget),
    ]
    entities.extend(RecipeSummarySensor(coordinator, entry) for coordinator in data.coordinators.values())
    async_add_entities(entities)


class _UsageSensorBase(SensorEntity):
    """Shared base for the two budget-derived sensors: no polling, dispatcher-driven."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, budget: BudgetGate) -> None:
        """Bind the sensor to its budget gate and the shared Gut Check device."""
        self._budget = budget
        self._attr_device_info = device_info(entry)

    async def async_added_to_hass(self) -> None:
        """Write state on every budget reserve, reconcile, release and midnight reset."""
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_BUDGET_UPDATED, self._handle_budget_updated))

    @callback
    def _handle_budget_updated(self) -> None:
        """Rewrite state when the budget changes; the value is read from the gate."""
        self.async_write_ha_state()


class TokensTodaySensor(_UsageSensorBase):
    """Tokens spent today; resets to 0 at local midnight."""

    _attr_translation_key = "tokens_today"
    _attr_native_unit_of_measurement = "tokens"
    # TOTAL, not TOTAL_INCREASING: a released reservation or a smaller reconciled
    # actual lowers the count mid-day, and only TOTAL allows a value to fall.
    _attr_state_class = SensorStateClass.TOTAL

    def __init__(self, entry: ConfigEntry, budget: BudgetGate) -> None:
        """Set the unique id alongside the shared budget binding."""
        super().__init__(entry, budget)
        self._attr_unique_id = f"{entry.entry_id}_tokens_today"

    @property
    def native_value(self) -> int:
        """Tokens spent today."""
        return self._budget.spent_today

    @property
    def extra_state_attributes(self) -> dict[str, int]:
        """The configured daily budget and what remains of it today."""
        return {ATTR_DAILY_BUDGET: self._budget.daily_budget, ATTR_REMAINING: self._budget.remaining}


class CostTodaySensor(_UsageSensorBase):
    """Cost today in USD, derived from tokens spent."""

    _attr_translation_key = "cost_today"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "USD"
    _attr_suggested_display_precision = 5
    # TOTAL is the only state class Home Assistant allows with MONETARY.
    _attr_state_class = SensorStateClass.TOTAL

    def __init__(self, entry: ConfigEntry, budget: BudgetGate) -> None:
        """Set the unique id alongside the shared budget binding."""
        super().__init__(entry, budget)
        self._attr_unique_id = f"{entry.entry_id}_cost_today"

    @property
    def native_value(self) -> float:
        """Today's spend in USD at the configured price per million tokens."""
        return self._budget.spent_today * PRICE_PER_MTOK_USD / 1_000_000


class RecipeSummarySensor(CoordinatorEntity[RecipeCoordinator], SensorEntity):
    """Open card count in state; items, unsure and the last payload in unrecorded attributes."""

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
        self._attr_device_info = device_info(entry)

    @property
    def _data(self) -> RecipeResult | None:
        """The coordinator's latest result, or None before the first run completes."""
        return self._recipe_coordinator.data

    @property
    def native_value(self) -> int | None:
        """Open, unignored Repairs cards this recipe raised, or None before the first run completes."""
        data = self._data
        if data is None:
            return None
        prefix = self._recipe_coordinator.recipe.issue_prefix
        # Only active issues show in Repairs; a stored one stays inactive
        # after a restart until this recipe's next run or restore recreates it.
        return sum(
            1
            for (domain, issue_id), issue in ir.async_get(self.hass).issues.items()
            if domain == DOMAIN and issue_id.startswith(prefix) and issue.active and issue.dismissed_version is None
        )

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
