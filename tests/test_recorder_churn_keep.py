"""Energy and total-state-class keeps, statistics and reference fields, and the top N, for recorder suggestions."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import (
    ATTR_COUNTS,
    ATTR_ITEMS,
    DOMAIN,
    RECIPE_RECORDER_CHURN,
    RECORDER_CHURN_ISSUE_PREFIX,
)
from custom_components.gutcheck.recipes.recorder_churn import RecorderChurnRecipe
from custom_components.gutcheck.recipes.recorder_churn_const import (
    CHURN_TOP_N,
    REASON_ENERGY,
    REASON_LOWER_RANK,
    REASON_NO_UNIQUE_ID,
    REASON_TOTAL_STATE_CLASS,
)
from custom_components.gutcheck.recipes.shapes import Batch

from .conftest import (
    posted_bodies,
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


def _heavy(hass: HomeAssistant, unique: str, state_class: str | None = "measurement") -> er.RegistryEntry:
    """Register one sensor named after its unique id."""
    return register_unit_sensor(hass, unique, unit="W", name=unique.replace("_", " ").title(), state_class=state_class)


def _seed_energy(hass_storage: dict[str, Any], grid_import: str, device: str) -> None:
    """Save Energy preferences naming a grid import statistic and a device consumption statistic."""
    hass_storage["energy"] = {
        "version": 1,
        "minor_version": 3,
        "key": "energy",
        "data": {
            "energy_sources": [{"type": "grid", "stat_energy_from": grid_import, "stat_energy_to": None}],
            "device_consumption": [{"stat_consumption": device}],
        },
    }


async def _prepare(hass: HomeAssistant, per_day: dict[str, int]) -> Batch:
    """Run the recipe's prepare step over a patched count."""
    with patch(_PATCH, AsyncMock(return_value=_counts(per_day))):
        return await RecorderChurnRecipe(None).async_prepare(hass)


def _asked(batch: Batch) -> list[str]:
    """The entity ids the batch asks about, in question order."""
    return [str(subject["entity_id"]) for subject in batch.subjects.values()]


def _carried(batch: Batch, option: str) -> list[tuple[str, str]]:
    """The entity id and reason of every item the batch carries under option."""
    return [(str(item["entity_id"]), str(item["reason"])) for item in batch.carried[option]]


