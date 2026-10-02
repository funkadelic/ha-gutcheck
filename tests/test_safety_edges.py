"""Safety rule edges: a disabled critical entity on a device, and a device label found through the registry."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr

from custom_components.gutcheck.recipes.safety import SafetyRules

from .conftest import AreaEntitySpec, register_area_device


async def test_a_disabled_entity_carrying_the_critical_label_still_excludes_its_device(hass: HomeAssistant) -> None:
    """The label sits on a disabled entity beside an ordinary one, and the device is out of the area recipe's reach."""
    label = lr.async_get(hass).async_create("Critical")
    device = register_area_device(
        hass,
        "d",
        entities=[
            AreaEntitySpec("sensor"),
            AreaEntitySpec("switch", disabled_by=er.RegistryEntryDisabler.USER, labels=frozenset({label.label_id})),
        ],
    )

    assert SafetyRules(label.label_id).excludes_device(hass, device) is True
    assert SafetyRules(None).excludes_device(hass, device) is False


async def test_a_stored_finding_is_excluded_by_a_label_on_its_device(hass: HomeAssistant) -> None:
    """Looking a finding up by registry id still sees the label on the device behind it."""
    label = lr.async_get(hass).async_create("Critical")
    device = register_area_device(hass, "d", labels=frozenset({label.label_id}), entities=["sensor"])
    (entry,) = er.async_entries_for_device(er.async_get(hass), device.id)

    assert SafetyRules(label.label_id).excludes_entity_id(hass, entry.id) is True
    assert SafetyRules(None).excludes_entity_id(hass, entry.id) is False
