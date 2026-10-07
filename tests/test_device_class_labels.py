"""Tests for the look-alike class descriptions in the model's criteria."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant

from custom_components.gutcheck.const import OPTION_NONE
from custom_components.gutcheck.recipes.device_class import DeviceClassRecipe
from custom_components.gutcheck.recipes.device_class_const import (
    DEVICE_CLASS_DESCRIPTIONS,
    DEVICE_CLASS_INSTRUCTIONS,
    DEVICE_CLASS_LABEL,
    DEVICE_CLASS_NONE_DESCRIPTION,
)
from custom_components.gutcheck.recipes.device_class_describe import candidate_classes, class_names

from .conftest import register_unit_sensor

WATER = "Water: water used from a supply, such as a water meter or the usage on a water bill"


async def _question(hass: HomeAssistant, unit: str) -> dict[str, object]:
    """Prepare one sensor with this unit and return its question."""
    register_unit_sensor(hass, "probe", unit=unit)
    batch = await DeviceClassRecipe(critical_label=None).async_prepare(hass)
    return next(iter(batch.questions.values()))


def _label(names: dict[str, str], device_class: str) -> str:
    """The described label for a class, built from the constants."""
    return DEVICE_CLASS_LABEL.format(name=names[device_class], description=DEVICE_CLASS_DESCRIPTIONS[device_class])


@pytest.mark.parametrize(
    ("unit", "described"),
    [
        pytest.param("gal", ("volume", "volume_storage", "water"), id="gallons"),
        pytest.param("ft³", ("gas", "volume", "volume_storage", "water"), id="cubic_feet"),
        pytest.param("hPa", ("atmospheric_pressure", "pressure"), id="hectopascals"),
    ],
)
async def test_look_alike_classes_carry_a_description(hass: HomeAssistant, unit: str, described: tuple[str, ...]) -> None:
    """Gallons, cubic feet and hPa questions describe their overlapping classes, keep instructions, and end in none."""
    question = await _question(hass, unit)
    names = await class_names(hass)

    criteria = question["criteria"]
    assert isinstance(criteria, dict)
    assert set(criteria) == {*candidate_classes(unit), OPTION_NONE}
    for cls in described:
        assert criteria[cls] == _label(names, cls)
    assert criteria[OPTION_NONE] == DEVICE_CLASS_NONE_DESCRIPTION
    assert question["instructions"] == DEVICE_CLASS_INSTRUCTIONS.format(index=0)


async def test_a_water_meter_class_reads_as_water_from_a_supply(hass: HomeAssistant) -> None:
    """The gallons question's water option is exactly the supply wording."""
    question = await _question(hass, "gal")

    assert question["criteria"]["water"] == WATER  # type: ignore[index]


@pytest.mark.parametrize("unit", ["%", "m/s", "°F", "ppm"])
async def test_every_other_question_keeps_bare_class_names(hass: HomeAssistant, unit: str) -> None:
    """A question with no look-alike classes offers HA's own names, unchanged."""
    question = await _question(hass, unit)
    names = await class_names(hass)

    criteria = question["criteria"]
    assert isinstance(criteria, dict)
    for cls in candidate_classes(unit):
        assert criteria[cls] == names[cls]
