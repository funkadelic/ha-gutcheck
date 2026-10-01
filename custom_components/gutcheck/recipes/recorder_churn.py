"""Recorder suggestions recipe: one choice question per heaviest recorder writer."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed

from ..const import FAILED_RUN_RETRY, RECIPE_RECORDER_CHURN, RECORDER_CHURN_ISSUE_PREFIX
from ..models import Question
from .gate import gate_choice
from .recorder_churn_cards import sync_exclude_cards
from .recorder_churn_const import (
    CHURN_TOP_N,
    OPTION_EXCLUDE,
    OPTION_KEEP,
    OPTION_NOT_ASKED,
    REASON_HISTORY_CARD,
    REASON_LOWER_RANK,
    REASON_NO_UNIQUE_ID,
    RECORDER_CHURN_CHOICES,
    RECORDER_CHURN_CONFIDENCE_THRESHOLD,
    RECORDER_CHURN_CRITERIA,
    RECORDER_CHURN_INSTRUCTIONS,
    RECORDER_CHURN_OPTIONS,
)
from .recorder_churn_count import async_churn
from .recorder_churn_describe import describe, rank, subject
from .recorder_churn_refs import async_history_card_entity_ids
from .safety import SafetyRules
from .shapes import Batch, Item, RecipeResult

_LOGGER = logging.getLogger(__name__)


def _veto_dashboard_excludes(result: RecipeResult) -> None:
    """Move every exclude item a recorder-backed dashboard card shows into keep.

    Runs in async_act, before the coordinator saves the result, so a restore
    reads the corrected buckets and needs no second veto.
    """
    items = result["items"]
    shown = [item for item in items.get(OPTION_EXCLUDE, []) if item.get("on_dashboard")]
    if not shown:
        return
    items[OPTION_EXCLUDE] = [item for item in items[OPTION_EXCLUDE] if not item.get("on_dashboard")]
    items.setdefault(OPTION_KEEP, []).extend({**item, "reason": REASON_HISTORY_CARD} for item in shown)
    for option in (OPTION_EXCLUDE, OPTION_KEEP):
        result["counts"][option] = len(items[option])


class RecorderChurnRecipe:
    """Ranks entities by recorder writes per day and classifies each heavy one with one choice question."""

    recipe_id = RECIPE_RECORDER_CHURN
    options: tuple[str, ...] = RECORDER_CHURN_OPTIONS
    issue_prefix = RECORDER_CHURN_ISSUE_PREFIX
    # An unsure item carries no choice, so the entity id is the one field
    # every stored item has, carried ones included.
    stored_item_keys: frozenset[str] = frozenset({"entity_id"})

    def __init__(self, critical_label: str | None) -> None:
        """Build the shared safety guard."""
        self._safety = SafetyRules(critical_label)

    def gate(self, answer: object) -> str | None:
        """Gate one answer through the choice gate over exclude, throttle and keep.

        OPTION_NONE is in the criteria only, so a confident "none of these"
        lands in unsure.
        """
        return gate_choice(answer, RECORDER_CHURN_CHOICES, RECORDER_CHURN_CONFIDENCE_THRESHOLD)

    async def async_prepare(self, hass: HomeAssistant, previous: RecipeResult | None = None, *, force: bool = False) -> Batch:
        """Count, rank and ask one choice question per ranked entity.

        previous and force are unused, since nothing carries forward between
        runs. With no recorder or a failed count the run fails, so the sensor
        keeps its last report.
        """
        churn = await async_churn(hass)
        if churn is None:
            raise UpdateFailed("recorder history unavailable", retry_after=FAILED_RUN_RETRY.total_seconds())
        window_days, counts = churn
        ranked = rank(hass, self._safety, counts, window_days)
        on_dashboard = await async_history_card_entity_ids(hass)

        entities: list[dict[str, Any]] = []
        questions: dict[str, Question] = {}
        subjects: dict[str, Item] = {}
        carried: dict[str, list[Item]] = {OPTION_NOT_ASKED: []}
        for item in ranked:
            # No card can be keyed without a registry id, and only the top N are asked.
            if item.entry is None or len(entities) >= CHURN_TOP_N:
                reason = REASON_NO_UNIQUE_ID if item.entry is None else REASON_LOWER_RANK
                carried[OPTION_NOT_ASKED].append({**subject(item), "reason": reason})
                continue
            state_item, asked = describe(hass, item.entry, item.per_day, on_dashboard=item.entity_id in on_dashboard)
            index = len(entities)
            entities.append(state_item)
            question_id = f"r{index}"
            questions[question_id] = {
                "type": "choice",
                "instructions": RECORDER_CHURN_INSTRUCTIONS.format(index=index),
                "criteria": RECORDER_CHURN_CRITERIA,
            }
            subjects[question_id] = asked

        _LOGGER.debug(
            "recorder suggestions counted=%s ranked=%s asked=%s not_asked=%s window_days=%s",
            len(counts),
            len(ranked),
            len(entities),
            len(carried[OPTION_NOT_ASKED]),
            window_days,
        )
        return Batch(
            state={"entities": entities},
            questions=questions,
            subjects=subjects,
            carried=carried,
            list_key="entities",
            template=RECORDER_CHURN_INSTRUCTIONS,
        )

    async def async_act(self, hass: HomeAssistant, result: RecipeResult) -> None:
        """Veto exclude answers for dashboard entities, sync one advisory card per exclude answer, and log counts."""
        _veto_dashboard_excludes(result)
        sync_exclude_cards(hass, result["items"].get(OPTION_EXCLUDE, []))
        _LOGGER.debug("recorder suggestions run complete, counts=%s", result["counts"])

    async def restore(self, hass: HomeAssistant, result: RecipeResult) -> None:
        """Re-sync the cards from the stored result with no API call.

        A non-persistent card loads inactive after a restart until a sync
        re-creates it.
        """
        sync_exclude_cards(hass, result["items"].get(OPTION_EXCLUDE, []))
