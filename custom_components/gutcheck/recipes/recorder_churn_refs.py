"""Read-only lookups of what uses an entity: dashboard history cards, Energy preferences, automations, scripts, scenes, groups."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from homeassistant.components.automation import automations_with_entity
from homeassistant.components.energy.data import async_get_manager
from homeassistant.components.group import groups_with_entity
from homeassistant.components.homeassistant.scene import scenes_with_entity
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.components.script import scripts_with_entity
from homeassistant.core import HomeAssistant, valid_entity_id
from homeassistant.exceptions import HomeAssistantError

from .recorder_churn_const import HISTORY_FEATURE_TYPES, RECORDER_CARD_TYPES, SENSOR_CARD_NO_GRAPH, SENSOR_CARD_TYPE


def _entity_ids(node: Any) -> Iterator[str]:
    """Every string anywhere inside nested dicts and lists that is a valid entity id."""
    if isinstance(node, str):
        if valid_entity_id(node):
            yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _entity_ids(value)
    elif isinstance(node, list):
        for item in node:
            yield from _entity_ids(item)


def _type(node: dict[str, Any]) -> str | None:
    """A config's type when it is a string; custom cards may nest a list or dict under "type"."""
    value = node.get("type")
    return value if isinstance(value, str) else None


def _reads_history(card: dict[str, Any]) -> bool:
    """Whether a card, header, footer or feature config draws recorded history."""
    card_type = _type(card)
    if card_type in RECORDER_CARD_TYPES:
        return True
    if card_type == SENSOR_CARD_TYPE and card.get("graph") not in (None, SENSOR_CARD_NO_GRAPH):
        return True
    features = card.get("features")
    return isinstance(features, list) and any(isinstance(f, dict) and _type(f) in HISTORY_FEATURE_TYPES for f in features)


def _history_card_ids(node: Any) -> set[str]:
    """Entity ids inside any recorder-backed card found anywhere in a dashboard config."""
    if isinstance(node, dict):
        if _reads_history(node):
            return set(_entity_ids(node))
        children: list[Any] = list(node.values())
    elif isinstance(node, list):
        children = node
    else:
        return set()
    return set().union(*(_history_card_ids(child) for child in children))


async def async_history_card_entity_ids(hass: HomeAssistant) -> set[str]:
    """Entity ids whose history a card draws on any readable dashboard.

    The auto-generated default and a YAML dashboard that fails to load raise
    a HomeAssistantError and are skipped.
    """
    data = hass.data.get(LOVELACE_DATA)
    if data is None:
        return set()
    shown: set[str] = set()
    for dashboard in list(data.dashboards.values()):
        try:
            config = await dashboard.async_load(False)
        except HomeAssistantError:
            continue
        shown |= _history_card_ids(config)
    return shown


async def async_energy_entity_ids(hass: HomeAssistant) -> set[str]:
    """Entity ids named anywhere in the Energy dashboard preferences, empty when never saved.

    No error handling: a failed read must fail the run, since an empty set
    would put an energy entity up for exclusion.
    """
    # A shared manager, loaded once.
    manager = await async_get_manager(hass)
    return set(_entity_ids(manager.data)) if manager.data is not None else set()


def referenced(hass: HomeAssistant, entity_id: str) -> bool:
    """Whether an automation, script, scene or group lists the entity.

    Entities read only inside a template are not found.
    """
    return any(
        find(hass, entity_id) for find in (automations_with_entity, scripts_with_entity, scenes_with_entity, groups_with_entity)
    )
