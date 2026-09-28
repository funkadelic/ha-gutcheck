"""Tests for the critical label fix flow's stale-state guards: each aborts, and none of them writes a label."""

from __future__ import annotations

from typing import Any

from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import label_registry as lr
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import (
    CONF_CRITICAL_LABEL,
    CRITICAL_LABEL_ISSUE_PREFIX,
    DOMAIN,
    ISSUE_CRITICAL_LABEL_SUGGESTION,
    RECIPE_CRITICAL_LABEL,
)
from custom_components.gutcheck.recipes.critical_label_const import OPTION_CRITICAL

from .conftest import api_response, critical_label_answer, recipe_sensor_entity_id, register_jev_responses, register_unit_sensor


async def _raise_card(
    hass: HomeAssistant, entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker, unique: str = "water_valve", **kwargs: Any
) -> tuple[er.RegistryEntry, str]:
    """Create the Critical label, register one valve, and raise its card through a real run."""
    lr.async_get(hass).async_create("Critical")
    valve = register_unit_sensor(hass, unique, domain="valve", unit=None, **kwargs)
    register_jev_responses(aioclient_mock, [api_response({"k0": critical_label_answer(OPTION_CRITICAL, 0.9)})])
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    issue_id = f"{CRITICAL_LABEL_ISSUE_PREFIX}{valve.id}"
    return valve, issue_id


async def _open_menu(hass: HomeAssistant, issue_id: str) -> dict[str, Any]:
    """Start the fix flow for issue_id and return its confirm/ignore menu result."""
    assert await async_setup_component(hass, "repairs", {})
    manager = repairs_flow_manager(hass)
    assert manager is not None
    result = await manager.async_init(DOMAIN, data={"issue_id": issue_id})
    assert result["type"] is FlowResultType.MENU
    assert set(result["menu_options"]) == {"confirm", "ignore"}
    return result


async def _confirm(hass: HomeAssistant, issue_id: str) -> dict[str, Any]:
    """Open the fix flow for issue_id and choose confirm from its menu."""
    result = await _open_menu(hass, issue_id)
    manager = repairs_flow_manager(hass)
    assert manager is not None
    return await manager.async_configure(result["flow_id"], {"next_step_id": "confirm"})


async def _ignore(hass: HomeAssistant, issue_id: str) -> dict[str, Any]:
    """Open the fix flow for issue_id and choose ignore from its menu."""
    result = await _open_menu(hass, issue_id)
    manager = repairs_flow_manager(hass)
    assert manager is not None
    return await manager.async_configure(result["flow_id"], {"next_step_id": "ignore"})


async def test_confirm_adds_only_the_one_label_and_leaves_the_device_untouched(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, critical_label_entry: MockConfigEntry
) -> None:
    """The control case: a good confirm unions the label into the entity's own labels and never touches the device."""
    valve, issue_id = await _raise_card(
        hass, critical_label_entry, aioclient_mock, device_name="Water main", labels=frozenset({"plumbing"})
    )

    result = await _confirm(hass, issue_id)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    updated = er.async_get(hass).async_get(valve.entity_id)
    assert updated is not None
    assert updated.labels == {"plumbing", "critical"}
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    assert valve.device_id is not None
    device_entry = dr.async_get(hass).async_get(valve.device_id)
    assert device_entry is not None
    assert device_entry.labels == set()


async def test_confirm_aborts_when_the_entity_was_removed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, critical_label_entry: MockConfigEntry
) -> None:
    """Confirming a card for an entity removed since it was raised aborts as outdated."""
    valve, issue_id = await _raise_card(hass, critical_label_entry, aioclient_mock)
    er.async_get(hass).async_remove(valve.entity_id)

    result = await _confirm(hass, issue_id)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "suggestion_outdated"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    assert er.async_get(hass).async_get(valve.entity_id) is None


async def test_confirm_aborts_when_the_label_was_deleted(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, critical_label_entry: MockConfigEntry
) -> None:
    """Confirming a card after the configured label was deleted from the label registry aborts as outdated."""
    valve, issue_id = await _raise_card(hass, critical_label_entry, aioclient_mock)
    before = er.async_get(hass).async_get(valve.entity_id)
    assert before is not None
    labels_before = set(before.labels)
    lr.async_get(hass).async_delete("critical")

    result = await _confirm(hass, issue_id)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "suggestion_outdated"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    updated = er.async_get(hass).async_get(valve.entity_id)
    assert updated is not None
    assert updated.labels == labels_before


