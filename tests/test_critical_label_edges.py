"""Critical label card guard: a malformed id beside a valid label id writes nothing."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import label_registry as lr

from custom_components.gutcheck.recipes.critical_label_repairs import add_critical_label
from custom_components.gutcheck.recipes.safety import SafetyRules


async def test_an_unhashable_registry_id_with_the_configured_label_is_refused(hass: HomeAssistant) -> None:
    """The label matches the configured one, so only the registry id check stops the write."""
    label = lr.async_get(hass).async_create("Critical")

    assert add_critical_label(hass, SafetyRules(label.label_id), {"registry_id": ["x"], "label_id": label.label_id}) is False
