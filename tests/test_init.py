"""Unit tests for __init__.py helpers not otherwise covered end-to-end."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck import _async_remove_recipe_entities
from custom_components.gutcheck.const import DOMAIN


async def test_a_recipe_id_that_prefixes_another_ones_id_keeps_the_other_recipes_entities(hass: HomeAssistant) -> None:
    """Removing "update" must not sweep an unrelated "updates" recipe's entities too."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    kept = registry.async_get_or_create("sensor", DOMAIN, f"{entry.entry_id}_updates", config_entry=entry)
    removed_sensor = registry.async_get_or_create("sensor", DOMAIN, f"{entry.entry_id}_update", config_entry=entry)
    removed_button = registry.async_get_or_create("button", DOMAIN, f"{entry.entry_id}_update_run", config_entry=entry)

    _async_remove_recipe_entities(hass, entry, "update")

    assert registry.async_get(kept.entity_id) is not None
    assert registry.async_get(removed_sensor.entity_id) is None
    assert registry.async_get(removed_button.entity_id) is None


async def test_setup_starts_reauth_with_no_network_call_when_the_stored_key_is_missing(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A config entry with no stored key at all must not reach the API."""
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert [flow["context"]["source"] for flow in entry.async_get_active_flows(hass, {SOURCE_REAUTH})] == [SOURCE_REAUTH]
    assert aioclient_mock.mock_calls == []


async def test_setup_starts_reauth_with_no_network_call_when_the_stored_key_is_blank(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A stored key that is empty or only whitespace must not reach the API either."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: "   "})
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert [flow["context"]["source"] for flow in entry.async_get_active_flows(hass, {SOURCE_REAUTH})] == [SOURCE_REAUTH]
    assert aioclient_mock.mock_calls == []


async def test_setup_proceeds_unchanged_with_a_normal_stored_key(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A normal stored key sets up exactly as it does today."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: "a-real-key"})
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)

    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data is not None
