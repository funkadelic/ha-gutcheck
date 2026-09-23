"""Recorder-backed unavailable duration: batched, restart-proof, with a last_changed fallback."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

from homeassistant.components.recorder import history
from homeassistant.const import ATTR_RESTORED, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.gutcheck.history import async_unavailable_since
from custom_components.gutcheck.recipes.health import HealthRecipe


async def test_recorder_off_returns_none(hass: HomeAssistant) -> None:
    """No recorder configured: the caller falls back to last_changed."""
    assert await async_unavailable_since(hass, ["sensor.a"]) is None


async def test_recorder_on_with_no_entities_returns_keep_days_and_empty_map(recorder_mock, hass: HomeAssistant) -> None:
    """An empty entity list still returns the retained-days window, with an empty map, not a query error."""
    keep_days, since_map = await async_unavailable_since(hass, [])
    assert keep_days == 10
    assert since_map == {}


async def test_recorder_on_reports_when_the_entity_last_left_unavailable(recorder_mock, hass: HomeAssistant, freezer) -> None:
    """The since_map reports the timestamp the entity last left an available state."""
    freezer.move_to("2026-01-01T00:00:00+00:00")
    hass.states.async_set("sensor.a", "on")
    await async_wait_recording_done(hass)

    freezer.move_to("2026-01-04T00:00:00+00:00")
    hass.states.async_set("sensor.a", STATE_UNAVAILABLE)
    await async_wait_recording_done(hass)

    freezer.move_to("2026-01-06T00:00:00+00:00")
    result = await async_unavailable_since(hass, ["sensor.a"])

    assert result is not None
    keep_days, since_map = result
    assert keep_days == 10
    assert since_map["sensor.a"] == datetime(2026, 1, 4, tzinfo=UTC)


async def test_recorder_on_reports_none_when_unavailable_for_the_whole_window(
    recorder_mock, hass: HomeAssistant, freezer
) -> None:
    """Never seen available within the retained window: None, so the caller reads 'beyond window'."""
    freezer.move_to("2026-01-01T00:00:00+00:00")
    hass.states.async_set("sensor.a", STATE_UNAVAILABLE)
    await async_wait_recording_done(hass)

    freezer.move_to("2026-01-13T00:00:00+00:00")  # 12 days later, past the 10-day keep window
    keep_days, since_map = await async_unavailable_since(hass, ["sensor.a"])

    assert keep_days == 10
    assert since_map["sensor.a"] is None


async def test_a_restart_reset_does_not_look_like_a_recovery(recorder_mock, hass: HomeAssistant, freezer) -> None:
    """Removing and re-writing the entity (a restart) must not reset the outage clock."""
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("sensor", "test", "unique_restart")

    freezer.move_to("2026-01-01T00:00:00+00:00")
    hass.states.async_set(entry.entity_id, STATE_UNAVAILABLE)
    await async_wait_recording_done(hass)

    freezer.move_to("2026-01-12T00:00:00+00:00")  # t0 + 11 days
    hass.states.async_remove(entry.entity_id)
    entry.write_unavailable_state(hass)
    await async_wait_recording_done(hass)

    freezer.move_to("2026-01-13T00:00:00+00:00")  # t0 + 12 days
    keep_days, since_map = await async_unavailable_since(hass, [entry.entity_id])

    assert keep_days == 10
    assert since_map[entry.entity_id] is None

    state = hass.states.get(entry.entity_id)
    assert state is not None
    assert state.attributes.get(ATTR_RESTORED) is True


async def test_recorder_on_but_no_rows_for_an_entity_is_left_out_of_the_map(recorder_mock, hass: HomeAssistant) -> None:
    """An entity with no recorder rows is left out of the map; the caller falls back per-entity."""
    hass.states.async_set("sensor.tracked", "on")
    await async_wait_recording_done(hass)

    keep_days, since_map = await async_unavailable_since(hass, ["sensor.tracked", "sensor.never_recorded"])

    assert keep_days == 10
    assert "sensor.never_recorded" not in since_map


async def test_dict_shaped_rows_are_skipped(recorder_mock, hass: HomeAssistant) -> None:
    """Defensive: a dict-shaped row (never returned with these kwargs) is skipped, not crashed on."""
    hass.states.async_set("sensor.a", STATE_UNAVAILABLE)
    await async_wait_recording_done(hass)

    with patch.object(history, "get_significant_states", return_value={"sensor.a": [{"state": "unavailable"}]}):
        keep_days, since_map = await async_unavailable_since(hass, ["sensor.a"])

    assert keep_days == 10
    assert "sensor.a" not in since_map


async def test_latest_row_not_unavailable_is_left_out_of_the_map(recorder_mock, hass: HomeAssistant) -> None:
    """An outage the recorder has not written yet falls back to last_changed instead of reading as the whole window."""
    hass.states.async_set("sensor.a", STATE_UNAVAILABLE)
    await async_wait_recording_done(hass)

    with patch.object(history, "get_significant_states", return_value={"sensor.a": [State("sensor.a", "20")]}):
        _, since_map = await async_unavailable_since(hass, ["sensor.a"])

    assert "sensor.a" not in since_map


async def test_one_query_for_every_entity_in_one_run(recorder_mock, hass: HomeAssistant) -> None:
    """Every entity is batched into a single recorder query, never one query per entity."""
    hass.states.async_set("sensor.a", STATE_UNAVAILABLE)
    hass.states.async_set("sensor.b", STATE_UNAVAILABLE)
    await async_wait_recording_done(hass)

    with patch.object(history, "get_significant_states", wraps=history.get_significant_states) as mock_query:
        await async_unavailable_since(hass, ["sensor.a", "sensor.b"])

    assert mock_query.call_count == 1


async def test_recipe_uses_longer_than_keep_days_when_beyond_the_window(recorder_mock, hass: HomeAssistant, freezer) -> None:
    """Beyond the recorder's retained window, the recipe reports a "longer than" lower bound, not a guess."""
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("sensor", "test", "unique_beyond")

    freezer.move_to("2026-01-01T00:00:00+00:00")
    hass.states.async_set(entry.entity_id, STATE_UNAVAILABLE)
    await async_wait_recording_done(hass)

    freezer.move_to("2026-01-13T00:00:00+00:00")  # 12 days later, past the 10-day keep window
    batch = await HealthRecipe(critical_label=None).async_prepare(hass)

    assert batch.state["entities"][0]["unavailable_for"] == "longer than 1 week"