async def test_energy_dashboard_entities_are_kept_and_never_asked_or_given_a_slot(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """A grid import and a device consumption sensor are kept in code; the third is the only one asked as r0."""
    grid, plug, other = _heavy(hass, "grid_import"), _heavy(hass, "plug_energy"), _heavy(hass, "other")
    _seed_energy(hass_storage, grid.entity_id, plug.entity_id)
    batch = await _prepare(hass, {grid.entity_id: 9_000, plug.entity_id: 8_000, other.entity_id: 1_500})
    assert _asked(batch) == [other.entity_id]
    assert list(batch.subjects) == ["r0"]
    assert _carried(batch, "keep") == [(grid.entity_id, REASON_ENERGY), (plug.entity_id, REASON_ENERGY)]


async def test_an_energy_entity_with_no_unique_id_is_kept_not_listed_as_missing_an_id(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """The Energy keep is decided by entity id, so a registry-less sensor still counts as energy."""
    hass.states.async_set("sensor.legacy_meter", "1")
    hass.states.async_set("sensor.legacy_other", "1")
    _seed_energy(hass_storage, "sensor.legacy_meter", "sensor.unused")
    batch = await _prepare(hass, {"sensor.legacy_meter": 5_000, "sensor.legacy_other": 4_000})
    assert _carried(batch, "keep") == [("sensor.legacy_meter", REASON_ENERGY)]
    assert _carried(batch, "not_asked") == [("sensor.legacy_other", REASON_NO_UNIQUE_ID)]


async def test_nothing_is_kept_for_energy_when_preferences_were_never_saved(hass: HomeAssistant) -> None:
    """With no saved Energy preferences every ordinary heavy sensor is asked."""
    sensor = _heavy(hass, "meter")
    batch = await _prepare(hass, {sensor.entity_id: 5_000})
    assert _asked(batch) == [sensor.entity_id]
    assert batch.carried["keep"] == []


async def test_a_run_where_only_kept_entities_rank_sends_nothing(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    recorder_churn_entry: MockConfigEntry,
    hass_storage: dict[str, Any],
) -> None:
    """Energy and total-class sensors alone leave no question, no request and no card."""
    grid, plug, total = _heavy(hass, "grid_import"), _heavy(hass, "plug_energy"), _heavy(hass, "lifetime", "total")
    _seed_energy(hass_storage, grid.entity_id, plug.entity_id)
    recorder_churn_entry.add_to_hass(hass)
    per_day = {grid.entity_id: 9_000, plug.entity_id: 8_000, total.entity_id: 7_000}
    with patch(_PATCH, AsyncMock(return_value=_counts(per_day))):
        assert await hass.config_entries.async_setup(recorder_churn_entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)

    assert posted_bodies(aioclient_mock) == []
    state = hass.states.get(recipe_sensor_entity_id(hass, recorder_churn_entry, RECIPE_RECORDER_CHURN))
    assert state is not None
    assert state.state == "0"
    assert state.attributes[ATTR_COUNTS]["keep"] == 3
    assert [(item["entity_id"], item["reason"]) for item in state.attributes[ATTR_ITEMS]["keep"]] == [
        (grid.entity_id, REASON_ENERGY),
        (plug.entity_id, REASON_ENERGY),
        (total.entity_id, REASON_TOTAL_STATE_CLASS),
    ]
    assert not [issue for domain, issue in ir.async_get(hass).issues if domain == DOMAIN]


@pytest.mark.parametrize("state_class", ["total", "total_increasing"])
async def test_total_state_classes_are_kept_without_a_question(hass: HomeAssistant, state_class: str) -> None:
    """A cumulative sensor is kept with its reason and never asked."""
    sensor = _heavy(hass, "meter", state_class)
    batch = await _prepare(hass, {sensor.entity_id: 5_000})
    assert _asked(batch) == []
    assert _carried(batch, "keep") == [(sensor.entity_id, REASON_TOTAL_STATE_CLASS)]


@pytest.mark.parametrize(
    ("state_class", "statistics"),
    [("measurement", True), ("measurement_angle", True), (None, False)],
)
async def test_long_term_statistics_follow_the_state_class(
    hass: HomeAssistant, state_class: str | None, statistics: bool
) -> None:
    """Measurement classes are asked with statistics true, a sensor with no class with it false."""
    sensor = _heavy(hass, "reading", state_class)
    batch = await _prepare(hass, {sensor.entity_id: 5_000})
    assert [item["long_term_statistics"] for item in batch.state["entities"]] == [statistics]


async def test_kept_entities_never_use_an_asked_slot(hass: HomeAssistant) -> None:
    """With kept sensors ranked first, exactly CHURN_TOP_N are asked and the next one is lower rank."""
    kept = [_heavy(hass, f"total_{index}", "total") for index in range(3)]
    askable = [_heavy(hass, f"reading_{index:02d}") for index in range(CHURN_TOP_N + 1)]
    per_day = {sensor.entity_id: 20_000 - index for index, sensor in enumerate([*kept, *askable])}
    batch = await _prepare(hass, per_day)
    assert _asked(batch) == [sensor.entity_id for sensor in askable[:CHURN_TOP_N]]
    assert [entity_id for entity_id, _reason in _carried(batch, "keep")] == [sensor.entity_id for sensor in kept]
    assert _carried(batch, "not_asked") == [(askable[-1].entity_id, REASON_LOWER_RANK)]


async def test_references_reach_the_model_but_never_decide_anything(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, recorder_churn_entry: MockConfigEntry
) -> None:
    """An automation, script, scene and group reference each make referenced true; a referenced exclude still gets a card."""
    automated, scripted, scened, grouped, loose = (_heavy(hass, name) for name in ("auto", "script", "scene", "group", "loose"))
    assert await async_setup_component(
        hass,
        "automation",
        {"automation": [{"trigger": {"platform": "state", "entity_id": automated.entity_id}, "action": [{"event": "noop"}]}]},
    )
    assert await async_setup_component(
        hass,
        "script",
        {
            "script": {
                "tidy": {
                    "sequence": [{"action": "homeassistant.turn_off", "target": {"entity_id": scripted.entity_id}}],
                }
            }
        },
    )
    assert await async_setup_component(
        hass,
        "scene",
        {"scene": {"platform": "homeassistant", "states": [{"name": "Movie", "entities": {scened.entity_id: "on"}}]}},
    )
    assert await async_setup_component(hass, "group", {"group": {"pair": {"entities": [grouped.entity_id]}}})

    sensors = [automated, scripted, scened, grouped, loose]
    per_day = {sensor.entity_id: 9_000 - index * 100 for index, sensor in enumerate(sensors)}
    register_jev_answers(aioclient_mock, {f"r{index}": recorder_churn_answer("exclude", 0.9) for index in range(5)})
    recorder_churn_entry.add_to_hass(hass)
    with patch(_PATCH, AsyncMock(return_value=_counts(per_day))):
        assert await hass.config_entries.async_setup(recorder_churn_entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)

    [body] = posted_bodies(aioclient_mock)
    assert [item["referenced"] for item in body["state"]["entities"]] == [True, True, True, True, False]
    registry = ir.async_get(hass)
    assert all(registry.async_get_issue(DOMAIN, f"{RECORDER_CHURN_ISSUE_PREFIX}{sensor.id}") for sensor in sensors)
