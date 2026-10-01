"""Ranking, safety, the not-asked paths, the top N, the gate and failed counts for recorder suggestions."""

from __future__ import annotations

import json
from pathlib import Path
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
    ATTR_COUNTS,
    ATTR_ITEMS,
    ATTR_LAST_ERROR,
    ATTR_UNSURE,
    DOMAIN,
    ISSUE_RECORDER_EXCLUDE_SUGGESTION,
    RECIPE_RECORDER_CHURN,
    RECORDER_CHURN_ISSUE_PREFIX,
)
from custom_components.gutcheck.recipes.recorder_churn import RecorderChurnRecipe
from custom_components.gutcheck.recipes.recorder_churn_const import (
    CHURN_BUCKETS,
    CHURN_FLOOR_PER_DAY,
    CHURN_TOP_N,
    REASON_LOWER_RANK,
    REASON_NO_UNIQUE_ID,
    RECORDER_CHURN_INSTRUCTIONS,
)
from custom_components.gutcheck.recipes.recorder_churn_describe import churn_bucket
from custom_components.gutcheck.recipes.shapes import Batch

from .conftest import (
    posted_bodies,
    press_recipe_run,
    recipe_sensor_entity_id,
    recorder_churn_answer,
    register_jev_answers,
    register_unit_sensor,
)

_PATCH = "custom_components.gutcheck.recipes.recorder_churn.async_churn"
_WINDOW = 7


def _counts(per_day: dict[str, int]) -> tuple[int, dict[str, int]]:
    """A count map over a 7-day window for the given changes per day."""
    return _WINDOW, {entity_id: changes * _WINDOW for entity_id, changes in per_day.items()}


def _heavy(hass: HomeAssistant, unique: str, **kwargs: Any) -> er.RegistryEntry:
    """Register one sensor named after its unique id."""
    return register_unit_sensor(hass, unique, unit="W", name=unique.replace("_", " ").title(), **kwargs)


async def _prepare(hass: HomeAssistant, per_day: dict[str, int], label: str | None = None) -> Batch:
    """Run the recipe's prepare step over a patched count."""
    with patch(_PATCH, AsyncMock(return_value=_counts(per_day))):
        return await RecorderChurnRecipe(label).async_prepare(hass)


def _asked(batch: Batch) -> set[str]:
    """The entity ids the batch asks about."""
    return {str(subject["entity_id"]) for subject in batch.subjects.values()}


def _not_asked(batch: Batch) -> list[dict[str, Any]]:
    """The not-asked items the batch carries."""
    return [dict(item) for item in batch.carried["not_asked"]]


async def test_the_floor_is_inclusive(hass: HomeAssistant) -> None:
    """An entity at exactly the floor is ranked; one a change below is listed nowhere."""
    at_floor = _heavy(hass, "at_floor")
    below = _heavy(hass, "below_floor")
    batch = await _prepare(hass, {at_floor.entity_id: CHURN_FLOOR_PER_DAY, below.entity_id: CHURN_FLOOR_PER_DAY - 1})
    assert _asked(batch) == {at_floor.entity_id}
    assert _not_asked(batch) == []


async def test_forbidden_entities_are_never_ranked_however_heavy(hass: HomeAssistant) -> None:
    """Locks, alarms, covers, disabled, our own and critical-labelled entities are neither asked nor listed."""
    lr.async_get(hass).async_create("Critical")
    ordinary = _heavy(hass, "ordinary")
    forbidden = [
        _heavy(hass, "front_door", domain="lock"),
        _heavy(hass, "house_alarm", domain="alarm_control_panel"),
        _heavy(hass, "garage", domain="cover"),
        _heavy(hass, "off", disabled_by=er.RegistryEntryDisabler.USER),
        _heavy(hass, "ours", platform=DOMAIN),
        _heavy(hass, "labelled", labels=frozenset({"critical"})),
        _heavy(hass, "on_critical_device", device_name="Pump", device_labels=frozenset({"critical"})),
    ]
    batch = await _prepare(hass, {entry.entity_id: 5_000 for entry in [ordinary, *forbidden]}, "critical")
    assert _asked(batch) == {ordinary.entity_id}
    assert _not_asked(batch) == []


