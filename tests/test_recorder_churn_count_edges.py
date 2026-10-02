"""The recorder count window: a row written exactly at the window start still counts."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.gutcheck.recipes.recorder_churn_const import CHURN_WINDOW_DAYS
from custom_components.gutcheck.recipes.recorder_churn_count import async_churn


async def test_a_state_written_exactly_at_the_window_start_is_counted(recorder_mock, hass: HomeAssistant, freezer) -> None:
    """The window is closed at its start, so the row from exactly CHURN_WINDOW_DAYS ago is one of the counted changes."""
    freezer.move_to("2026-01-01T00:00:00+00:00")
    hass.states.async_set("sensor.edge", "1")
    await async_wait_recording_done(hass)
    freezer.tick(CHURN_WINDOW_DAYS * 86400)

    churn = await async_churn(hass)

    assert churn is not None
    assert churn[1].get("sensor.edge") == 1
