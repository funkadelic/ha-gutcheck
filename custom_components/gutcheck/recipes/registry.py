"""The recipes Gut Check registers, in registration order."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..const import (
    AREA_ISSUE_PREFIX,
    CONF_AREAS_ENABLED,
    CONF_CONFIG_ENTRIES_ENABLED,
    CONF_CRITICAL_LABEL_ENABLED,
    CONF_DEVICE_CLASS_ENABLED,
    CONF_HEALTH_ENABLED,
    CONF_HIDE_DIAGNOSTIC_ENABLED,
    CONF_RECORDER_CHURN_ENABLED,
    CONF_UPDATES_ENABLED,
    CONFIG_ENTRY_ISSUE_PREFIX,
    CRITICAL_LABEL_ISSUE_PREFIX,
    DEVICE_CLASS_ISSUE_PREFIX,
    HEALTH_ISSUE_PREFIX,
    HIDE_DIAGNOSTIC_ISSUE_PREFIX,
    RECIPE_AREAS,
    RECIPE_CONFIG_ENTRIES,
    RECIPE_CRITICAL_LABEL,
    RECIPE_DEVICE_CLASS,
    RECIPE_HEALTH,
    RECIPE_HIDE_DIAGNOSTIC,
    RECIPE_RECORDER_CHURN,
    RECIPE_UPDATES,
    RECORDER_CHURN_ISSUE_PREFIX,
    UPDATES_ISSUE_PREFIX,
)
from .areas import AreaRecipe
from .config_entries import ConfigEntryRecipe
from .critical_label import CriticalLabelRecipe
from .device_class import DeviceClassRecipe
from .health import HealthRecipe
from .hide_diagnostic import HideDiagnosticRecipe
from .recorder_churn import RecorderChurnRecipe
from .shapes import Recipe
from .updates import UpdateRecipe


@dataclass(frozen=True)
class RecipeSpec:
    """One recipe: its option, its default, how to build it and how to sweep it when off."""

    option_key: str
    default: bool
    recipe_id: str
    issue_prefix: str
    # Builds the recipe from the configured critical label.
    factory: Callable[[str | None], Recipe]
    # An ignored card is the user's rejection record: only the suggestion recipes keep it.
    keep_ignored: bool = False


# Registration order matters: hide_diagnostic goes after device class so its
# restore reads device class cards the device class restore already re-synced. Everything
# after the first three is off by default, so an upgraded install does not
# start raising cards unasked.
RECIPE_SPECS: tuple[RecipeSpec, ...] = (
    RecipeSpec(CONF_HEALTH_ENABLED, True, RECIPE_HEALTH, HEALTH_ISSUE_PREFIX, HealthRecipe),
    RecipeSpec(CONF_UPDATES_ENABLED, True, RECIPE_UPDATES, UPDATES_ISSUE_PREFIX, UpdateRecipe),
    RecipeSpec(CONF_AREAS_ENABLED, True, RECIPE_AREAS, AREA_ISSUE_PREFIX, AreaRecipe, keep_ignored=True),
    RecipeSpec(
        CONF_DEVICE_CLASS_ENABLED, False, RECIPE_DEVICE_CLASS, DEVICE_CLASS_ISSUE_PREFIX, DeviceClassRecipe, keep_ignored=True
    ),
    RecipeSpec(
        CONF_CONFIG_ENTRIES_ENABLED,
        False,
        RECIPE_CONFIG_ENTRIES,
        CONFIG_ENTRY_ISSUE_PREFIX,
        lambda _label: ConfigEntryRecipe(),
    ),
    RecipeSpec(
        CONF_CRITICAL_LABEL_ENABLED,
        False,
        RECIPE_CRITICAL_LABEL,
        CRITICAL_LABEL_ISSUE_PREFIX,
        CriticalLabelRecipe,
        keep_ignored=True,
    ),
    RecipeSpec(
        CONF_HIDE_DIAGNOSTIC_ENABLED,
        False,
        RECIPE_HIDE_DIAGNOSTIC,
        HIDE_DIAGNOSTIC_ISSUE_PREFIX,
        HideDiagnosticRecipe,
        keep_ignored=True,
    ),
    RecipeSpec(
        CONF_RECORDER_CHURN_ENABLED,
        False,
        RECIPE_RECORDER_CHURN,
        RECORDER_CHURN_ISSUE_PREFIX,
        RecorderChurnRecipe,
        keep_ignored=True,
    ),
)