async def test_an_entity_with_no_unique_id_is_listed_but_never_asked(hass: HomeAssistant) -> None:
    """A live state with no registry entry is listed with a reason; gone or blocked ones are dropped."""
    ordinary = _heavy(hass, "ordinary")
    hass.states.async_set("sensor.legacy", "1")
    hass.states.async_set("cover.legacy_garage", "closed")
    batch = await _prepare(
        hass,
        {ordinary.entity_id: 4_000, "sensor.legacy": 3_000, "sensor.gone": 3_000, "cover.legacy_garage": 3_000},
    )
    assert _asked(batch) == {ordinary.entity_id}
    [item] = _not_asked(batch)
    assert (item["entity_id"], item["registry_id"], item["reason"]) == ("sensor.legacy", None, REASON_NO_UNIQUE_ID)


async def test_an_entity_with_no_unique_id_never_shifts_the_question_indices(hass: HomeAssistant) -> None:
    """entities[k] always describes the entity asked as r<k>."""
    first, second = _heavy(hass, "alpha"), _heavy(hass, "bravo")
    hass.states.async_set("sensor.legacy", "1")
    batch = await _prepare(hass, {first.entity_id: 3_000, "sensor.legacy": 2_000, second.entity_id: 1_000})
    assert list(batch.subjects) == ["r0", "r1"]
    registry = er.async_get(hass)
    for index, question_id in enumerate(batch.subjects):
        entry = registry.async_get(str(batch.subjects[question_id]["entity_id"]))
        assert entry is not None
        assert batch.state["entities"][index]["name"] == entry.original_name


@pytest.mark.parametrize(
    ("per_day", "word"),
    [(999, "heavy"), (1_000, "heavy"), (2_999, "heavy"), (3_000, "very heavy"), (9_999, "very heavy"), (10_000, "extreme")],
)
def test_churn_buckets_start_at_their_bound(per_day: int, word: str) -> None:
    """A figure takes the word of the highest bound it reaches."""
    assert churn_bucket(per_day) == word


def test_the_question_and_the_card_name_every_bucket_bound() -> None:
    """Each bound in the table appears with a thousands separator in the question and the card, the top one inclusive."""
    path = Path(__file__).parent.parent / "custom_components" / "gutcheck" / "translations" / "en.json"
    card = json.loads(path.read_text())["issues"][ISSUE_RECORDER_EXCLUDE_SUGGESTION]["description"]
    for text in (RECORDER_CHURN_INSTRUCTIONS, card):
        assert all(f"{bound:,}" in text for bound, _word in CHURN_BUCKETS)
        assert f"{CHURN_BUCKETS[0][0]:,} or more" in text


async def test_only_the_top_n_are_asked_in_requests_of_ten(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, recorder_churn_entry: MockConfigEntry
) -> None:
    """The heaviest CHURN_TOP_N go out, ten to a request, and the lightest two are listed as lower rank."""
    sensors = [_heavy(hass, f"s{index:02d}") for index in range(CHURN_TOP_N + 2)]
    per_day = {sensor.entity_id: 5_000 - index for index, sensor in enumerate(sensors)}
    register_jev_answers(aioclient_mock, {f"r{index}": recorder_churn_answer("keep", 0.9) for index in range(CHURN_TOP_N)})
    recorder_churn_entry.add_to_hass(hass)
    with patch(_PATCH, AsyncMock(return_value=_counts(per_day))):
        assert await hass.config_entries.async_setup(recorder_churn_entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)

    bodies = posted_bodies(aioclient_mock)
    assert [len(body["state"]["entities"]) for body in bodies] == [10, 10, 10]
    state = hass.states.get(recipe_sensor_entity_id(hass, recorder_churn_entry, RECIPE_RECORDER_CHURN))
    assert state is not None
    assert state.attributes[ATTR_COUNTS]["keep"] == CHURN_TOP_N
    lower = state.attributes[ATTR_ITEMS]["not_asked"]
    assert [(item["entity_id"], item["reason"]) for item in lower] == [(s.entity_id, REASON_LOWER_RANK) for s in sensors[-2:]]


async def test_a_quiet_install_sends_nothing(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, recorder_churn_entry: MockConfigEntry
) -> None:
    """Nothing at the floor: no request, every count 0, the sensor reads 0."""
    quiet = _heavy(hass, "quiet")
    recorder_churn_entry.add_to_hass(hass)
    with patch(_PATCH, AsyncMock(return_value=_counts({quiet.entity_id: CHURN_FLOOR_PER_DAY - 1}))):
        assert await hass.config_entries.async_setup(recorder_churn_entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)

    assert posted_bodies(aioclient_mock) == []
    state = hass.states.get(recipe_sensor_entity_id(hass, recorder_churn_entry, RECIPE_RECORDER_CHURN))
    assert state is not None
    assert state.state == "0"
    assert state.attributes[ATTR_COUNTS] == {"exclude": 0, "throttle": 0, "keep": 0, "not_asked": 0}