async def test_confirm_aborts_when_the_configured_label_was_switched(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, critical_label_entry: MockConfigEntry
) -> None:
    """Confirming a card after the configured label changed to a different, still-existing label aborts as outdated."""
    valve, issue_id = await _raise_card(hass, critical_label_entry, aioclient_mock)
    other = lr.async_get(hass).async_create("Other")
    hass.config_entries.async_update_entry(
        critical_label_entry, options={**critical_label_entry.options, CONF_CRITICAL_LABEL: other.label_id}
    )

    result = await _confirm(hass, issue_id)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "suggestion_outdated"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    updated = er.async_get(hass).async_get(valve.entity_id)
    assert updated is not None
    assert updated.labels == set()


async def test_confirm_aborts_when_the_label_was_added_to_the_entity_by_hand(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, critical_label_entry: MockConfigEntry
) -> None:
    """Confirming a card for an entity labelled by hand since it was raised aborts without changing its labels."""
    valve, issue_id = await _raise_card(hass, critical_label_entry, aioclient_mock)
    er.async_get(hass).async_update_entity(valve.entity_id, labels={"critical"})

    result = await _confirm(hass, issue_id)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "suggestion_outdated"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    updated = er.async_get(hass).async_get(valve.entity_id)
    assert updated is not None
    assert updated.labels == {"critical"}


async def test_confirm_aborts_when_the_label_was_added_to_the_device_by_hand(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, critical_label_entry: MockConfigEntry
) -> None:
    """Confirming a card for an entity whose device was labelled by hand since it was raised aborts, entity labels untouched."""
    valve, issue_id = await _raise_card(hass, critical_label_entry, aioclient_mock, device_name="Water main")
    assert valve.device_id is not None
    dr.async_get(hass).async_update_device(valve.device_id, labels={"critical"})

    result = await _confirm(hass, issue_id)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "suggestion_outdated"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    updated = er.async_get(hass).async_get(valve.entity_id)
    assert updated is not None
    assert updated.labels == set()
    device_entry = dr.async_get(hass).async_get(valve.device_id)
    assert device_entry is not None
    assert device_entry.labels == {"critical"}


async def test_confirm_aborts_when_the_entity_was_disabled(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, critical_label_entry: MockConfigEntry
) -> None:
    """Confirming a card for an entity disabled since it was raised aborts as outdated."""
    valve, issue_id = await _raise_card(hass, critical_label_entry, aioclient_mock)
    er.async_get(hass).async_update_entity(valve.entity_id, disabled_by=er.RegistryEntryDisabler.USER)

    result = await _confirm(hass, issue_id)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "suggestion_outdated"
    updated = er.async_get(hass).async_get(valve.entity_id)
    assert updated is not None
    assert updated.labels == set()


async def test_confirm_aborts_when_card_data_lacks_label_id(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, critical_label_entry: MockConfigEntry
) -> None:
    """A card whose data holds only the registry id aborts rather than raising, since there is no label id to re-check."""
    lr.async_get(hass).async_create("Critical")
    valve = register_unit_sensor(hass, "water_valve", domain="valve", unit=None)
    register_jev_responses(aioclient_mock, [api_response({})])
    critical_label_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(critical_label_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    issue_id = f"{CRITICAL_LABEL_ISSUE_PREFIX}malformed"
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_CRITICAL_LABEL_SUGGESTION,
        translation_placeholders={"entity_id": valve.entity_id, "label_name": "Critical"},
        data={"registry_id": valve.id},
    )

    result = await _confirm(hass, issue_id)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "suggestion_outdated"
    updated = er.async_get(hass).async_get(valve.entity_id)
    assert updated is not None
    assert updated.labels == set()


async def test_ignore_aborts_as_suggestion_ignored_and_drops_the_sensor_count(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, critical_label_entry: MockConfigEntry
) -> None:
    """Choosing ignore aborts as suggestion_ignored and the recipe's open card count drops by one."""
    valve, issue_id = await _raise_card(hass, critical_label_entry, aioclient_mock)
    state = hass.states.get(recipe_sensor_entity_id(hass, critical_label_entry, RECIPE_CRITICAL_LABEL))
    assert state is not None
    assert state.state == "1"

    result = await _ignore(hass, issue_id)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "suggestion_ignored"
    updated = er.async_get(hass).async_get(valve.entity_id)
    assert updated is not None
    assert updated.labels == set()
    state = hass.states.get(recipe_sensor_entity_id(hass, critical_label_entry, RECIPE_CRITICAL_LABEL))
    assert state is not None
    assert state.state == "0"
