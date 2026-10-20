"""Entity-level name text: what the device-class, critical-label, diagnostic and recorder checks send."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.gutcheck.recipes.entity_text import entity_text


async def test_an_email_in_a_device_or_entity_name_is_replaced(hass: HomeAssistant) -> None:
    """An account device named after its email, as Google Drive does, never sends the email."""
    config_entry = MockConfigEntry(domain="google_drive")
    config_entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=config_entry.entry_id, identifiers={("google_drive", "acct")}, name="me@example.com"
    )
    entry = er.async_get(hass).async_get_or_create(
        "sensor", "google_drive", "used", device_id=device.id, original_name="me@example.com Used storage"
    )

    text = entity_text(hass, entry)

    assert text["name"] == "[email] Used storage"
    assert text["device_name"] == "[email]"
