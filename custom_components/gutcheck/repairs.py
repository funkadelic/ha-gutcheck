"""Repairs issue sync and placeholder sanitizing for actionable findings."""

from __future__ import annotations

import logging
import re
from collections.abc import Collection, Mapping
from urllib.parse import urlparse

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_state_change_event

from .const import DOMAIN, NO_VERDICT_STATES

# Re-export so the repairs platform loader finds this hook on this module.
from .recipes.suggestion_flow import async_create_fix_flow as async_create_fix_flow

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


def _create_issue(
    hass: HomeAssistant,
    issue_id: str,
    translation_key: str,
    placeholders: Mapping[str, str],
    *,
    is_fixable: bool = False,
    is_persistent: bool = False,
    learn_more_url: str | None = None,
    data: dict[str, str | int | float | None] | None = None,
) -> None:
    """Create or update one issue, sanitizing its placeholders."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=is_fixable,
        is_persistent=is_persistent,
        severity=ir.IssueSeverity.WARNING,
        translation_key=translation_key,
        translation_placeholders={key: sanitize_placeholder(value) for key, value in placeholders.items()},
        learn_more_url=learn_more_url,
        data=data,
    )


def safe_url(value: str | None) -> str | None:
    """Return value only when it parses as an http or https URL with a network location.

    A release url comes from the update's publisher and renders as a
    clickable link, so a script or data scheme reaching it is an execution
    vector, not a cosmetic problem.
    """
    if not value:
        return None
    try:
        parsed = urlparse(value)
    except ValueError:  # an unclosed IPv6 bracket, for one
        return None
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
    *,
    is_fixable: bool = False,
    is_persistent: bool = False,
    issue_data: Mapping[str, dict[str, str | int | float | None]] | None = None,
    keep: Collection[str] = (),
) -> None:
    """Create or update every wanted issue, and delete every other issue under prefix.

    Re-creating an existing issue id leaves its dismissed_version untouched,
    which is what keeps a user's ignore across runs, renames and reloads.
    learn_more_urls and issue_data map an issue id to its link or its fixable
    flow's data; an id absent from either gets none, as before. keep names
    existing issue ids the stale sweep must leave untouched, dismissed_version
    included. is_persistent makes HA store the whole issue and reload it
    active at startup.
    """
    for issue_id, placeholders in wanted.items():
        _create_issue(
            hass,
            issue_id,
            translation_key,
            placeholders,
            is_fixable=is_fixable,
            is_persistent=is_persistent,
            learn_more_url=learn_more_urls.get(issue_id) if learn_more_urls else None,
            data=issue_data.get(issue_id) if issue_data else None,
        )

    registry = ir.async_get(hass)
    stale = [
        issue_id
        for domain, issue_id in registry.issues
        if domain == DOMAIN and issue_id.startswith(prefix) and issue_id not in wanted and issue_id not in keep
    ]
    for issue_id in stale:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
    _LOGGER.debug("issue sync created_or_updated=%s deleted=%s kept=%s", len(wanted), len(stale), len(keep))


@callback
def async_create_ignored_issue(
    hass: HomeAssistant,
    issue_id: str,
    translation_key: str,
    placeholders: Mapping[str, str],
    data: dict[str, str | int | float | None],
) -> None:
    """Create an already-ignored fixable issue that records a rejection made outside a run."""
    _create_issue(hass, issue_id, translation_key, placeholders, is_fixable=True, is_persistent=True, data=data)
    ir.async_ignore_issue(hass, DOMAIN, issue_id, True)


@callback
def async_delete_issues(hass: HomeAssistant, prefix: str = "", *, keep_ignored: bool = False) -> None:
    """Delete every Gut Check issue whose id starts with prefix (default: every issue).

    keep_ignored leaves an ignored issue in place: it is the user's
    rejection record, and a recipe off/on toggle must not lose it.
    """
    registry = ir.async_get(hass)
    stale = [
        issue_id
        for domain, issue_id in registry.issues
        if domain == DOMAIN and issue_id.startswith(prefix) and not (keep_ignored and is_ignored(registry, issue_id))
    ]
    for issue_id in stale:
        ir.async_delete_issue(hass, DOMAIN, issue_id)


def is_ignored(registry: ir.IssueRegistry, issue_id: str) -> bool:
    """Whether the issue's dismissed_version is set, meaning the user ignored it."""
    issue = registry.async_get_issue(DOMAIN, issue_id)
    return issue is not None and issue.dismissed_version is not None


def _has_recovered(state: str, problem_state: str) -> bool:
    """Whether a state says the problem is over, rather than saying nothing at all.

    A reload, a device dropping off, or a source going quiet resolve
    nothing, and clearing on one of them would take the dismissal with it.
    """
    if state == problem_state:
        return False
    return state not in NO_VERDICT_STATES


@callback
def async_track_recovery(
    hass: HomeAssistant, watched: dict[str, str], *, problem_state: str = STATE_UNAVAILABLE
) -> CALLBACK_TYPE:
    """Delete a watched issue the moment its entity recovers from problem_state.

    Checks current state immediately, then subscribes for future changes. A
    removed or renamed entity reports new_state as None and is left alone,
    which keeps an ignored issue alive across a rename.
    """
    for entity_id, issue_id in watched.items():
        state = hass.states.get(entity_id)
        if state is not None and _has_recovered(state.state, problem_state):
            ir.async_delete_issue(hass, DOMAIN, issue_id)

    @callback
    def _handle_state_change(event: Event[EventStateChangedData]) -> None:
        """Delete the watched issue once its entity recovers from problem_state."""
        new_state = event.data["new_state"]
        if new_state is None or not _has_recovered(new_state.state, problem_state):
            return
        ir.async_delete_issue(hass, DOMAIN, watched[event.data["entity_id"]])

    return async_track_state_change_event(hass, list(watched), _handle_state_change)
