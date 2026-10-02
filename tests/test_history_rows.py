"""Row handling in the recorder lookup: a skipped row or a clean entity must not hide a later outage."""

from __future__ import annotations

from datetime import UTC, datetime

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.gutcheck.history import async_unavailable_since


async def test_an_available_entity_does_not_hide_an_unavailable_one_in_the_same_query(
    recorder_mock, hass: HomeAssistant, freezer
) -> None:
    """sensor.a is fine, sensor.b is down: only b is mapped, whichever order the recorder returns them in."""
    freezer.move_to("2026-01-01T00:00:00+00:00")
    hass.states.async_set("sensor.a", "on")
    hass.states.async_set("sensor.b", "on")
    await async_wait_recording_done(hass)
    freezer.move_to("2026-01-03T00:00:00+00:00")
    hass.states.async_set("sensor.b", STATE_UNAVAILABLE)
    await async_wait_recording_done(hass)

    for entity_ids in (["sensor.a", "sensor.b"], ["sensor.b", "sensor.a"]):
        _, since_map = await async_unavailable_since(hass, entity_ids)
        assert since_map == {"sensor.b": datetime(2026, 1, 3, tzinfo=UTC)}


async def test_a_removed_state_row_before_the_outage_is_skipped_not_a_stop(recorder_mock, hass: HomeAssistant, freezer) -> None:
    """Available, removed, then unavailable: the empty row is skipped and the outage still dates from its own row."""
    freezer.move_to("2026-01-01T00:00:00+00:00")
    hass.states.async_set("sensor.a", "on")
    await async_wait_recording_done(hass)
    freezer.move_to("2026-01-02T00:00:00+00:00")
    hass.states.async_remove("sensor.a")
    await async_wait_recording_done(hass)
    freezer.move_to("2026-01-03T00:00:00+00:00")
    hass.states.async_set("sensor.a", STATE_UNAVAILABLE)
    await async_wait_recording_done(hass)

    _, since_map = await async_unavailable_since(hass, ["sensor.a"])

    assert since_map == {"sensor.a": datetime(2026, 1, 3, tzinfo=UTC)}
