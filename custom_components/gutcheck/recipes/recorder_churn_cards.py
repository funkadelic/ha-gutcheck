"""Non-fixable Repairs cards for entities the model says need no recorded history."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from ..const import DOMAIN, ISSUE_RECORDER_EXCLUDE_SUGGESTION, RECORDER_CHURN_ISSUE_PREFIX
from ..repairs import async_sync_issues, is_ignored
from .recorder_churn_const import MAX_RECORDER_EXCLUDE_CARDS, RECORDER_FILTER_DOCS_URL
from .safety import SafetyRules
from .shapes import Item


def _churn(item: Item) -> int:
    """An item's changes per day as an int, 0 when absent."""
    value = item.get("changes_per_day")
    return int(value) if isinstance(value, int | float) else 0


def _wanted(hass: HomeAssistant, safety: SafetyRules, excluded: list[Item]) -> dict[str, dict[str, str]]:
    """Cards to open: the heaviest exclude items still registered, still allowed and not ignored.

    An ignored card never takes one of the MAX_RECORDER_EXCLUDE_CARDS slots.
    The entity id comes from the live entry, so a rename is followed.
    """
    entities = er.async_get(hass)
    issues = ir.async_get(hass)
    wanted: dict[str, dict[str, str]] = {}
    for item in sorted(excluded, key=lambda item: (-_churn(item), str(item["entity_id"]))):
        if len(wanted) >= MAX_RECORDER_EXCLUDE_CARDS:
            break
        entry = entities.async_get(str(item["registry_id"]))
        issue_id = f"{RECORDER_CHURN_ISSUE_PREFIX}{item['registry_id']}"
        if entry is None or safety.excludes(hass, entry) or is_ignored(issues, issue_id):
            continue
        wanted[issue_id] = {"entity_id": entry.entity_id, "bucket": str(item["bucket"])}
    return wanted


def _kept(hass: HomeAssistant) -> set[str]:
    """Every ignored card whose entity is still registered: the ignore is the only rejection record."""
    entities = er.async_get(hass)
    issues = ir.async_get(hass)
    return {
        issue_id
        for domain, issue_id in issues.issues
        if domain == DOMAIN
        and issue_id.startswith(RECORDER_CHURN_ISSUE_PREFIX)
        and is_ignored(issues, issue_id)
        and entities.async_get(issue_id.removeprefix(RECORDER_CHURN_ISSUE_PREFIX)) is not None
    }


def sync_exclude_cards(hass: HomeAssistant, safety: SafetyRules, excluded: list[Item]) -> None:
    """Sync the capped exclude cards and keep every ignored one.

    Any other card under the prefix is swept: its entity dropped out,
    changed answer, fell past the cap, became critical or was removed.
    """
    wanted = _wanted(hass, safety, excluded)
    async_sync_issues(
        hass,
        RECORDER_CHURN_ISSUE_PREFIX,
        ISSUE_RECORDER_EXCLUDE_SUGGESTION,
        wanted,
        dict.fromkeys(wanted, RECORDER_FILTER_DOCS_URL),
        keep=_kept(hass),
    )
