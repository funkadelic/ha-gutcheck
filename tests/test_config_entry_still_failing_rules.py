"""Rules for the still-failing card: the week boundary, the reason it shows, and restore."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import (
    CONFIG_ENTRY_ISSUE_PREFIX,
    DOMAIN,
    ISSUE_CONFIG_ENTRY_STILL_FAILING,
    OPTION_NONE,
    RECIPE_CONFIG_ENTRIES,
    STORE_VERSION,
)
from custom_components.gutcheck.recipes.config_entry_const import (
    CONFIG_ENTRY_NO_REASON,
    CONFIG_ENTRY_OPTIONS,
    CONFIG_ENTRY_UNSURE_CARD_AFTER,
    OPTION_DEAD,
)
from custom_components.gutcheck.recipes.config_entry_describe import long_unsure
from custom_components.gutcheck.recipes.config_entry_repairs import ConfigEntryIssueTracker
from custom_components.gutcheck.recipes.shapes import Item, RecipeResult, recipe_store_key
from custom_components.gutcheck.repairs import sanitize_placeholder

from .conftest import (
    api_response,
    area_answer,
    posted_bodies,
    press_triage_run,
    register_jev_responses,
)

LAST_RUN = datetime.fromisoformat("2026-01-08T00:00:00+00:00")
STORE_KEY = recipe_store_key(RECIPE_CONFIG_ENTRIES)


def _result(unsure: list[Item], buckets: dict[str, list[Item]] | None = None) -> RecipeResult:
    """A hand-built result run at LAST_RUN."""
    return {
        "last_run": LAST_RUN.isoformat(),
        "counts": {},
        "items": buckets or {},
        "unsure": unsure,
        "last_payload": None,
    }


def _item(first_seen: object, **extra: object) -> Item:
    """One unsure item with a reason, unless extra overrides it."""
    return {"entry_id": "e", "first_seen": first_seen, "reason": "offline", **extra}


WEEK_AGO = (LAST_RUN - CONFIG_ENTRY_UNSURE_CARD_AFTER).isoformat()
JUST_UNDER = (LAST_RUN - CONFIG_ENTRY_UNSURE_CARD_AFTER + timedelta(seconds=1)).isoformat()


@pytest.mark.parametrize(
    ("item", "qualifies"),
    [
        (_item(WEEK_AGO), True),
        (_item(JUST_UNDER), False),
        (_item(WEEK_AGO, reason=None), True),
        ({"entry_id": "e", "first_seen": WEEK_AGO}, False),
        ({"entry_id": "e", "reason": "x"}, False),
        (_item(12345), False),
        (_item("not a date"), False),
    ],
    ids=[
        "exactly-a-week",
        "a-second-short",
        "no-reason-reported",
        "stored-before-reason",
        "no-first-seen",
        "non-string",
        "unparseable",
    ],
)
def test_only_an_unsure_item_a_full_week_old_with_a_stored_reason_qualifies(item: Item, qualifies: bool) -> None:
    """The cutoff is inclusive, and items lacking a usable first_seen or the reason key never qualify."""
    assert long_unsure(_result([item])) == ([item] if qualifies else [])


def test_an_old_item_in_a_verdict_bucket_is_never_selected() -> None:
    """Only the unsure list feeds the card; verdict buckets are ignored."""
    assert long_unsure(_result([], {OPTION_DEAD: [_item(WEEK_AGO)]})) == []


async def test_the_card_shows_the_reason_escaped_and_a_missing_reason_as_the_fixed_word(
    hass: HomeAssistant, failing_entry: Any
) -> None:
    """A markdown link, backtick, angle brackets and whitespace runs are sanitized; no reason reads as the fallback."""
    messy = "see [x](https://evil.example) `code`  <b>bold</b>\n\n  end"
    await failing_entry("messy_hub", ConfigEntryError("offline"), title="Messy", entry_id="messy_entry")
    await failing_entry("quiet_hub", ConfigEntryError("offline"), title="Quiet", entry_id="quiet_entry")
    tracker = ConfigEntryIssueTracker()
    tracker.sync(
        hass,
        [],
        [],
        [
            {"entry_id": "messy_entry", "reason": messy},
            {"entry_id": "quiet_entry", "reason": None},
        ],
    )
    registry = ir.async_get(hass)
    messy_issue = registry.async_get_issue(DOMAIN, f"{CONFIG_ENTRY_ISSUE_PREFIX}messy_entry")
    quiet_issue = registry.async_get_issue(DOMAIN, f"{CONFIG_ENTRY_ISSUE_PREFIX}quiet_entry")
    assert messy_issue is not None
    assert messy_issue.translation_placeholders is not None
    assert messy_issue.translation_placeholders["reason"] == sanitize_placeholder(messy)
    assert "](" not in messy_issue.translation_placeholders["reason"].replace("\\]", "").replace("\\[", "")
    assert quiet_issue is not None
    assert quiet_issue.translation_placeholders is not None
    assert quiet_issue.translation_placeholders["reason"] == CONFIG_ENTRY_NO_REASON
    tracker.shutdown()


async def test_the_card_shows_the_redacted_reason_not_the_original(
    hass: HomeAssistant,
    freezer: Any,
    aioclient_mock: AiohttpClientMocker,
    triage_entry: MockConfigEntry,
    failing_entry: Any,
) -> None:
    """An email and a URL password in the error never reach the card."""
    freezer.move_to("2026-01-01T00:00:00-08:00")
    error = "login for bob@example.com failed at https://user:hunter2@host.example/x?token=abc"
    await failing_entry("leaky_hub", ConfigEntryError(error), title="Leaky", entry_id="leaky_entry")
    unsure = api_response({"c0": area_answer(OPTION_NONE, 0.8, CONFIG_ENTRY_OPTIONS)})
    register_jev_responses(aioclient_mock, [unsure, unsure])
    triage_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(triage_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    freezer.tick(CONFIG_ENTRY_UNSURE_CARD_AFTER)
    await press_triage_run(hass, triage_entry)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"{CONFIG_ENTRY_ISSUE_PREFIX}leaky_entry")
    assert issue is not None
    assert issue.translation_key == ISSUE_CONFIG_ENTRY_STILL_FAILING
    reason = (issue.translation_placeholders or {})["reason"]
    assert "bob@example.com" not in reason
    assert "hunter2" not in reason
    assert "token=abc" not in reason
    assert "email" in reason
    assert "redacted" in reason


async def test_restore_raises_the_card_for_a_week_old_unsure_item_with_a_reason_and_skips_a_pre_reason_one(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    aioclient_mock: AiohttpClientMocker,
    triage_entry: MockConfigEntry,
    failing_entry: Any,
) -> None:
    """A stored item with a reason gets its card at setup with no POST; one stored without the key waits for a run."""
    await failing_entry("new_hub", ConfigEntryError("offline"), title="New", entry_id="new_entry")
    await failing_entry("old_hub", ConfigEntryError("offline"), title="Old", entry_id="old_entry")
    now = dt_util.utcnow()
    seen = (now - timedelta(days=8)).isoformat()
    base = {"integration": "x", "title": "X", "first_seen": seen, "failing_for": "longer than 1 week"}
    hass_storage[STORE_KEY] = {
        "version": STORE_VERSION,
        "minor_version": 1,
        "key": STORE_KEY,
        "data": {
            "last_run": now.isoformat(),
            "counts": {option: 0 for option in CONFIG_ENTRY_OPTIONS},
            "items": {option: [] for option in CONFIG_ENTRY_OPTIONS},
            "unsure": [
                {**base, "entry_id": "new_entry", "reason": "kept failing"},
                {**base, "entry_id": "old_entry"},
            ],
            "last_payload": None,
        },
    }
    triage_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(triage_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert posted_bodies(aioclient_mock) == []
    registry = ir.async_get(hass)
    card = registry.async_get_issue(DOMAIN, f"{CONFIG_ENTRY_ISSUE_PREFIX}new_entry")
    assert card is not None
    assert (card.translation_placeholders or {})["reason"] == "kept failing"
    assert registry.async_get_issue(DOMAIN, f"{CONFIG_ENTRY_ISSUE_PREFIX}old_entry") is None