async def test_recipe_uses_recorder_duration_within_a_week(recorder_mock, hass: HomeAssistant, freezer) -> None:
    """End-to-end: the recipe's unavailable_for field reflects the recorder-derived duration."""
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("sensor", "test", "unique_week")

    freezer.move_to("2026-01-01T00:00:00+00:00")
    hass.states.async_set(entry.entity_id, "on")
    await async_wait_recording_done(hass)

    freezer.move_to("2026-01-04T00:00:00+00:00")
    hass.states.async_set(entry.entity_id, STATE_UNAVAILABLE)
    await async_wait_recording_done(hass)

    freezer.move_to("2026-01-06T00:00:00+00:00")
    batch = await HealthRecipe(critical_label=None).async_prepare(hass)

    assert batch.state["entities"][0]["unavailable_for"] == "1 to 6 days"


async def test_recipe_uses_recorder_duration_under_a_day(recorder_mock, hass: HomeAssistant, freezer) -> None:
    """A recorder-derived duration under 24 hours buckets to "less than a day"."""
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("sensor", "test", "unique_hour")

    freezer.move_to("2026-01-01T00:00:00+00:00")
    hass.states.async_set(entry.entity_id, "on")
    await async_wait_recording_done(hass)

    freezer.move_to("2026-01-01T01:00:00+00:00")
    hass.states.async_set(entry.entity_id, STATE_UNAVAILABLE)
    await async_wait_recording_done(hass)

    freezer.move_to("2026-01-01T05:00:00+00:00")
    batch = await HealthRecipe(critical_label=None).async_prepare(hass)

    assert batch.state["entities"][0]["unavailable_for"] == "less than a day"


async def test_recipe_falls_back_to_last_changed_without_a_recorder(hass: HomeAssistant, freezer) -> None:
    """With no recorder at all, the recipe falls back to bucketing by last_changed."""
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("sensor", "test", "unique_no_recorder")

    freezer.move_to("2026-01-01T00:00:00+00:00")
    hass.states.async_set(entry.entity_id, STATE_UNAVAILABLE)

    freezer.move_to("2026-01-04T00:00:00+00:00")
    batch = await HealthRecipe(critical_label=None).async_prepare(hass)

    assert batch.state["entities"][0]["unavailable_for"] == "longer than 1 day"


async def test_recipe_falls_back_to_unknown_when_recently_changed_without_a_recorder(hass: HomeAssistant, freezer) -> None:
    """With no recorder and a change under a day old, the fallback reports "unknown", not a false precision."""
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("sensor", "test", "unique_recent")

    freezer.move_to("2026-01-01T00:00:00+00:00")
    hass.states.async_set(entry.entity_id, STATE_UNAVAILABLE)

    freezer.move_to("2026-01-01T02:00:00+00:00")
    batch = await HealthRecipe(critical_label=None).async_prepare(hass)

    assert batch.state["entities"][0]["unavailable_for"] == "unknown"
