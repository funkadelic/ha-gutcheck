"""Repairs issue lifecycle for possibly-breaking updates: create, link, and clear on recovery."""

from __future__ import annotations

from homeassistant.const import STATE_ON
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from ..const import ISSUE_POSSIBLY_BREAKING_UPDATE, UPDATES_ISSUE_PREFIX
from ..repairs import async_sync_issues, async_track_recovery, safe_url
from .shapes import Item


def _wanted_issues(possibly_breaking: list[Item]) -> dict[str, dict[str, str]]:
    """The issue id and placeholders for each possibly-breaking finding, keyed by registry id.

    The release-note title and text never become a placeholder; the device
    name does, sanitized like every placeholder, since a Repairs card renders Markdown.
    """
    return {
        f"{UPDATES_ISSUE_PREFIX}{item['registry_id']}": {
            "entity_id": str(item["entity_id"]),
            "name": str(item["name"]),
            "latest_version": str(item["latest_version"]),
        }
        for item in possibly_breaking
    }


def _learn_more_urls(possibly_breaking: list[Item]) -> dict[str, str]:
    """The validated learn_more_url for each possibly-breaking finding that has one, keyed by issue id."""
    urls: dict[str, str] = {}
    for item in possibly_breaking:
        release_url = item.get("release_url")
        safe = safe_url(release_url) if isinstance(release_url, str) else None
        if safe is not None:
            urls[f"{UPDATES_ISSUE_PREFIX}{item['registry_id']}"] = safe
    return urls


def _with_current_names(hass: HomeAssistant, items: list[Item]) -> list[Item]:
    """Each item with its entity id and display name re-read by registry id, so a rename since the run is followed.

    The name is the device's (the integration or add-on being updated), then the entity's, then the entity id.
    """
    registry = er.async_get(hass)
    devices = dr.async_get(hass)
    resolved: list[Item] = []
    for item in items:
        entry = registry.async_get(str(item["registry_id"]))
        if entry is None:
            resolved.append({**item, "name": str(item["entity_id"])})
            continue
        device = devices.async_get(entry.device_id) if entry.device_id else None
        device_name = (device.name_by_user or device.name) if device else None
        name = device_name or entry.name or entry.original_name or entry.entity_id
        resolved.append({**item, "entity_id": entry.entity_id, "name": name})
    return resolved


class UpdateIssueTracker:
    """Owns the recovery-tracking subscription for possibly-breaking update issues."""

    def __init__(self) -> None:
        """Start with no active subscription."""
        self._unsub_recovery: CALLBACK_TYPE | None = None

    def sync(self, hass: HomeAssistant, possibly_breaking: list[Item]) -> None:
        """Sync the possibly-breaking Repairs issues and re-arm recovery tracking.

        Watches for the entity leaving STATE_ON, which covers installed,
        skipped and superseded in one condition; a version bump alone
        leaves the entity on and so never wrongly clears the card.
        """
        possibly_breaking = _with_current_names(hass, possibly_breaking)
        async_sync_issues(
            hass,
            UPDATES_ISSUE_PREFIX,
            ISSUE_POSSIBLY_BREAKING_UPDATE,
            _wanted_issues(possibly_breaking),
            _learn_more_urls(possibly_breaking),
        )
        self.shutdown()
        watched = {str(item["entity_id"]): f"{UPDATES_ISSUE_PREFIX}{item['registry_id']}" for item in possibly_breaking}
        if watched:
            self._unsub_recovery = async_track_recovery(hass, watched, problem_state=STATE_ON)

    def shutdown(self) -> None:
        """Cancel the recovery subscription, if any."""
        if self._unsub_recovery is not None:
            self._unsub_recovery()
            self._unsub_recovery = None
