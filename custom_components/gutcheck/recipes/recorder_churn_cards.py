"""Non-fixable Repairs cards for entities the model says need no recorded history."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from ..const import ISSUE_RECORDER_EXCLUDE_SUGGESTION, RECORDER_CHURN_ISSUE_PREFIX
from ..repairs import async_sync_issues
from .recorder_churn_const import MAX_RECORDER_EXCLUDE_CARDS, RECORDER_FILTER_DOCS_URL
from .shapes import Item


def _churn(item: Item) -> int:
    """An item's changes per day as an int, 0 when absent."""
    value = item.get("changes_per_day")
    return int(value) if isinstance(value, int | float) else 0


def sync_exclude_cards(hass: HomeAssistant, excluded: list[Item]) -> None:
    """Sync one advisory card per exclude item, the highest churn first, up to MAX_RECORDER_EXCLUDE_CARDS."""
    top = sorted(excluded, key=lambda item: (-_churn(item), str(item["entity_id"])))[:MAX_RECORDER_EXCLUDE_CARDS]
    wanted = {
        f"{RECORDER_CHURN_ISSUE_PREFIX}{item['registry_id']}": {
            "entity_id": str(item["entity_id"]),
            "bucket": str(item["bucket"]),
        }
        for item in top
    }
    async_sync_issues(
        hass,
        RECORDER_CHURN_ISSUE_PREFIX,
        ISSUE_RECORDER_EXCLUDE_SUGGESTION,
        wanted,
        dict.fromkeys(wanted, RECORDER_FILTER_DOCS_URL),
    )
