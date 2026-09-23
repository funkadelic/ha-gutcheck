"""Batched recorder lookup of when each entity last left a non-unavailable state."""

from __future__ import annotations

import functools
from datetime import datetime, timedelta

from homeassistant.components.recorder import history
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers.recorder import DATA_INSTANCE, get_instance
from homeassistant.util import dt as dt_util


async def async_unavailable_since(hass: HomeAssistant, entity_ids: list[str]) -> tuple[int, dict[str, datetime | None]] | None:
    """Return (purge_keep_days, {entity_id: time last left unavailable, or None}).

    None overall means the recorder is not running; the caller falls back to
    last_changed. A mapped None means every retained row for that entity is
    unavailable back to the window start, i.e. the outage is at least as old
    as the retention window.
    An entity with no rows at all, or whose latest rows are not unavailable,
    is left out of the map, so the caller falls back for that entity too.
    """
    if DATA_INSTANCE not in hass.data:
        return None

    instance = get_instance(hass)
    keep_days = instance.keep_days
    if not entity_ids:
        return keep_days, {}

    start_time = dt_util.utcnow() - timedelta(days=keep_days)
    query = functools.partial(
        history.get_significant_states,
        hass,
        start_time,
        entity_ids=entity_ids,
        include_start_time_state=True,
        significant_changes_only=False,
        minimal_response=False,
        no_attributes=True,
    )
    changes = await instance.async_add_executor_job(query)

    result: dict[str, datetime | None] = {}
    for entity_id, rows in changes.items():
        run_start: datetime | None = None
        seen_available = False
        for row in rows:
            if not isinstance(row, State):
                continue
            if row.state == "":
                # The recorder writes this marker when a state is removed from
                # the state machine (a restart's remove-then-restore). Skipping
                # it keeps that gap from looking like a recovery.
                continue
            if row.state == STATE_UNAVAILABLE:
                if run_start is None:
                    run_start = row.last_changed
            else:
                seen_available = True
                run_start = None
        if run_start is None:
            # The current outage has no retained row yet (not flushed), so the
            # caller falls back to last_changed rather than reading a full window.
            continue
        # An unavailable first row inside the window dates the outage's start
        # (new entity or young database).
        result[entity_id] = run_start if seen_available or run_start > start_time else None

    return keep_days, result
