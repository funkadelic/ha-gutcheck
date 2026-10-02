"""Recorder suggestion edges: ranking, churn figures, vetoes with missing buckets, restore, and the failed-count retry."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util

from custom_components.gutcheck.const import FAILED_RUN_RETRY
from custom_components.gutcheck.recipes.recorder_churn import RecorderChurnRecipe
from custom_components.gutcheck.recipes.recorder_churn_cards import _churn, _wanted
from custom_components.gutcheck.recipes.recorder_churn_const import (
    CHURN_FLOOR_PER_DAY,
    OPTION_EXCLUDE,
    OPTION_KEEP,
    REASON_TOTAL_STATE_CLASS,
)
from custom_components.gutcheck.recipes.recorder_churn_describe import rank
from custom_components.gutcheck.recipes.recorder_churn_keep import async_veto_on_restore, veto_excludes
from custom_components.gutcheck.recipes.safety import SafetyRules

from .conftest import health_result, register_unit_sensor


async def test_ranking_floors_the_per_day_figure_and_passes_over_light_and_stateless_entities(hass: HomeAssistant) -> None:
    """A light entity and an unregistered one with no state, both ahead of a heavy one, do not stop the ranking."""
    hass.states.async_set("sensor.light", "1")
    hass.states.async_set("sensor.heavy", "1")
    window = 7
    counts = {"sensor.light": 10, "sensor.ghost": 5000 * window, "sensor.heavy": 1071 * window + 3}

    ranked = rank(hass, SafetyRules(None), counts, window)

    assert [(item.entity_id, item.per_day) for item in ranked] == [("sensor.heavy", 1071)]
    assert isinstance(ranked[0].per_day, int)
    assert ranked[0].per_day >= CHURN_FLOOR_PER_DAY


def test_changes_per_day_reads_numbers_and_treats_anything_else_as_zero() -> None:
    """A whole or fractional figure is cut to an int; a missing or text figure is 0."""
    assert _churn({"changes_per_day": 5}) == 5
    assert _churn({"changes_per_day": 7.9}) == 7
    assert _churn({"changes_per_day": "many"}) == 0
    assert _churn({}) == 0


async def test_ranking_skips_a_critical_entity_and_still_ranks_the_ones_after_it(hass: HomeAssistant) -> None:
    """An entity under the critical label is left out, and a heavier-counted order does not stop the scan."""
    critical = register_unit_sensor(hass, "critical", unit="W", labels=frozenset({"critical"}))
    other = register_unit_sensor(hass, "other", unit="W")
    counts = {critical.entity_id: 5000 * 7, other.entity_id: 2000 * 7}

    ranked = rank(hass, SafetyRules("critical"), counts, 7)

    assert [item.entity_id for item in ranked] == [other.entity_id]


async def test_a_card_is_not_wanted_for_an_entity_whose_device_carries_the_critical_label(hass: HomeAssistant) -> None:
    """The label sits on the device only, so the rules have to look it up through the registry."""
    sensor = register_unit_sensor(hass, "heavy", unit="W", device_name="Hub", device_labels=frozenset({"critical"}))
    item = {"entity_id": sensor.entity_id, "registry_id": sensor.id, "bucket": "heavy", "changes_per_day": 5000}

    assert _wanted(hass, SafetyRules("critical"), [item]) == {}
    assert len(_wanted(hass, SafetyRules(None), [item])) == 1


async def test_a_failed_count_retries_after_the_failed_run_delay(hass: HomeAssistant) -> None:
    """With no recorder the run fails and asks to be retried in an hour, not at the weekly time."""
    recipe = RecorderChurnRecipe(None)
    with pytest.raises(UpdateFailed) as err:
        await recipe.async_prepare(hass)

    assert err.value.retry_after == FAILED_RUN_RETRY.total_seconds()


async def test_acting_on_and_restoring_a_result_without_buckets_does_nothing(hass: HomeAssistant) -> None:
    """A result with no exclude or keep bucket syncs an empty set and creates no bucket."""
    recipe = RecorderChurnRecipe(None)
    result = health_result({})
    result["last_run"] = dt_util.utcnow().isoformat()

    await recipe.async_act(hass, result)
    await recipe.restore(hass, result)

    assert result["items"] == {}


async def test_restore_keeps_an_unsure_item_whose_entity_is_still_allowed(hass: HomeAssistant) -> None:
    """The unsure list is filtered through the same rule as every bucket, with the item and the hass it needs."""
    sensor = register_unit_sensor(hass, "heavy", unit="W")
    unsure = {"entity_id": sensor.entity_id, "registry_id": sensor.id, "bucket": "heavy"}
    result = health_result({})
    result["unsure"] = [unsure]

    await RecorderChurnRecipe(None).restore(hass, result)

    assert result["unsure"] == [unsure]


def test_a_veto_with_no_exclude_bucket_changes_nothing() -> None:
    """Nothing to move: the result is left exactly as it was, with no keep bucket made."""
    result = health_result({})

    veto_excludes(result, lambda _item: "reason")

    assert result["items"] == {}


def test_a_veto_creates_the_keep_bucket_when_it_is_missing() -> None:
    """An exclude item given a reason moves into a keep bucket made for it, carrying the reason."""
    result = health_result({OPTION_EXCLUDE: [{"entity_id": "sensor.a"}, {"entity_id": "sensor.b"}]})
    result["counts"] = {OPTION_EXCLUDE: 2}

    veto_excludes(result, lambda item: "why" if item["entity_id"] == "sensor.a" else None)

    assert result["items"] == {
        OPTION_EXCLUDE: [{"entity_id": "sensor.b"}],
        OPTION_KEEP: [{"entity_id": "sensor.a", "reason": "why"}],
    }
    assert result["counts"] == {OPTION_EXCLUDE: 1, OPTION_KEEP: 1}


async def test_a_restore_veto_moves_a_total_state_class_entity_to_keep(hass: HomeAssistant) -> None:
    """The stored exclude item's live registry entry is what decides the total state class keep."""
    sensor = register_unit_sensor(hass, "energy", unit="kWh", state_class="total_increasing")
    item = {"entity_id": sensor.entity_id, "registry_id": sensor.id, "bucket": "heavy"}
    result = health_result({OPTION_EXCLUDE: [item]})
    result["counts"] = {OPTION_EXCLUDE: 1}

    await async_veto_on_restore(hass, result)

    assert result["items"][OPTION_EXCLUDE] == []
    assert result["items"][OPTION_KEEP] == [{**item, "reason": REASON_TOTAL_STATE_CLASS}]
