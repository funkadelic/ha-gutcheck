"""Diagnostics download for Gut Check config entries.

Redacts the API key and every `title` key, since a config entry title is often
an account email; update titles go too, as redaction is by key. Every email
address left in any string goes as well, which covers a result stored before
names were redacted. Other names and payloads stay in: a bug report needs them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant

from .describe import redact_emails

if TYPE_CHECKING:
    from . import GutCheckConfigEntry

TO_REDACT = {CONF_API_KEY, "title"}


def _redact(payload: dict[str, Any]) -> dict[str, Any]:
    """Redact TO_REDACT keys, then every email address in what is left."""
    scrubbed: dict[str, Any] = _scrub_emails(async_redact_data(payload, TO_REDACT))
    return scrubbed


def _scrub_emails(value: Any) -> Any:
    """Every string in a JSON-shaped value with its email addresses redacted."""
    if isinstance(value, str):
        return redact_emails(value)
    if isinstance(value, dict):
        return {key: _scrub_emails(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub_emails(item) for item in value]
    return value


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: GutCheckConfigEntry) -> dict[str, Any]:
    """Return a snapshot of one config entry with the API key, titles and email addresses redacted.

    Nothing is awaited, so a download cannot race a recipe run.
    """
    payload: dict[str, Any] = {
        "entry": {
            "data": dict(entry.data),
            "options": dict(entry.options),
        },
    }
    # HA serves this view for any entry; runtime data exists only once setup finished.
    if entry.state is not ConfigEntryState.LOADED:
        return _redact(payload)
    data = entry.runtime_data
    payload |= {
        "budget": {
            "daily_budget": data.budget.daily_budget,
            "spent_today": data.budget.spent_today,
            "remaining": data.budget.remaining,
        },
        "recipes": {recipe_id: coordinator.data for recipe_id, coordinator in data.coordinators.items()},
    }
    return _redact(payload)
