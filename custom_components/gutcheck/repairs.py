"""Repairs issue sync and placeholder sanitizing for actionable findings."""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_state_change_event

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

_WHITESPACE_RE = re.compile(r"\s+")
# Escape only what can open a link, image, code span or raw HTML; escaping more
# shows literal backslashes in the plain-text title and inside code spans.
_MARKDOWN_ACTIVE_RE = re.compile(r"([\\`\[\]<>])")
_MAX_PLACEHOLDER_LENGTH = 100
_SAFE_URL_SCHEMES = frozenset({"http", "https"})


def sanitize_placeholder(value: str) -> str:
    """Collapse whitespace, cap length, then escape link, code and HTML characters.

    Truncating before escaping, not after: slicing escaped text can cut between a
    backslash and the character it escapes, leaving a dangling backslash.
    """
    collapsed = _WHITESPACE_RE.sub(" ", value)[:_MAX_PLACEHOLDER_LENGTH]
    return _MARKDOWN_ACTIVE_RE.sub(r"\\\1", collapsed)


def safe_url(value: str | None) -> str | None:
    """Return value only when it parses as an http or https URL with a network location.

    A release url comes from the update's publisher and a Repairs card
    renders learn_more_url as a clickable link, so a script or data scheme
    reaching it is an execution vector, not a cosmetic problem.
    """
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in _SAFE_URL_SCHEMES or not parsed.netloc:
        return None
    return value


@callback
def async_sync_issues(
    hass: HomeAssistant,
    prefix: str,
    translation_key: str,
    wanted: dict[str, dict[str, str]],
    learn_more_urls: dict[str, str] | None = None,
) -> None:
    """Create or update every wanted issue, and delete every other issue under prefix.

    Re-creating an existing issue id leaves its dismissed_version untouched,
    which is what keeps a user's ignore across runs, renames and reloads.
    learn_more_urls maps an issue id to its validated link; an id absent
    from it gets no link, same as omitting learn_more_url entirely.
    """
    for issue_id, placeholders in wanted.items():
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=translation_key,
            translation_placeholders={key: sanitize_placeholder(value) for key, value in placeholders.items()},
            learn_more_url=learn_more_urls.get(issue_id) if learn_more_urls else None,
        )

    registry = ir.async_get(hass)
    stale = [
        issue_id
        for domain, issue_id in list(registry.issues)
        if domain == DOMAIN and issue_id.startswith(prefix) and issue_id not in wanted
    ]
    for issue_id in stale:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
    _LOGGER.debug("issue sync created_or_updated=%s deleted=%s", len(wanted), len(stale))


@callback
def async_delete_issues(hass: HomeAssistant, prefix: str = "") -> None:
    """Delete every Gut Check issue whose id starts with prefix (default: every issue)."""
    registry = ir.async_get(hass)
    stale = [issue_id for domain, issue_id in list(registry.issues) if domain == DOMAIN and issue_id.startswith(prefix)]
    for issue_id in stale:
        ir.async_delete_issue(hass, DOMAIN, issue_id)


@callback
def async_track_recovery(
    hass: HomeAssistant, watched: dict[str, str], *, problem_state: str = STATE_UNAVAILABLE
) -> CALLBACK_TYPE:
    """Delete a watched issue the moment its entity leaves problem_state.

    The issue stays while the entity holds problem_state, and clears the
    moment it leaves it. Checks current state immediately, since an entity
    may have already recovered since it was classified, then subscribes for
    future changes. A removed or renamed entity reports new_state as None,
    which is left alone: the issue stays until the next run reclassifies it,
    which is what keeps an ignored issue alive across a rename.
    """
    for entity_id, issue_id in watched.items():
        state = hass.states.get(entity_id)
        if state is not None and state.state != problem_state:
            ir.async_delete_issue(hass, DOMAIN, issue_id)

    @callback
    def _handle_state_change(event: Event[EventStateChangedData]) -> None:
        """Delete the watched issue once its entity leaves problem_state."""
        new_state = event.data["new_state"]
        if new_state is None or new_state.state == problem_state:
            return
        ir.async_delete_issue(hass, DOMAIN, watched[event.data["entity_id"]])

    return async_track_state_change_event(hass, list(watched), _handle_state_change)
