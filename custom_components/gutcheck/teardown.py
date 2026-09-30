"""Sweeping a disabled recipe's issues and entities."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .repairs import async_delete_issues


def async_remove_recipe_entities(hass: HomeAssistant, entry: ConfigEntry, recipe_id: str) -> None:
    """Remove every entity this entry registered for a now-disabled recipe."""
    registry = er.async_get(hass)
    prefix = f"{entry.entry_id}_{recipe_id}"
    for entity_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity_entry.unique_id == prefix or entity_entry.unique_id.startswith(f"{prefix}_"):
            registry.async_remove(entity_entry.entity_id)


def async_disable_recipe(
    hass: HomeAssistant, entry: ConfigEntry, issue_prefix: str, recipe_id: str, *, keep_ignored: bool = False
) -> None:
    """Sweep a disabled recipe's issues and entities.

    keep_ignored leaves an ignored card in place: it is the user's rejection
    record, and a recipe off/on toggle must not bring the suggestion back.
    """
    async_delete_issues(hass, issue_prefix, keep_ignored=keep_ignored)
    async_remove_recipe_entities(hass, entry, recipe_id)
