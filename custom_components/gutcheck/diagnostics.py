"""Diagnostics support for Gut Check config entries.

Home Assistant discovers this module by file presence and renders a
"Download diagnostics" action on the config entry page. The download is
admin-only.

The redaction policy here is narrower than the project's logging rule, and
deliberately so. A log line is emitted without anyone asking for it, so the
logging house style keeps device, area and entity names out of it entirely.
A diagnostics file is different: it is downloaded on purpose by someone who
can read it before deciding whether to send it on, most often attached to a
GitHub issue. So this dump redacts only the one field that authenticates,
the API key, and keeps everything else: the options, the budget counters,
and each recipe's classified items and last request, names included. A file
that hid those details would not be worth attaching to a bug report.

Every value in this payload is built from TypedDicts defined in this
repository (RecipeResult, SystemOneRequest). There is no vendor-controlled
shape riding along that could grow an unreviewed field, so a flat deny-list
is enough; an allow-list per record, as some other diagnostics modules use
for an untrusted third-party shape, would defend against a threat this
integration does not have.
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
    """Return a snapshot of one Gut Check config entry, with the API key redacted.

    Awaits nothing and mutates nothing: the whole payload is read from
    entry.data, entry.options and entry.runtime_data as they stand at the
    instant this function runs, so a download can never race a concurrent
    recipe run or a second download.
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
