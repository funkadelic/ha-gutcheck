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
from .recorder_churn_describe import Ranked, describe, keep_reason, rank, subject
from .recorder_churn_keep import async_code_keeps, async_veto_on_restore, veto_excludes
from .recorder_churn_refs import async_history_card_entity_ids, referenced
from .safety import SafetyRules
from .shapes import Batch, Item, RecipeResult

_LOGGER = logging.getLogger(__name__)


def _on_dashboard(item: Item) -> str | None:
    """The history card reason for an exclude answer whose entity a dashboard graphs, else None."""
    return REASON_HISTORY_CARD if item.get("on_dashboard") else None


def _placement(item: Ranked, keeps: dict[str, str], asked: int) -> tuple[str, str] | None:
    """The bucket and reason code gives an entity without asking, or None when it is to be asked.

    Order: a code keep (Energy dashboard, total state class), no unique id
    (no card can be keyed without one), then the top-N cap on entities asked so far.
    """
    reason = keep_reason(item.entity_id, item.entry, keeps)
    if reason is not None:
        return OPTION_KEEP, reason
    if item.entry is None:
        return OPTION_NOT_ASKED, REASON_NO_UNIQUE_ID
    if asked >= CHURN_TOP_N:
        return OPTION_NOT_ASKED, REASON_LOWER_RANK
    return None


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
        keeps = await async_code_keeps(hass)

        entities: list[dict[str, Any]] = []
        questions: dict[str, Question] = {}
        subjects: dict[str, Item] = {}
        carried: dict[str, list[Item]] = {OPTION_KEEP: [], OPTION_NOT_ASKED: []}
        for item in ranked:
            placement = _placement(item, keeps, len(entities))
            if placement is not None:
                option, reason = placement
                carried[option].append({**subject(item), "reason": reason})
                continue
            assert item.entry is not None  # _placement sends a missing entry to not_asked
            state_item, asked = describe(
                hass,
                item.entry,
                item.per_day,
                on_dashboard=item.entity_id in on_dashboard,
                referenced=referenced(hass, item.entity_id),
            )
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
            "recorder suggestions counted=%s ranked=%s asked=%s kept_in_code=%s not_asked=%s window_days=%s",
            len(counts),
            len(ranked),
            len(entities),
            len(carried[OPTION_KEEP]),
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
        # Before the coordinator saves the result, so a restore reads the corrected buckets.
        veto_excludes(result, _on_dashboard)
        sync_exclude_cards(hass, self._safety, result["items"].get(OPTION_EXCLUDE, []))
        _LOGGER.debug("recorder suggestions run complete, counts=%s", result["counts"])

    def _allowed(self, hass: HomeAssistant, item: Item) -> bool:
        """Whether a stored item's entity is still allowed; one with no registry id or no entry stays."""
        registry_id = item.get("registry_id")
        return not (registry_id and self._safety.excludes_entity_id(hass, str(registry_id)))

    async def restore(self, hass: HomeAssistant, result: RecipeResult) -> None:
        """Drop items that became critical, re-apply the code keeps, then re-sync the cards with no API call.

        An entity removed since stays listed and simply gets no card. A
        non-persistent card loads inactive after a restart until a sync
        re-creates it.
        """
        for option, items in result["items"].items():
            result["items"][option] = [item for item in items if self._allowed(hass, item)]
            result["counts"][option] = len(result["items"][option])
        result["unsure"] = [item for item in result["unsure"] if self._allowed(hass, item)]
        await async_veto_on_restore(hass, result)
        sync_exclude_cards(hass, self._safety, result["items"].get(OPTION_EXCLUDE, []))
