"""Tests for Gut Check diagnostics: redaction, passthrough, and the single-secret invariant."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.components.diagnostics.const import REDACTED
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import get_diagnostics_for_config_entry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.gutcheck import GutCheckData
from custom_components.gutcheck.budget import BudgetGate
from custom_components.gutcheck.client import GutCheckClient
from custom_components.gutcheck.const import (
    CONF_AREAS_ENABLED,
    CONF_HEALTH_ENABLED,
    CONF_UPDATES_ENABLED,
    DOMAIN,
    RECIPE_HEALTH,
)
from custom_components.gutcheck.diagnostics import async_get_config_entry_diagnostics
from custom_components.gutcheck.recipes.device_class_undo import AppliedClasses

from .conftest import health_item, health_result


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


async def test_download_carries_counts_items_unsure_and_last_payload_unredacted(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Nothing but the API key is touched: counts, items, unsure and the last payload survive byte-for-byte."""
    assert await async_setup_component(hass, "diagnostics", {})
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    item = health_item("sensor.front_door", "reg1")
    unsure_item = health_item("sensor.back_door", "reg2")
    last_payload = {
        "state": {"entities": [{"entity_id": "sensor.front_door", "friendly_name": "Front Door Sensor"}]},
        "model": "jev-latest",
        "questions": {"q0": {"type": "choice", "instructions": "Decide.", "criteria": {"expected": "As expected."}}},
    }
    result_data = {
        "last_run": "2026-09-24T00:00:00+00:00",
        "counts": {"expected": 1},
        "items": {"expected": [item]},
        "unsure": [unsure_item],
        "last_payload": last_payload,
    }
    mock_config_entry.runtime_data.coordinators[RECIPE_HEALTH].async_set_updated_data(result_data)

    result = await get_diagnostics_for_config_entry(hass, hass_client, mock_config_entry)

    health = result["recipes"][RECIPE_HEALTH]
    assert health["counts"] == {"expected": 1}
    assert health["items"] == {"expected": [item]}
    assert health["unsure"] == [unsure_item]
    assert health["last_payload"] == last_payload
    budget = mock_config_entry.runtime_data.budget
    assert result["budget"]["daily_budget"] == budget.daily_budget
    assert result["budget"]["spent_today"] == budget.spent_today
    assert result["budget"]["remaining"] == budget.remaining
    assert isinstance(result["budget"]["daily_budget"], int)
    assert isinstance(result["budget"]["spent_today"], int)
    assert isinstance(result["budget"]["remaining"], int)


async def test_a_second_stored_credential_fails_this_test_until_redaction_is_reviewed(hass: HomeAssistant) -> None:
    """The config flow's own created entry stores exactly one secret; a new one needs a redaction review first."""
    with patch("custom_components.gutcheck.config_flow.async_validate_api_key", return_value=None):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: "test-key"})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert set(result["data"]) == {CONF_API_KEY}, (
        "a new field in the created entry's data needs a TO_REDACT review in diagnostics.py before this test is updated"
    )


async def test_a_never_run_recipe_dumps_as_null(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> None:
    """A coordinator that has never completed a run dumps null, not a missing key.

    Calls async_get_config_entry_diagnostics directly against a hand-built
    coordinator stub, rather than through a real config entry setup: this
    integration's own recipes complete an empty run (and so set .data) the
    moment Home Assistant reaches its started state, which every test hass
    already has, so a real setup can never observe the pre-first-run state.
    """
    client = GutCheckClient(async_get_clientsession(hass), "test-key")
    budget = BudgetGate(hass, client, 150_000)
    await budget.async_load()
    applied = AppliedClasses(hass)
    await applied.async_load()
    mock_config_entry.add_to_hass(hass)
    mock_config_entry.mock_state(hass, config_entries.ConfigEntryState.LOADED)
    mock_config_entry.runtime_data = GutCheckData(
        client=client, budget=budget, coordinators={RECIPE_HEALTH: SimpleNamespace(data=None)}, applied=applied
    )

    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    assert result["recipes"][RECIPE_HEALTH] is None


async def test_an_entry_with_every_recipe_off_dumps_an_empty_recipe_map(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
) -> None:
    """An entry with health, updates and areas all disabled dumps a recipe map with nothing in it."""
    assert await async_setup_component(hass, "diagnostics", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_API_KEY: "test-key"},
        options={CONF_HEALTH_ENABLED: False, CONF_UPDATES_ENABLED: False, CONF_AREAS_ENABLED: False},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    assert result["recipes"] == {}


async def test_an_entry_that_never_finished_setup_still_downloads(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
) -> None:
    """An entry that failed setup has no runtime data; the download carries the entry alone."""
    assert await async_setup_component(hass, "diagnostics", {})
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: " "}, options={CONF_HEALTH_ENABLED: False})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is config_entries.ConfigEntryState.SETUP_ERROR

    result = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    assert result == {"entry": {"data": {CONF_API_KEY: REDACTED}, "options": {CONF_HEALTH_ENABLED: False}}}
