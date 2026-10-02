"""Device class names and choices, and the repair flow's forms, with the shapes a real card can have."""

from __future__ import annotations

from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.gutcheck.const import DEVICE_CLASS_ISSUE_PREFIX, DOMAIN, ISSUE_DEVICE_CLASS_SUGGESTION, OPTION_NONE
from custom_components.gutcheck.recipes.device_class_const import DEVICE_CLASS_NONE_DESCRIPTION
from custom_components.gutcheck.recipes.device_class_describe import KNOWN_CLASSES, class_names, criteria
from custom_components.gutcheck.recipes.device_class_repairs import other_class_choices, set_device_class
from custom_components.gutcheck.recipes.safety import SafetyRules
from custom_components.gutcheck.recipes.suggestion_flow import SuggestionRepairFlow

from .conftest import register_unit_sensor


async def test_every_known_class_has_a_text_name(hass: HomeAssistant) -> None:
    """A class with no translation still maps to its own key, never to nothing."""
    names = await class_names(hass)

    assert set(names) == set(KNOWN_CLASSES)
    assert all(isinstance(name, str) and name for name in names.values())


def test_a_candidate_missing_from_the_names_is_described_by_its_own_key() -> None:
    """The criteria describe each candidate by its name, or by the class itself when no name is known."""
    assert criteria(("battery", "humidity"), {"battery": "Battery"}) == {
        "battery": "Battery",
        "humidity": "humidity",
        OPTION_NONE: DEVICE_CLASS_NONE_DESCRIPTION,
    }


async def test_other_choices_label_a_class_with_no_name_by_the_class_itself(hass: HomeAssistant, monkeypatch) -> None:
    """The picker lists the other classes the unit fits, each labelled by name or, failing that, by key."""
    sensor = register_unit_sensor(hass, "pct", unit="%", name="Percent")

    async def _no_names(_hass: HomeAssistant) -> dict[str, str]:
        """No class has a name."""
        return {}

    monkeypatch.setattr("custom_components.gutcheck.recipes.device_class_repairs.class_names", _no_names)

    choices = await other_class_choices(hass, SafetyRules(None), {"registry_id": sensor.id, "device_class": "battery"})

    assert choices
    assert all(choice["label"] == choice["value"] for choice in choices)
    assert "battery" not in {choice["value"] for choice in choices}


async def test_a_card_with_one_malformed_field_offers_and_writes_nothing(hass: HomeAssistant) -> None:
    """An unhashable registry id beside a valid class gives no choices and no write."""
    data = {"registry_id": ["x"], "device_class": "battery"}

    assert await other_class_choices(hass, SafetyRules(None), data) == []  # type: ignore[arg-type]
    assert set_device_class(hass, SafetyRules(None), data) is False  # type: ignore[arg-type]


async def _open_flow(hass: HomeAssistant, issue_id: str) -> tuple[dict, object]:
    """Start the repair flow for the issue and return its first result and the manager."""
    assert await async_setup_component(hass, "repairs", {})
    manager = repairs_flow_manager(hass)
    assert manager is not None
    return await manager.async_init(DOMAIN, data={"issue_id": issue_id}), manager


async def test_the_repair_flow_names_its_steps_and_carries_the_cards_text(
    hass: HomeAssistant, device_class_entry: MockConfigEntry
) -> None:
    """The menu is the init step, the picker is the choose step, both show the card's text, and confirm ends with no data."""
    device_class_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(device_class_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    sensor = register_unit_sensor(hass, "pct", unit="%", name="Percent")
    issue_id = f"{DEVICE_CLASS_ISSUE_PREFIX}{sensor.id}"
    placeholders = {"entity_id": sensor.entity_id, "class_name": "Battery", "unit": "%"}
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_DEVICE_CLASS_SUGGESTION,
        translation_placeholders=placeholders,
        data={"registry_id": sensor.id, "device_class": "battery"},
    )

    menu, manager = await _open_flow(hass, issue_id)
    assert menu["step_id"] == "init"
    assert menu["description_placeholders"] == placeholders

    picker = await manager.async_configure(menu["flow_id"], {"next_step_id": "choose"})  # type: ignore[attr-defined]
    assert picker["type"] is FlowResultType.FORM
    assert picker["step_id"] == "choose"
    assert picker["description_placeholders"] == placeholders

    done = await manager.async_configure(picker["flow_id"], {"device_class": "humidity"})  # type: ignore[attr-defined]
    assert done["type"] is FlowResultType.CREATE_ENTRY
    assert done["data"] == {}


async def test_placeholders_are_none_when_the_card_is_gone(hass: HomeAssistant) -> None:
    """A flow left open after its card was swept reads no placeholders instead of failing."""
    flow = SuggestionRepairFlow({}, lambda *_args: True)
    flow.hass = hass
    flow.handler = DOMAIN
    flow.issue_id = "area_swept"

    assert flow._placeholders() is None
