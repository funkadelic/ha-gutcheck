"""Reader sources: other integrations' options are ignored, and a sensor that cannot be read is counted, not trusted."""

from __future__ import annotations

import logging
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockEntity, MockEntityPlatform

from custom_components.gutcheck.recipes.recorder_churn_readers import recorder_reader_sources

from .test_recorder_churn_readers import _HISTORY_STATS_TIMER, _YAML_READERS

_LOGGER_NAME = "custom_components.gutcheck.recipes.recorder_churn_readers"
_FILTERS = [
    {
        "platform": "filter",
        "name": f"Smooth {index}",
        "entity_id": f"sensor.filter_source_{index}",
        "filters": [{"filter": "outlier"}],
    }
    for index in range(2)
]


def test_an_entity_id_option_on_another_integrations_entry_is_not_a_reader_source(hass: HomeAssistant) -> None:
    """Only the statistics, history stats and filter domains name sources; any other entry with the option is left alone."""
    MockConfigEntry(domain="derivative", options={"entity_id": "sensor.not_a_reader_source"}).add_to_hass(hass)
    MockConfigEntry(domain="statistics", options={"entity_id": "sensor.real_source"}).add_to_hass(hass)

    assert recorder_reader_sources(hass) == {"sensor.real_source"}


@_HISTORY_STATS_TIMER
async def test_a_sensor_that_is_not_a_reader_does_not_stop_the_scan(recorder_mock: Any, hass: HomeAssistant) -> None:
    """A sensor from another platform listed first is passed over and the readers after it still name their sources."""
    plain = MockEntityPlatform(hass, domain="sensor", platform_name="plain")
    await plain.async_add_entities([MockEntity(name="Plain", unique_id="p")])
    assert await async_setup_component(hass, "sensor", {"sensor": _YAML_READERS[:3]})
    await hass.async_block_till_done()
    assert next(iter(hass.data["sensor"].entities)).platform.platform_name == "plain"

    assert recorder_reader_sources(hass) == {"sensor.source_a", "binary_sensor.source_b", "sensor.source_c"}


@_HISTORY_STATS_TIMER
async def test_a_source_that_is_not_an_entity_id_is_counted_as_unreadable(
    recorder_mock: Any, hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A filter sensor whose source attribute holds something other than an entity id is left out and counted."""
    assert await async_setup_component(hass, "sensor", {"sensor": _FILTERS})
    await hass.async_block_till_done()
    first = hass.data["sensor"].get_entity("sensor.smooth_0")
    assert first is not None
    first._entity = "not an entity id"
    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        assert recorder_reader_sources(hass) == {"sensor.filter_source_1"}
    assert "{'filter': 1}" in caplog.text


@_HISTORY_STATS_TIMER
async def test_every_unreadable_sensor_on_a_platform_is_counted(
    recorder_mock: Any, hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Two filter sensors with no readable source are logged as two for the platform."""
    assert await async_setup_component(hass, "sensor", {"sensor": _FILTERS})
    await hass.async_block_till_done()
    for index in range(2):
        entity = hass.data["sensor"].get_entity(f"sensor.smooth_{index}")
        assert entity is not None
        del entity._entity
    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        assert recorder_reader_sources(hass) == set()
    assert "{'filter': 2}" in caplog.text
