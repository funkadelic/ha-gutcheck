"""Exclude card lifecycle: the cap, rejection memory, live checks, toggle, restart and removal."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import label_registry as lr
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import (
    CONF_CRITICAL_LABEL,
    CONF_DAILY_BUDGET,
    CONF_RECORDER_CHURN_ENABLED,
    DEFAULT_DAILY_BUDGET,
    DOMAIN,
    RECIPE_RECORDER_CHURN,
    RECORDER_CHURN_ISSUE_PREFIX,
)
from custom_components.gutcheck.recipes.recorder_churn_const import CHURN_FLOOR_PER_DAY, MAX_RECORDER_EXCLUDE_CARDS

from .conftest import (
    find_recipe_sensor,
    press_recipe_run,
    recipe_sensor_entity_id,
    recorder_churn_answer,
    register_jev_answers,
    register_unit_sensor,
    restart_config_entry,
)

_PATCH = "custom_components.gutcheck.recipes.recorder_churn.async_churn"
_WINDOW = 7
_HEAVIEST = 9_000


@pytest.fixture
def per_day() -> Iterator[dict[str, int]]:
    """The changes per day the patched count reports; a test edits it between runs."""
    counts: dict[str, int] = {}
    with patch(_PATCH, AsyncMock(side_effect=lambda _hass: (_WINDOW, {k: v * _WINDOW for k, v in counts.items()}))):
        yield counts


@pytest.fixture
def answers(aioclient_mock: AiohttpClientMocker) -> dict[str, Any]:
    """The answer the mocked API gives per question id; a test edits it between runs."""
    table: dict[str, Any] = {}
    register_jev_answers(aioclient_mock, table)
    return table


def _sensors(hass: HomeAssistant, per_day: dict[str, int], count: int) -> list[er.RegistryEntry]:
    """Register count heavy sensors, the first the heaviest, and report their churn."""
    sensors = [register_unit_sensor(hass, f"s{index:02d}", unit="W", name=f"Sensor {index}") for index in range(count)]
    per_day.update({sensor.entity_id: _HEAVIEST - index * 100 for index, sensor in enumerate(sensors)})
    return sensors


def _say(table: dict[str, Any], *replies: str | tuple[str, float]) -> None:
    """Answer r0, r1, ... in order: a choice at 0.9, or a (choice, confidence) pair."""
    table.clear()
    for index, reply in enumerate(replies):
        choice, confidence = (reply, 0.9) if isinstance(reply, str) else reply
        table[f"r{index}"] = recorder_churn_answer(choice, confidence)


def _issue(hass: HomeAssistant, sensor: er.RegistryEntry) -> ir.IssueEntry | None:
    """The exclude card for this entity, or None."""
    return ir.async_get(hass).async_get_issue(DOMAIN, f"{RECORDER_CHURN_ISSUE_PREFIX}{sensor.id}")


def _open_ids(hass: HomeAssistant) -> set[str]:
    """Registry ids of every unignored exclude card."""
    return {
        issue_id.removeprefix(RECORDER_CHURN_ISSUE_PREFIX)
        for (domain, issue_id), issue in ir.async_get(hass).issues.items()
        if domain == DOMAIN and issue_id.startswith(RECORDER_CHURN_ISSUE_PREFIX) and issue.dismissed_version is None
    }


def _ignore(hass: HomeAssistant, sensor: er.RegistryEntry) -> None:
    """Ignore this entity's card the way the Repairs panel does."""
    ir.async_ignore_issue(hass, DOMAIN, f"{RECORDER_CHURN_ISSUE_PREFIX}{sensor.id}", True)


def _assert_ignored(hass: HomeAssistant, sensor: er.RegistryEntry) -> None:
    """The entity's card exists and is still ignored."""
    issue = _issue(hass, sensor)
    assert issue is not None
    assert issue.dismissed_version is not None


def _ids(sensors: list[er.RegistryEntry]) -> set[str]:
    """The registry ids of these sensors."""
    return {sensor.id for sensor in sensors}


