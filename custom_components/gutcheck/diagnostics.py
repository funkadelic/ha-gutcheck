"""Diagnostics download for Gut Check config entries.

Only the API key is redacted. Names and payloads stay in: the user reads the
file before sharing it, and a bug report needs them. Every value comes from
this repo's own TypedDicts, so a deny-list is enough.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant

if TYPE_CHECKING:
    from . import GutCheckConfigEntry

TO_REDACT = {CONF_API_KEY}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: GutCheckConfigEntry) -> dict[str, Any]:
    """Return a snapshot of one config entry with the API key redacted.

    Nothing is awaited, so a download cannot race a recipe run.
    """
    data = entry.runtime_data
    payload: dict[str, Any] = {
        "entry": {
            "data": dict(entry.data),
            "options": dict(entry.options),
        },
        "budget": {
            "daily_budget": data.budget.daily_budget,
            "spent_today": data.budget.spent_today,
            "remaining": data.budget.remaining,
        },
        "recipes": {recipe_id: coordinator.data for recipe_id, coordinator in data.coordinators.items()},
    }
    return async_redact_data(payload, TO_REDACT)
