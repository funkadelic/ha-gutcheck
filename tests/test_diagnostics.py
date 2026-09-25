"""Tests for Gut Check diagnostics: redaction, passthrough, and the single-secret invariant."""

from __future__ import annotations

import json

from homeassistant.components.diagnostics.const import REDACTED
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import get_diagnostics_for_config_entry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.gutcheck.const import RECIPE_HEALTH

from .conftest import health_result


async def test_download_redacts_the_api_key_and_carries_the_rest(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A real download through HA's diagnostics view redacts the key and nothing else."""
    assert await async_setup_component(hass, "diagnostics", {})
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    mock_config_entry.runtime_data.coordinators[RECIPE_HEALTH].async_set_updated_data(health_result({}))

    result = await get_diagnostics_for_config_entry(hass, hass_client, mock_config_entry)

    assert result["entry"]["data"][CONF_API_KEY] == REDACTED
    assert "test-key" not in json.dumps(result)
    assert set(result["recipes"]) == set(mock_config_entry.runtime_data.coordinators)
    assert "daily_budget" in result["budget"]
    assert "spent_today" in result["budget"]
    assert "remaining" in result["budget"]
