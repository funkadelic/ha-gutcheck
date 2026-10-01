"""Keeps decided in code: entities a run never asks about, and exclude answers code overrules."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .recorder_churn_const import OPTION_EXCLUDE, OPTION_KEEP, REASON_DERIVED_SOURCE, REASON_ENERGY, REASON_HISTORY_CARD
from .recorder_churn_describe import keep_reason
from .recorder_churn_readers import recorder_reader_sources
from .recorder_churn_refs import async_energy_entity_ids, async_history_card_entity_ids
from .shapes import Item, RecipeResult


async def async_code_keeps(hass: HomeAssistant) -> dict[str, str]:
    """Entity ids code keeps without asking, each with its reason; the Energy reason wins."""
    keeps = dict.fromkeys(recorder_reader_sources(hass), REASON_DERIVED_SOURCE)
    keeps.update(dict.fromkeys(await async_energy_entity_ids(hass), REASON_ENERGY))
    return keeps


def veto_excludes(result: RecipeResult, reason_for: Callable[[Item], str | None]) -> None:
    """Move every exclude item reason_for gives a reason into keep, carrying that reason."""
    items = result["items"]
    left: list[Item] = []
    kept: list[Item] = []
    for item in items.get(OPTION_EXCLUDE, []):
        reason = reason_for(item)
        if reason is None:
            left.append(item)
        else:
            kept.append({**item, "reason": reason})
    if not kept:
        return
    items[OPTION_EXCLUDE] = left
    items.setdefault(OPTION_KEEP, []).extend(kept)
    for option in (OPTION_EXCLUDE, OPTION_KEEP):
        result["counts"][option] = len(items[option])


async def async_veto_on_restore(hass: HomeAssistant, result: RecipeResult) -> None:
    """Re-check the stored exclude items against today's code keeps and history cards, with no API call.

    An entity added to the Energy dashboard or a history card since the run
    moves to keep, so its card does not come back after a restart.
    """
    keeps = {**dict.fromkeys(await async_history_card_entity_ids(hass), REASON_HISTORY_CARD), **await async_code_keeps(hass)}
    registry = er.async_get(hass)

    def reason_for(item: Item) -> str | None:
        """The keep reason for the item's entity under its current entity id, else None."""
        entry = registry.async_get(str(item["registry_id"]))
        return keep_reason(entry.entity_id if entry else str(item["entity_id"]), entry, keeps)

    veto_excludes(result, reason_for)
