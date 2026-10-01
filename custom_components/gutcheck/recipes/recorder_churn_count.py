"""One grouped recorder count per entity, never one row per change."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.components.recorder.db_schema import States, StatesMeta
from homeassistant.core import HomeAssistant
from homeassistant.helpers.recorder import DATA_INSTANCE, get_instance, session_scope
from homeassistant.util import dt as dt_util
from sqlalchemy import func, select

from .recorder_churn_const import CHURN_WINDOW_DAYS


def _count(hass: HomeAssistant, start_ts: float) -> dict[str, int]:
    """Count the state rows written since start_ts per entity id, on the recorder's executor."""
    query = (
        select(StatesMeta.entity_id, func.count(States.state_id))
        .join(StatesMeta, States.metadata_id == StatesMeta.metadata_id)
        .where(States.last_updated_ts >= start_ts)
        .group_by(States.metadata_id, StatesMeta.entity_id)
    )
    with session_scope(hass=hass, read_only=True) as session:
        return {entity_id: count for entity_id, count in session.execute(query) if entity_id is not None}


async def async_churn(hass: HomeAssistant) -> tuple[int, dict[str, int]] | None:
    """Return (window_days, {entity_id: state changes in the window}), or None when the count is unavailable.

    The window is the shorter of CHURN_WINDOW_DAYS and the recorder's
    retention. An entity the recorder's own filter no longer records is left
    out, since its older rows linger until purge. None means no recorder, or
    a query that failed (session_scope already logged why).
    """
    if DATA_INSTANCE not in hass.data:
        return None
    instance = get_instance(hass)
    window_days = min(CHURN_WINDOW_DAYS, instance.keep_days)
    start_ts = (dt_util.utcnow() - timedelta(days=window_days)).timestamp()
    try:
        counts = await instance.async_add_executor_job(_count, hass, start_ts)
    except Exception:  # a schema or database error reads as unavailable
        return None
    if instance.entity_filter is not None:
        counts = {entity_id: count for entity_id, count in counts.items() if instance.entity_filter(entity_id)}
    return window_days, counts