async def test_every_answer_lands_in_the_right_bucket_and_only_exclude_raises_a_card(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, recorder_churn_entry: MockConfigEntry
) -> None:
    """Confident answers fill their buckets, every doubtful or malformed one is unsure, and highest churn leads."""
    sensors = [_heavy(hass, f"e{index}") for index in range(8)]
    per_day = {sensor.entity_id: 8_000 - index * 100 for index, sensor in enumerate(sensors)}
    answers: list[Any] = [
        recorder_churn_answer("exclude", 0.9),
        recorder_churn_answer("throttle", 0.9),
        recorder_churn_answer("keep", 0.9),
        recorder_churn_answer("exclude", 0.9),
        recorder_churn_answer("exclude", 0.3),
        recorder_churn_answer("none_of_these", 0.9),
        {"type": "choice", "choice": "bogus", "confidence": 0.9},
        "not an answer",
    ]
    register_jev_answers(aioclient_mock, {f"r{index}": answer for index, answer in enumerate(answers)})
    recorder_churn_entry.add_to_hass(hass)
    with patch(_PATCH, AsyncMock(return_value=_counts(per_day))):
        assert await hass.config_entries.async_setup(recorder_churn_entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)

    state = hass.states.get(recipe_sensor_entity_id(hass, recorder_churn_entry, RECIPE_RECORDER_CHURN))
    assert state is not None
    assert state.attributes[ATTR_COUNTS] == {"exclude": 2, "throttle": 1, "keep": 1, "not_asked": 0}
    items = state.attributes[ATTR_ITEMS]
    assert [item["entity_id"] for item in items["exclude"]] == [sensors[0].entity_id, sensors[3].entity_id]
    assert [item["entity_id"] for item in items["throttle"] + items["keep"]] == [sensors[1].entity_id, sensors[2].entity_id]
    assert [item["entity_id"] for item in state.attributes[ATTR_UNSURE]] == [s.entity_id for s in sensors[4:]]
    issues = {issue_id for domain, issue_id in ir.async_get(hass).issues if domain == DOMAIN}
    assert issues == {f"{RECORDER_CHURN_ISSUE_PREFIX}{sensors[0].id}", f"{RECORDER_CHURN_ISSUE_PREFIX}{sensors[3].id}"}
    assert state.state == "2"


async def test_no_recorder_fails_the_run_and_sends_nothing(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, recorder_churn_entry: MockConfigEntry
) -> None:
    """With no recorder there is no request, no result, and the reason is recorded."""
    recorder_churn_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(recorder_churn_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert posted_bodies(aioclient_mock) == []
    coordinator = recorder_churn_entry.runtime_data.coordinators[RECIPE_RECORDER_CHURN]
    assert str(coordinator.last_exception) == "recorder history unavailable"
    state = hass.states.get(recipe_sensor_entity_id(hass, recorder_churn_entry, RECIPE_RECORDER_CHURN))
    assert state is not None
    assert state.state == "unavailable"


async def test_a_failed_count_keeps_the_last_report_and_its_card(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, recorder_churn_entry: MockConfigEntry
) -> None:
    """After a good run, a count that fails leaves the counts and the card as they were and says why."""
    sensor = _heavy(hass, "co2")
    register_jev_answers(aioclient_mock, {"r0": recorder_churn_answer("exclude", 0.9)})
    recorder_churn_entry.add_to_hass(hass)
    with patch(_PATCH, AsyncMock(side_effect=[_counts({sensor.entity_id: 3_000}), None])):
        assert await hass.config_entries.async_setup(recorder_churn_entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        await press_recipe_run(hass, recorder_churn_entry, RECIPE_RECORDER_CHURN)

    state = hass.states.get(recipe_sensor_entity_id(hass, recorder_churn_entry, RECIPE_RECORDER_CHURN))
    assert state is not None
    assert state.attributes[ATTR_COUNTS]["exclude"] == 1
    assert state.attributes[ATTR_LAST_ERROR] == "recorder history unavailable"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"{RECORDER_CHURN_ISSUE_PREFIX}{sensor.id}")
    assert issue is not None
    assert issue.active
    assert len(posted_bodies(aioclient_mock)) == 1
