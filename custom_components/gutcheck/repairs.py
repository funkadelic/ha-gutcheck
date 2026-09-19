"""Repairs issue sync and placeholder sanitizing for actionable findings."""

from __future__ import annotations

import logging
import re

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


def sanitize_placeholder(value: str) -> str:
    """Collapse whitespace, escape link, code and HTML characters, and cap length."""
    collapsed = _WHITESPACE_RE.sub(" ", value)
    escaped = _MARKDOWN_ACTIVE_RE.sub(r"\\\1", collapsed)
    return escaped[:_MAX_PLACEHOLDER_LENGTH]


@callback
def async_sync_issues(
    hass: HomeAssistant,
    prefix: str,
    translation_key: str,
    wanted: dict[str, dict[str, str]],
) -> None:
    """Create or update every wanted issue, and delete every other issue under prefix.

    Re-creating an existing issue id leaves its dismissed_version untouched,
    which is what keeps a user's ignore across runs, renames and reloads.
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
def async_track_recovery(hass: HomeAssistant, watched: dict[str, str]) -> CALLBACK_TYPE:
    """Delete a watched issue the moment its entity leaves STATE_UNAVAILABLE.

    Checks current state immediately, since an entity may have already
    recovered since it was classified, then subscribes for future changes. A
    removed or renamed entity reports new_state as None, which is left alone:
    the issue stays until the next run reclassifies it, which is what keeps
    an ignored issue alive across a rename.
    """
    for entity_id, issue_id in watched.items():
        state = hass.states.get(entity_id)
        if state is not None and state.state != STATE_UNAVAILABLE:
            ir.async_delete_issue(hass, DOMAIN, issue_id)

    @callback
    def _handle_state_change(event: Event[EventStateChangedData]) -> None:
        new_state = event.data["new_state"]
        if new_state is None or new_state.state == STATE_UNAVAILABLE:
            return
        ir.async_delete_issue(hass, DOMAIN, watched[event.data["entity_id"]])

    return async_track_state_change_event(hass, list(watched), _handle_state_change)
