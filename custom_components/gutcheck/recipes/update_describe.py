"""Release-note fetching and the model-visible shape of one pending update."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.components.update import DATA_COMPONENT, UpdateEntity, UpdateEntityFeature  # type: ignore[attr-defined]
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er

from ..const import RELEASE_NOTES_FETCH_TIMEOUT, UPDATE_TITLE_MAX_CHARS
from ..describe import clean_release_notes, version_jump
from .shapes import Item

_LOGGER = logging.getLogger(__name__)


async def _async_fetch_one_note(hass: HomeAssistant, entry: er.RegistryEntry) -> str | None:
    """Fetch one entity's full release notes through the same path HA's own websocket handler uses.

    Requires the entity to exist, be available, and support the
    release-notes feature; anything else returns None so the caller
    falls back to release_summary. A raised exception, including a
    timeout past RELEASE_NOTES_FETCH_TIMEOUT, is left to propagate: the
    caller gathers with return_exceptions=True so one entity's failure
    cannot stall or fail the whole run.
    """
    entity: UpdateEntity | None = hass.data[DATA_COMPONENT].get_entity(entry.entity_id)
    if entity is None or not entity.available or UpdateEntityFeature.RELEASE_NOTES not in entity.supported_features:
        return None
    async with asyncio.timeout(RELEASE_NOTES_FETCH_TIMEOUT):
        return await entity.async_release_notes()


async def async_fetch_notes(
    hass: HomeAssistant, selected: list[tuple[er.RegistryEntry, State]]
) -> list[str | BaseException | None]:
    """Fetch every selected entity's release notes concurrently, one outcome per entity in order."""
    return await asyncio.gather(
        *(_async_fetch_one_note(hass, entry) for entry, _ in selected),
        return_exceptions=True,
    )


def describe_subject(entry: er.RegistryEntry, state: State) -> Item:
    """Build one update's code-only subject fields, which never reach the model.

    release_url lives only here, so it only ever reaches the Repairs card.
    """
    attributes = state.attributes
    return {
        "entity_id": entry.entity_id,
        "registry_id": entry.id,
        "installed_version": attributes.get("installed_version"),
        "latest_version": attributes.get("latest_version"),
        "skipped_version": attributes.get("skipped_version"),
        "release_url": attributes.get("release_url"),
    }


def describe(entry: er.RegistryEntry, state: State, fetched_notes: str | BaseException | None) -> Item:
    """Build one update's model-visible state fields."""
    attributes = state.attributes
    installed_version = attributes.get("installed_version")
    latest_version = attributes.get("latest_version")
    release_summary = attributes.get("release_summary")
    title = attributes.get("title")
    if isinstance(fetched_notes, BaseException):
        _LOGGER.debug(
            "update review release notes fetch failed integration=%s error=%s",
            entry.platform,
            type(fetched_notes).__name__,
        )
        notes_text = release_summary
    else:
        notes_text = fetched_notes or release_summary
    return {
        "integration": entry.platform,
        "installed_version": installed_version,
        "latest_version": latest_version,
        "version_jump": version_jump(installed_version, latest_version),
        "title": title[:UPDATE_TITLE_MAX_CHARS] if isinstance(title, str) else None,
        "release_summary": clean_release_notes(release_summary),
        "release_notes": clean_release_notes(notes_text),
    }
