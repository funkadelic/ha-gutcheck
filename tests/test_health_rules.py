"""Timeline and boundary tests for the code-decided leftover rule."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.components.recorder import history
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import (
    DOMAIN,
    HEALTH_ISSUE_PREFIX,
    OPTION_SAFE_TO_REMOVE,
    OPTION_WORTH_FIXING,
)
from custom_components.gutcheck.recipes.health import HealthRecipe

from .conftest import (
    api_response,
    choice_answer,
    health_sensor_entity_id,
    posted_bodies,
    register_jev_responses,
    register_unavailable_entity,
)

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_WELL_PAST_LEFTOVER = _T0 + timedelta(days=41)


async def test_leftover_carries_forward_while_the_ordinary_entity_is_asked(
    hass: HomeAssistant,
    freezer: Any,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A long-gone leftover is decided in code; the ordinary entity is still asked, in one POST."""
    hass.set_state(CoreState.not_running)
    registry = er.async_get(hass)
    freezer.move_to(_T0)
    leftover = registry.async_get_or_create("sensor", "test", "unique_leftover")
    leftover.write_unavailable_state(hass)
    ordinary_id = register_unavailable_entity(hass, "unique_ordinary")

    freezer.move_to(_WELL_PAST_LEFTOVER)
    register_jev_responses(aioclient_mock, [api_response({"e0": choice_answer(OPTION_WORTH_FIXING, 0.9)})])
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done(wait_background_tasks=True)

    bodies = posted_bodies(aioclient_mock)
    assert len(bodies) == 1
    body = bodies[0]
    assert set(body["questions"]) == {"e0"}
    assert body["state"]["entities"][0]["restored"] is False

    state = hass.states.get(health_sensor_entity_id(hass, mock_config_entry))
    assert state is not None
    assert state.state == "2"
    safe_to_remove = state.attributes["items"][OPTION_SAFE_TO_REMOVE]
    assert [item["entity_id"] for item in safe_to_remove] == [leftover.entity_id]
    assert "confidence" not in safe_to_remove[0]
    worth_fixing = state.attributes["items"][OPTION_WORTH_FIXING]
    assert [item["entity_id"] for item in worth_fixing] == [ordinary_id]
    assert worth_fixing[0]["confidence"] == 0.9

    issues = ir.async_get(hass)
    assert issues.async_get_issue(DOMAIN, f"{HEALTH_ISSUE_PREFIX}{leftover.id}") is None
    assert registry.async_get(leftover.entity_id) is not None


