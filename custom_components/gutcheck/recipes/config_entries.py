"""Stuck config entry triage recipe: one choice question per entry stuck in setup_retry or setup_error."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from ..const import CONFIG_ENTRY_ISSUE_PREFIX, RECIPE_CONFIG_ENTRIES
from ..models import Question
from .config_entry_const import (
    CONFIG_ENTRY_CONFIDENCE_THRESHOLD,
    CONFIG_ENTRY_CRITERIA,
    CONFIG_ENTRY_INSTRUCTIONS,
    CONFIG_ENTRY_OPTIONS,
    OPTION_DEAD,
    OPTION_NEEDS_REAUTH,
)
from .config_entry_describe import describe, describe_subject, failing_for, first_seen, reauth_active, resolved, select
from .config_entry_repairs import ConfigEntryIssueTracker
from .gate import gate_choice
from .shapes import Batch, Item, RecipeResult

_LOGGER = logging.getLogger(__name__)


class ConfigEntryRecipe:
    """Selects config entries stuck in setup_retry or setup_error and asks one choice question each."""

    recipe_id = RECIPE_CONFIG_ENTRIES
    options: tuple[str, ...] = CONFIG_ENTRY_OPTIONS
    issue_prefix = CONFIG_ENTRY_ISSUE_PREFIX
    stored_item_keys: frozenset[str] = frozenset({"entry_id"})

    def __init__(self) -> None:
        """Build the Repairs issue tracker."""
        self._issues = ConfigEntryIssueTracker()

    def gate(self, answer: object) -> str | None:
        """Gate one answer through the choice gate at this recipe's own threshold."""
        return gate_choice(answer, CONFIG_ENTRY_OPTIONS, CONFIG_ENTRY_CONFIDENCE_THRESHOLD)

    async def async_prepare(self, hass: HomeAssistant, previous: RecipeResult | None = None, *, force: bool = False) -> Batch:
        """Select every stuck entry and ask one choice question about each.

        An entry Home Assistant is already reauthenticating is not asked: it
        is carried straight into needs_reauth. Ignores force, since no answer
        is carried forward between runs.
        """
        now = dt_util.utcnow()
        selected = select(hass)
        entries: list[Item] = []
        questions: dict[str, Question] = {}
        subjects: dict[str, Item] = {}
        carried: dict[str, list[Item]] = {}
        reauth_skipped = 0
        for entry in selected:
            seen = first_seen(previous, entry.entry_id, now)
            duration = failing_for(seen, now)
            subject = describe_subject(entry, seen, duration)
            if reauth_active(hass, entry):
                reauth_skipped += 1
                # Set here, not through classify: a carried item bypasses classify entirely.
                carried.setdefault(OPTION_NEEDS_REAUTH, []).append({**subject, "reauth_in_progress": True, "confidence": 1.0})
                continue
            index = len(entries)
            entries.append(describe(entry, duration))
            question_id = f"c{index}"
            questions[question_id] = {
                "type": "choice",
                "instructions": CONFIG_ENTRY_INSTRUCTIONS.format(index=index),
                "criteria": CONFIG_ENTRY_CRITERIA,
            }
            subjects[question_id] = subject

        _LOGGER.debug("config entry triage selected=%s asked=%s reauth_skipped=%s", len(selected), len(entries), reauth_skipped)
        return Batch(
            state={"entries": entries},
            questions=questions,
            subjects=subjects,
            carried=carried,
            list_key="entries",
            template=CONFIG_ENTRY_INSTRUCTIONS,
        )

    async def async_act(self, hass: HomeAssistant, result: RecipeResult) -> None:
        """Sync both advisory card kinds and log counts."""
        self._issues.sync(hass, result["items"].get(OPTION_NEEDS_REAUTH, []), result["items"].get(OPTION_DEAD, []))
        _LOGGER.debug("config entry triage run complete, counts=%s", result["counts"])

    async def restore(self, hass: HomeAssistant, result: RecipeResult) -> None:
        """Drop any bucket item whose entry has recovered since the run, then re-sync both card kinds.

        A restore calls no API, so an entry removed, disabled or loaded since
        the run must still drop from every bucket, including unsure, rather
        than sitting stale until the next paid run notices. An entry Home
        Assistant has not set up yet at restore time is not resolved, so it
        keeps its item and its card; the recovery listener the sync re-arms
        clears it live if that entry then loads.
        """
        for option, items in result["items"].items():
            kept = [item for item in items if not resolved(hass, str(item["entry_id"]))]
            result["items"][option] = kept
            result["counts"][option] = len(kept)
        result["unsure"] = [item for item in result["unsure"] if not resolved(hass, str(item["entry_id"]))]
        self._issues.sync(hass, result["items"].get(OPTION_NEEDS_REAUTH, []), result["items"].get(OPTION_DEAD, []))

    def shutdown(self) -> None:
        """Cancel the recovery subscription, if any."""
        self._issues.shutdown()
