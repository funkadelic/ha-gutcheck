"""Source entities of statistics, history stats and filter sensors are kept in code, UI-made or YAML."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.gutcheck.recipes.recorder_churn import RecorderChurnRecipe
from custom_components.gutcheck.recipes.recorder_churn_const import REASON_DERIVED_SOURCE
from custom_components.gutcheck.recipes.recorder_churn_readers import recorder_reader_sources

from .conftest import register_unit_sensor

_PATCH = "custom_components.gutcheck.recipes.recorder_churn.async_churn"

# The shape of the target install's YAML helpers: no config entry, no registry entry.
_YAML_READERS: list[dict[str, Any]] = [
    {
        "platform": "statistics",
        "name": "Mean",
        "entity_id": "sensor.source_a",
        "state_characteristic": "mean",
        "max_age": {"hours": 1},
    },
    {
        "platform": "history_stats",
        "name": "On time",
        "entity_id": "binary_sensor.source_b",
        "state": "on",
        "type": "time",
        "start": "{{ today_at() }}",
        "end": "{{ now() }}",
    },
    {
        "platform": "filter",
        "name": "Smooth",
        "entity_id": "sensor.source_c",
        "filters": [{"filter": "lowpass", "time_constant": 10}],
    },
    {"platform": "template", "sensors": {"plain": {"value_template": "{{ states('sensor.source_d') }}"}}},
]

# The history stats sensor's own coordinator keeps its refresh timer until Home Assistant stops.
_HISTORY_STATS_TIMER = pytest.mark.parametrize("expected_lingering_timers", [True])


def test_ui_made_helpers_name_their_source_by_entity_id_or_registry_id(hass: HomeAssistant) -> None:
    """Each helper domain's entity_id option counts, a registry id resolves, and anything else is skipped."""
    tracked = register_unit_sensor(hass, "tracked", unit="W")
    options = [
        ("statistics", {"entity_id": "sensor.mean_source"}),
        ("history_stats", {"entity_id": tracked.id}),
        ("filter", {"entity_id": "sensor.filter_source"}),
        ("filter", {"entity_id": "not a known id"}),
        ("statistics", {}),
        ("derivative", {"source": "sensor.live_only"}),
    ]
    for domain, option in options:
        MockConfigEntry(domain=domain, options=option).add_to_hass(hass)
    assert recorder_reader_sources(hass) == {"sensor.mean_source", tracked.entity_id, "sensor.filter_source"}


@_HISTORY_STATS_TIMER
async def test_running_yaml_helpers_name_their_source(recorder_mock: Any, hass: HomeAssistant) -> None:
    """A YAML statistics, history stats and filter sensor each name their source; a template sensor does not."""
    assert await async_setup_component(hass, "sensor", {"sensor": _YAML_READERS})
    await hass.async_block_till_done()
    assert recorder_reader_sources(hass) == {"sensor.source_a", "binary_sensor.source_b", "sensor.source_c"}


@_HISTORY_STATS_TIMER
async def test_an_unreadable_source_is_counted_and_left_out(
    recorder_mock: Any, hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A renamed private attribute drops that one sensor's source and logs the count per platform, not a crash."""
    assert await async_setup_component(hass, "sensor", {"sensor": _YAML_READERS})
    await hass.async_block_till_done()
    smooth = hass.data["sensor"].get_entity("sensor.smooth")
    assert smooth is not None
    del smooth._entity
    with caplog.at_level(logging.DEBUG, logger="custom_components.gutcheck.recipes.recorder_churn_readers"):
        assert recorder_reader_sources(hass) == {"sensor.source_a", "binary_sensor.source_b"}
    assert "{'filter': 1}" in caplog.text


async def test_without_sensors_loaded_only_config_entries_count(hass: HomeAssistant) -> None:
    """With no sensor component the running pass adds nothing."""
    assert "sensor" not in hass.data
    assert recorder_reader_sources(hass) == set()


async def test_a_source_is_kept_in_code_and_never_asked(hass: HomeAssistant) -> None:
    """The statistics source is carried as kept with its reason; the other heavy sensor is the only one asked."""
    source, other = register_unit_sensor(hass, "source", unit="W"), register_unit_sensor(hass, "other", unit="W")
    MockConfigEntry(domain="statistics", options={"entity_id": source.entity_id}).add_to_hass(hass)
    counts = (7, {source.entity_id: 70_000, other.entity_id: 35_000})
    with patch(_PATCH, AsyncMock(return_value=counts)):
        batch = await RecorderChurnRecipe(None).async_prepare(hass)
    assert [subject["entity_id"] for subject in batch.subjects.values()] == [other.entity_id]
    assert [(item["entity_id"], item["reason"]) for item in batch.carried["keep"]] == [(source.entity_id, REASON_DERIVED_SOURCE)]
