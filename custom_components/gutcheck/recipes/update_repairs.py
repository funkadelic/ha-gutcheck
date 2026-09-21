"""Repairs issue lifecycle for possibly-breaking updates: create, link, and clear on recovery."""

from __future__ import annotations

from homeassistant.const import STATE_ON
from homeassistant.core import CALLBACK_TYPE, HomeAssistant

from ..const import ISSUE_POSSIBLY_BREAKING_UPDATE, UPDATES_ISSUE_PREFIX
from ..repairs import async_sync_issues, async_track_recovery, safe_url
from .shapes import Item


def _wanted_issues(possibly_breaking: list[Item]) -> dict[str, dict[str, str]]:
    """The issue id and placeholders for each possibly-breaking finding, keyed by registry id.

    Placeholders are the entity id and the latest version only: the
    publisher-written title and release-note excerpt must never become a
    placeholder, since a Repairs card renders Markdown.
    """
    return {
        f"{UPDATES_ISSUE_PREFIX}{item['registry_id']}": {
            "entity_id": str(item["entity_id"]),
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
