"""Unit tests for __init__.py helpers not otherwise covered end-to-end."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.gutcheck import _async_remove_recipe_entities
from custom_components.gutcheck.const import DOMAIN


async def test_a_recipe_id_that_prefixes_another_ones_id_keeps_the_other_recipes_entities(hass: HomeAssistant) -> None:
    """Removing "update" must not sweep an unrelated "updates" recipe's entities too."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    kept = registry.async_get_or_create("sensor", DOMAIN, f"{entry.entry_id}_updates", config_entry=entry)
    removed_sensor = registry.async_get_or_create("sensor", DOMAIN, f"{entry.entry_id}_update", config_entry=entry)
    removed_button = registry.async_get_or_create("button", DOMAIN, f"{entry.entry_id}_update_run", config_entry=entry)

    _async_remove_recipe_entities(hass, entry, "update")

    assert registry.async_get(kept.entity_id) is not None
    assert registry.async_get(removed_sensor.entity_id) is None
    assert registry.async_get(removed_button.entity_id) is None