async def test_all_leftover_run_sends_nothing(
    hass: HomeAssistant,
    freezer: Any,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A run where every unavailable entity is a code-decided leftover sends no request at all."""
    hass.set_state(CoreState.not_running)
    registry = er.async_get(hass)
    freezer.move_to(_T0)
    leftover = registry.async_get_or_create("sensor", "test", "unique_only_leftover")
    leftover.write_unavailable_state(hass)

    freezer.move_to(_WELL_PAST_LEFTOVER)
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert posted_bodies(aioclient_mock) == []
    state = hass.states.get(health_sensor_entity_id(hass, mock_config_entry))
    assert state is not None
    assert state.state == "1"
    assert [item["entity_id"] for item in state.attributes["items"][OPTION_SAFE_TO_REMOVE]] == [leftover.entity_id]

    budget = mock_config_entry.runtime_data.budget
    assert budget.spent_today == 0


@pytest.mark.parametrize(("days_gone", "expect_carried"), [(30, False), (31, True)])
async def test_last_changed_boundary(hass: HomeAssistant, freezer: Any, days_gone: int, expect_carried: bool) -> None:
    """Without a recorder, 30 days gone is still asked; 31 days gone carries with no question."""
    freezer.move_to(_T0)
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("sensor", "test", "unique_boundary")
    entry.write_unavailable_state(hass)

    freezer.move_to(_T0 + timedelta(days=days_gone))
    batch = await HealthRecipe(critical_label=None).async_prepare(hass)

    if expect_carried:
        assert batch.subjects == {}
        assert len(batch.carried[OPTION_SAFE_TO_REMOVE]) == 1
    else:
        assert len(batch.subjects) == 1
        assert batch.carried.get(OPTION_SAFE_TO_REMOVE, []) == []


@pytest.mark.parametrize(
    ("recorder_config", "days_later", "expect_carried", "expected_text"),
    [
        ({"purge_keep_days": 31}, 41, True, "longer than 31 days"),
        ({"purge_keep_days": 10}, 41, True, "longer than 10 days"),
        ({"purge_keep_days": 60}, 41, True, "more than 4 weeks"),
        ({"purge_keep_days": 60}, 30, False, "more than 4 weeks"),
        ({"purge_keep_days": 10}, 5, False, "1 to 6 days"),
    ],
)
async def test_recorder_window_gates_the_leftover_rule(
    recorder_mock: Any,
    hass: HomeAssistant,
    freezer: Any,
    recorder_config: dict[str, Any],
    days_later: int,
    expect_carried: bool,
    expected_text: str,
) -> None:
    """The rule needs HEALTH_LEFTOVER_DAYS, or the retention window if shorter; one begun inside it counts from its start."""
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("sensor", "test", "unique_recorder_leftover")
    freezer.move_to(_T0)
    entry.write_unavailable_state(hass)
    await async_wait_recording_done(hass)

    freezer.move_to(_T0 + timedelta(days=days_later))
    batch = await HealthRecipe(critical_label=None).async_prepare(hass)

    if expect_carried:
        assert batch.subjects == {}
        assert batch.carried[OPTION_SAFE_TO_REMOVE][0]["unavailable_for"] == expected_text
    else:
        assert len(batch.subjects) == 1
        subject = next(iter(batch.subjects.values()))
        assert subject["unavailable_for"] == expected_text


@pytest.mark.parametrize("recorder_config", [{"purge_keep_days": 10}])
@pytest.mark.parametrize(("rows", "days_gone"), [([], 12), ([State("sensor.test_unique_undated", "20")], 0)])
async def test_retention_cap_needs_a_recorder_dated_outage(
    recorder_mock: Any,
    hass: HomeAssistant,
    freezer: Any,
    recorder_config: dict[str, Any],
    rows: list[State],
    days_gone: int,
) -> None:
    """With no retained unavailable row, the 31 days count from last_changed even under a 10-day recorder window."""
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("sensor", "test", "unique_undated")
    freezer.move_to(_T0)
    entry.write_unavailable_state(hass)
    await async_wait_recording_done(hass)

    freezer.move_to(_T0 + timedelta(days=days_gone))
    with patch.object(history, "get_significant_states", return_value={entry.entity_id: rows} if rows else {}):
        batch = await HealthRecipe(critical_label=None).async_prepare(hass)

    assert len(batch.subjects) == 1
    assert batch.carried.get(OPTION_SAFE_TO_REMOVE, []) == []


@pytest.mark.parametrize(
    ("state", "expect_carried"),
    [
        (ConfigEntryState.LOADED, True),
        (ConfigEntryState.SETUP_RETRY, False),
        (ConfigEntryState.SETUP_ERROR, False),
        (ConfigEntryState.NOT_LOADED, False),
    ],
)
async def test_owning_entry_state_gates_the_leftover_rule(
    hass: HomeAssistant, freezer: Any, state: ConfigEntryState, expect_carried: bool
) -> None:
    """A leftover whose owning integration is not loaded is still asked, never decided by the rule."""
    owning_entry = MockConfigEntry(domain="test", state=state)
    owning_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("sensor", "test", "unique_owned", config_entry=owning_entry)

    freezer.move_to(_T0)
    entry.write_unavailable_state(hass)
    freezer.move_to(_WELL_PAST_LEFTOVER)

    batch = await HealthRecipe(critical_label=None).async_prepare(hass)

    if expect_carried:
        assert batch.subjects == {}
        assert len(batch.carried[OPTION_SAFE_TO_REMOVE]) == 1
    else:
        assert len(batch.subjects) == 1
        assert batch.carried.get(OPTION_SAFE_TO_REMOVE, []) == []


async def test_restored_leftover_lock_and_critical_sensor_are_neither_asked_nor_carried(
    hass: HomeAssistant, freezer: Any
) -> None:
    """A restored, long-gone lock and a restored, long-gone critical-labelled sensor never reach the rule."""
    registry = er.async_get(hass)
    freezer.move_to(_T0)
    lock = registry.async_get_or_create("lock", "test", "unique_leftover_lock")
    lock.write_unavailable_state(hass)
    critical = registry.async_get_or_create("sensor", "test", "unique_leftover_critical")
    registry.async_update_entity(critical.entity_id, labels={"critical"})
    critical.write_unavailable_state(hass)

    freezer.move_to(_WELL_PAST_LEFTOVER)
    batch = await HealthRecipe(critical_label="critical").async_prepare(hass)

    assert batch.subjects == {}
    assert batch.carried.get(OPTION_SAFE_TO_REMOVE, []) == []
