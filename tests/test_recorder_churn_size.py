"""A full recorder suggestions run at the top-N cap goes out capped, with headroom."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import DEVICE_TEXT_MAX_CHARS
from custom_components.gutcheck.recipes.recorder_churn_const import CHURN_TOP_N

from .conftest import (
    assert_capped_run_fits,
    posted_bodies,
    recorder_churn_answer,
    register_jev_answers,
    register_unit_sensor,
)

_LONG = ("Very Long Text Chosen By The User Or Maker " * 6)[:DEVICE_TEXT_MAX_CHARS]
_WINDOW = 7


async def test_a_full_top_n_run_fits_the_token_limits_and_default_budget(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, recorder_churn_entry: MockConfigEntry
) -> None:
    """CHURN_TOP_N entities with every text field at its cap go out ten to a request inside both limits and the budget."""
    sensors = [
        register_unit_sensor(
            hass,
            f"long_{index:02d}",
            unit=_LONG,
            name=_LONG,
            device_name=_LONG,
            manufacturer=_LONG,
            model=_LONG,
            device_class=_LONG,
            state_class="measurement",
        )
        for index in range(CHURN_TOP_N)
    ]
    counts = (_WINDOW, {sensor.entity_id: (9_000 - index) * _WINDOW for index, sensor in enumerate(sensors)})
    register_jev_answers(aioclient_mock, {f"r{index}": recorder_churn_answer("keep", 0.9) for index in range(CHURN_TOP_N)})
    recorder_churn_entry.add_to_hass(hass)
    with patch("custom_components.gutcheck.recipes.recorder_churn.async_churn", AsyncMock(return_value=counts)):
        assert await hass.config_entries.async_setup(recorder_churn_entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)

    total = assert_capped_run_fits(posted_bodies(aioclient_mock), "entities", CHURN_TOP_N)
    print(f"recorder suggestions estimate, factored: {total / CHURN_TOP_N:.0f} tokens per asked entity")
