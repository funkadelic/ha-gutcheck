"""Source entities of built-in sensors that read recorded history rather than live state."""

from __future__ import annotations

import logging
from collections import Counter

from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.const import CONF_ENTITY_ID
from homeassistant.core import HomeAssistant, valid_entity_id
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_component import EntityComponent

from .recorder_churn_const import RECORDER_READER_SOURCE_ATTRS

_LOGGER = logging.getLogger(__name__)


def _follow(node: object, path: tuple[str, ...]) -> str | None:
    """The attribute at the end of path when it is a valid entity id, else None."""
    for name in path:
        node = getattr(node, name, None)
    return node if isinstance(node, str) and valid_entity_id(node) else None


def _entry_sources(hass: HomeAssistant) -> set[str]:
    """Sources named in the options of UI-made statistics, history stats and filter helpers."""
    registry = er.async_get(hass)
    values = (
        entry.options.get(CONF_ENTITY_ID)
        for domain in RECORDER_READER_SOURCE_ATTRS
        for entry in hass.config_entries.async_entries(domain)
    )
    return {
        entity_id for value in values if isinstance(value, str) and (entity_id := er.async_resolve_entity_id(registry, value))
    }


def _running_sources(hass: HomeAssistant) -> set[str]:
    """Sources of every running statistics, history stats and filter sensor, YAML ones included.

    The source sits in a private attribute; one that cannot be read is
    counted per platform and left out.
    """
    component: EntityComponent[Entity] | None = hass.data.get(SENSOR_DOMAIN)
    sources: set[str] = set()
    unread: Counter[str] = Counter()
    for entity in component.entities if component else ():
        platform = getattr(getattr(entity, "platform", None), "platform_name", None)
        if platform not in RECORDER_READER_SOURCE_ATTRS:
            continue
        source = _follow(entity, RECORDER_READER_SOURCE_ATTRS[platform])
        if source is None:
            unread[platform] += 1
        else:
            sources.add(source)
    if unread:
        _LOGGER.debug("recorder-reading sensors with an unreadable source: %s", dict(unread))
    return sources


def recorder_reader_sources(hass: HomeAssistant) -> set[str]:
    """Entity ids a statistics, history stats or filter sensor reads from the recorder."""
    return _entry_sources(hass) | _running_sources(hass)
