"""Config entry triage edges: the reauth count, the failing-for word in the subject, empty results and the tracker."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, flush_store

from custom_components.gutcheck.const import CONFIG_ENTRY_ISSUE_PREFIX, DOMAIN, RECIPE_CONFIG_ENTRIES
from custom_components.gutcheck.recipes.config_entries import ConfigEntryRecipe
from custom_components.gutcheck.recipes.config_entry_repairs import ConfigEntryIssueTracker, _wanted_issues

from .conftest import health_result


async def test_prepare_counts_each_reauth_skipped_entry(hass: HomeAssistant, failing_entry: Any, caplog) -> None:
    """Two entries Home Assistant is already reauthenticating are skipped and logged as two, with the third asked."""
    caplog.set_level(logging.DEBUG, logger="custom_components.gutcheck.recipes.config_entries")
    for index in range(2):
        await failing_entry(
            f"cloud_hub{index}",
            ConfigEntryAuthFailed("invalid credentials"),
            title="Hub",
            entry_id=f"a_reauth{index}",
            reauth=True,
        )
    await failing_entry("dead_hub", ConfigEntryError("gone"), title="Dead", entry_id="b_dead")

    batch = await ConfigEntryRecipe().async_prepare(hass)

    assert "selected=3 asked=1 reauth_skipped=2" in caplog.text
    assert len(batch.subjects) == 1


async def test_the_subject_carries_the_failing_for_word_from_the_first_seen_time(hass: HomeAssistant, failing_entry: Any) -> None:
    """An entry first seen ten days ago is recorded as failing for longer than a week."""
    entry = await failing_entry("dead_hub", ConfigEntryError("gone"), title="Dead", entry_id="b_dead")
    seen = (dt_util.utcnow() - timedelta(days=10)).isoformat()
    previous = health_result({"dead": [{"entry_id": entry.entry_id, "first_seen": seen}]})

    batch = await ConfigEntryRecipe().async_prepare(hass, previous)

    assert next(iter(batch.subjects.values()))["failing_for"] == "longer than 1 week"


async def test_acting_on_and_restoring_a_result_with_no_buckets_syncs_nothing(hass: HomeAssistant) -> None:
    """A result that lacks the reauth and dead buckets is treated as empty for both."""
    recipe = ConfigEntryRecipe()
    result = health_result({})
    result["last_run"] = dt_util.utcnow().isoformat()

    await recipe.async_act(hass, result)
    await recipe.restore(hass, result)

    recipe.shutdown()


async def test_a_reauth_in_progress_item_does_not_hide_the_ones_after_it(hass: HomeAssistant, failing_entry: Any) -> None:
    """The item for an entry Home Assistant is reauthenticating is passed over; the next still gets its card."""
    entry = await failing_entry("dead_hub", ConfigEntryError("gone"), title="Dead", entry_id="b_dead")
    items = [{"entry_id": "a_other", "reauth_in_progress": True}, {"entry_id": entry.entry_id, "reason": "gone"}]

    assert list(_wanted_issues(hass, items)) == [f"{CONFIG_ENTRY_ISSUE_PREFIX}{entry.entry_id}"]


async def test_the_tracker_can_be_shut_down_twice(hass: HomeAssistant, failing_entry: Any) -> None:
    """Stopping the recovery watch again after it was cleared does nothing and does not fail."""
    entry = await failing_entry("dead_hub", ConfigEntryError("gone"), title="Dead", entry_id="b_dead")
    tracker = ConfigEntryIssueTracker()

    tracker.sync(hass, [], [{"entry_id": entry.entry_id, "reason": "gone"}], [])
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{CONFIG_ENTRY_ISSUE_PREFIX}{entry.entry_id}") is not None
    tracker.watch(hass, {entry.entry_id}, lambda: None)
    tracker.shutdown()
    tracker.shutdown()


async def test_a_run_drops_an_entry_that_loaded_while_its_question_was_in_flight(hass: HomeAssistant, failing_entry: Any) -> None:
    """An item whose entry is already loaded when the run acts leaves the result instead of waiting for the next run."""
    entry = await failing_entry("late_hub", None, title="Late", entry_id="late_entry")
    recipe = ConfigEntryRecipe()
    result = health_result({"dead": [{"entry_id": entry.entry_id, "reason": "gone"}]})
    result["unsure"] = [{"entry_id": entry.entry_id}]
    result["last_run"] = dt_util.utcnow().isoformat()

    await recipe.async_act(hass, result)

    assert result["items"]["dead"] == []
    assert result["counts"]["dead"] == 0
    assert result["unsure"] == []
    recipe.shutdown()


async def test_the_tracker_reports_a_recovery_once_and_ignores_later_reloads(hass: HomeAssistant, failing_entry: Any) -> None:
    """A recovered entry is no longer watched, so reloading it does not report it again."""
    entry = await failing_entry("retry_hub", ConfigEntryNotReady("offline"), None, title="Retry", entry_id="retry_entry")
    calls: list[None] = []
    tracker = ConfigEntryIssueTracker()
    tracker.watch(hass, {entry.entry_id}, lambda: calls.append(None))

    for _ in range(2):
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    assert len(calls) == 1
    tracker.shutdown()


async def test_publishing_before_the_first_run_finishes_saves_nothing(
    hass: HomeAssistant, hass_storage: dict[str, Any], triage_entry: MockConfigEntry
) -> None:
    """With no result yet, an edit signal leaves the Store alone; the run's own save covers it."""
    triage_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(triage_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = triage_entry.runtime_data.coordinators[RECIPE_CONFIG_ENTRIES]
    coordinator.data = None  # type: ignore[assignment]
    hass_storage.clear()

    coordinator.async_publish()
    await flush_store(coordinator._store)

    assert hass_storage == {}