async def _start(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Set the entry up and let its first run finish."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)


def _state(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    """The recipe sensor's state."""
    state = hass.states.get(recipe_sensor_entity_id(hass, entry, RECIPE_RECORDER_CHURN))
    assert state is not None
    return state.state


async def test_twelve_exclude_answers_open_ten_cards_for_the_heaviest(
    hass: HomeAssistant, recorder_churn_entry: MockConfigEntry, per_day: dict[str, int], answers: dict[str, Any]
) -> None:
    """The ten highest-churn entities get a card, the lightest two none, and the sensor reads 10."""
    sensors = _sensors(hass, per_day, 12)
    _say(answers, *["exclude"] * 12)
    await _start(hass, recorder_churn_entry)
    assert _open_ids(hass) == _ids(sensors[:MAX_RECORDER_EXCLUDE_CARDS])
    assert _state(hass, recorder_churn_entry) == "10"


async def test_an_ignored_card_outlives_unsure_below_floor_and_exclude_runs_without_taking_a_slot(
    hass: HomeAssistant, recorder_churn_entry: MockConfigEntry, per_day: dict[str, int], answers: dict[str, Any]
) -> None:
    """The ignore is the only rejection record: every later week leaves it, and the other ten cards still open."""
    sensors = _sensors(hass, per_day, 12)
    ignored, others = sensors[0], sensors[1:]
    _say(answers, *["exclude"] * 12)
    await _start(hass, recorder_churn_entry)
    _ignore(hass, ignored)

    _say(answers, ("exclude", 0.3), *["exclude"] * 11)
    await press_recipe_run(hass, recorder_churn_entry, RECIPE_RECORDER_CHURN)
    _assert_ignored(hass, ignored)
    assert _open_ids(hass) == _ids(others[:MAX_RECORDER_EXCLUDE_CARDS])

    per_day[ignored.entity_id] = CHURN_FLOOR_PER_DAY - 1
    _say(answers, *["exclude"] * 11)
    await press_recipe_run(hass, recorder_churn_entry, RECIPE_RECORDER_CHURN)
    _assert_ignored(hass, ignored)
    assert _open_ids(hass) == _ids(others[:MAX_RECORDER_EXCLUDE_CARDS])

    per_day[ignored.entity_id] = _HEAVIEST
    _say(answers, *["exclude"] * 12)
    await press_recipe_run(hass, recorder_churn_entry, RECIPE_RECORDER_CHURN)
    _assert_ignored(hass, ignored)
    assert _open_ids(hass) == _ids(others[:MAX_RECORDER_EXCLUDE_CARDS])


async def test_an_open_card_goes_when_its_entity_is_throttle_or_below_the_floor(
    hass: HomeAssistant, recorder_churn_entry: MockConfigEntry, per_day: dict[str, int], answers: dict[str, Any]
) -> None:
    """The next run deletes a card the model no longer backs, and one whose entity went quiet."""
    sensors = _sensors(hass, per_day, 3)
    _say(answers, "exclude", "exclude", "exclude")
    await _start(hass, recorder_churn_entry)
    assert _open_ids(hass) == _ids(sensors)

    per_day[sensors[1].entity_id] = CHURN_FLOOR_PER_DAY - 1
    _say(answers, "throttle", "exclude")
    await press_recipe_run(hass, recorder_churn_entry, RECIPE_RECORDER_CHURN)
    assert _open_ids(hass) == {sensors[2].id}


async def test_an_ignored_card_for_a_removed_entity_is_swept(
    hass: HomeAssistant, recorder_churn_entry: MockConfigEntry, per_day: dict[str, int], answers: dict[str, Any]
) -> None:
    """Once the entity is gone from the registry there is nothing left to reject."""
    gone, stays = _sensors(hass, per_day, 2)
    _say(answers, "exclude", "exclude")
    await _start(hass, recorder_churn_entry)
    _ignore(hass, gone)
    er.async_get(hass).async_remove(gone.entity_id)

    _say(answers, "exclude")
    await press_recipe_run(hass, recorder_churn_entry, RECIPE_RECORDER_CHURN)
    assert _issue(hass, gone) is None
    assert _open_ids(hass) == {stays.id}


async def test_a_card_follows_a_rename_on_the_next_restore(
    hass: HomeAssistant, recorder_churn_entry: MockConfigEntry, per_day: dict[str, int], answers: dict[str, Any]
) -> None:
    """The card shows the entity's current entity id, not the one the run saw."""
    [sensor] = _sensors(hass, per_day, 1)
    _say(answers, "exclude")
    await _start(hass, recorder_churn_entry)
    er.async_get(hass).async_update_entity(sensor.entity_id, new_entity_id="sensor.renamed_writer")

    await restart_config_entry(hass, recorder_churn_entry)
    issue = _issue(hass, sensor)
    assert issue is not None
    assert issue.translation_placeholders is not None
    assert issue.translation_placeholders["entity_id"] == "sensor.renamed_writer"


async def test_an_entity_that_became_critical_loses_its_item_and_card_on_restore(
    hass: HomeAssistant, recorder_churn_entry: MockConfigEntry, per_day: dict[str, int], answers: dict[str, Any]
) -> None:
    """A label added after the run removes the entity from the report and its card at the next restore."""
    label = lr.async_get(hass).async_create("Critical")
    recorder_churn_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        recorder_churn_entry, options={**recorder_churn_entry.options, CONF_CRITICAL_LABEL: label.label_id}
    )
    labelled, plain = _sensors(hass, per_day, 2)
    _say(answers, "exclude", "exclude")
    assert await hass.config_entries.async_setup(recorder_churn_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _open_ids(hass) == _ids([labelled, plain])

    er.async_get(hass).async_update_entity(labelled.entity_id, labels={label.label_id})
    await restart_config_entry(hass, recorder_churn_entry)

    assert _open_ids(hass) == {plain.id}
    state = hass.states.get(recipe_sensor_entity_id(hass, recorder_churn_entry, RECIPE_RECORDER_CHURN))
    assert state is not None
    assert [item["entity_id"] for item in state.attributes["items"]["exclude"]] == [plain.entity_id]
    assert state.attributes["counts"]["exclude"] == 1


async def test_a_restart_reopens_cards_and_keeps_an_ignore(
    hass: HomeAssistant, recorder_churn_entry: MockConfigEntry, per_day: dict[str, int], answers: dict[str, Any]
) -> None:
    """After a restart the open card is active, the ignored one is still ignored, and the sensor counts open ones."""
    ignored, open_one = _sensors(hass, per_day, 2)
    _say(answers, "exclude", "exclude")
    await _start(hass, recorder_churn_entry)
    _ignore(hass, ignored)

    await restart_config_entry(hass, recorder_churn_entry)
    _assert_ignored(hass, ignored)
    issue = _issue(hass, open_one)
    assert issue is not None
    assert issue.active
    assert _state(hass, recorder_churn_entry) == "1"


async def test_switching_off_keeps_an_ignore_and_switching_on_leaves_it_ignored(
    hass: HomeAssistant,
    recorder_churn_entry: MockConfigEntry,
    per_day: dict[str, int],
    answers: dict[str, Any],
) -> None:
    """Off deletes open cards, their sensor and button, and keeps the ignored card; on and exclude again leaves it ignored."""
    ignored, open_one = _sensors(hass, per_day, 2)
    _say(answers, "exclude", "exclude")
    await _start(hass, recorder_churn_entry)
    _ignore(hass, ignored)

    async def set_enabled(enabled: bool) -> None:
        """Save the options with only this recipe's switch changed."""
        flow = await hass.config_entries.options.async_init(recorder_churn_entry.entry_id)
        options = {**recorder_churn_entry.options, CONF_RECORDER_CHURN_ENABLED: enabled, CONF_DAILY_BUDGET: DEFAULT_DAILY_BUDGET}
        await hass.config_entries.options.async_configure(flow["flow_id"], options)
        await hass.async_block_till_done(wait_background_tasks=True)

    await set_enabled(False)
    assert find_recipe_sensor(hass, recorder_churn_entry, RECIPE_RECORDER_CHURN) is None
    assert (
        er.async_get(hass).async_get_entity_id("button", DOMAIN, f"{recorder_churn_entry.entry_id}_{RECIPE_RECORDER_CHURN}_run")
        is None
    )
    assert _open_ids(hass) == set()
    _assert_ignored(hass, ignored)

    await set_enabled(True)
    await press_recipe_run(hass, recorder_churn_entry, RECIPE_RECORDER_CHURN)
    _assert_ignored(hass, ignored)
    assert _open_ids(hass) == {open_one.id}


async def test_removing_the_integration_clears_every_card_ignored_ones_included(
    hass: HomeAssistant, recorder_churn_entry: MockConfigEntry, per_day: dict[str, int], answers: dict[str, Any]
) -> None:
    """Removal leaves no exclude issue behind."""
    ignored, _open_one = _sensors(hass, per_day, 2)
    _say(answers, "exclude", "exclude")
    await _start(hass, recorder_churn_entry)
    _ignore(hass, ignored)

    await hass.config_entries.async_remove(recorder_churn_entry.entry_id)
    await hass.async_block_till_done()
    assert not [issue_id for domain, issue_id in ir.async_get(hass).issues if issue_id.startswith(RECORDER_CHURN_ISSUE_PREFIX)]
