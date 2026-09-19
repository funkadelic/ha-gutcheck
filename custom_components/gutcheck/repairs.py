"""Repairs issue sync and placeholder sanitizing for actionable findings."""

from __future__ import annotations

import logging
import re

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

_WHITESPACE_RE = re.compile(r"\s+")
# Backslash-escape every Markdown-active character so a value cannot open a
# link, image, emphasis or code span in a Repairs card.
_MARKDOWN_ACTIVE_RE = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~])")
_MAX_PLACEHOLDER_LENGTH = 100


def sanitize_placeholder(value: str) -> str:
    """Collapse whitespace, escape Markdown-active characters, and cap length."""
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
