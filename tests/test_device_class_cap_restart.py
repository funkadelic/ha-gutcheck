"""Timeline test: a suggestion the per-run cap held back stays held back across a restart."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry, flush_store
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import DEVICE_CLASS_ISSUE_PREFIX, DOMAIN
from custom_components.gutcheck.recipes.device_class import DeviceClassRecipe

from .conftest import api_response, device_class_sensor_entity_id, posted_bodies, register_jev_responses, register_unit_sensor

FIXTURES = Path(__file__).parent / "fixtures" / "captured"

# The captured run may suggest ten or fewer sensors, so the cap is patched
# down to force the held-back path this test proves, rather than relying on
# the capture's own suggested count exceeding the real cap.
CAP = 1


def _load(name: str) -> dict[str, Any]:
    """The captured fixture JSON at `name`, parsed."""
    return json.loads((FIXTURES / name).read_text())


async def _register_captured_sensors(hass: HomeAssistant) -> dict[str, Any]:
    """Register the captured install's asked sensors and return its real answers re-keyed to this run's question ids.

    The recipe asks in entity id order, not the capture's, so each captured
    answer follows its own sensor item to wherever this run asks about it.
    """
    payload = _load("device_class_payload.json")
    captured = _load("device_class_response.json")["answers"]
    for index, item in enumerate(payload["state"]["sensors"]):
        register_unit_sensor(
            hass,
            f"dc{index}",
            unit=item["unit"],
            name=item["name"],
            platform=item["integration"],
            entity_category=er.EntityCategory(item["entity_category"]) if item["entity_category"] else None,
            device_name=item["device_name"],
            manufacturer=item["manufacturer"],
            model=item["model"],
        )
    by_item: dict[str, list[Any]] = {}
    for index, item in enumerate(payload["state"]["sensors"]):
        by_item.setdefault(json.dumps(item, sort_keys=True), []).append(captured[f"s{index}"])
    batch = await DeviceClassRecipe(None).async_prepare(hass)
    return {f"s{index}": by_item[json.dumps(item, sort_keys=True)].pop() for index, item in enumerate(batch.state["sensors"])}


def _card_ids(hass: HomeAssistant) -> set[str]:
    """Every device class card in the issue registry."""
    return {
        issue_id
        for domain, issue_id in ir.async_get(hass).issues
        if domain == DOMAIN and issue_id.startswith(DEVICE_CLASS_ISSUE_PREFIX)
    }


async def test_a_restart_raises_no_card_the_run_held_back_over_the_cap(
    hass: HomeAssistant,
    freezer: Any,
    aioclient_mock: AiohttpClientMocker,
    device_class_entry: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The captured run suggests more sensors than the cap; a restart restores the run's cards and adds none."""
    monkeypatch.setattr("custom_components.gutcheck.recipes.device_class_cards.MAX_NEW_DEVICE_CLASS_CARDS_PER_RUN", CAP)
    freezer.move_to("2026-01-01T00:00:00-08:00")
    register_jev_responses(aioclient_mock, [api_response(await _register_captured_sensors(hass))])
    device_class_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(device_class_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    sensor_id = device_class_sensor_entity_id(hass, device_class_entry)
    state = hass.states.get(sensor_id)
    assert state is not None
    assert len(state.attributes["items"]["suggested"]) > CAP
    assert state.state == str(CAP)
    run_cards = _card_ids(hass)
    assert len(run_cards) == CAP
    posted = len(posted_bodies(aioclient_mock))

    freezer.move_to("2026-01-04T00:00:00-08:00")
    assert await hass.config_entries.async_unload(device_class_entry.entry_id)
    await flush_store(ir.async_get(hass)._store)
    await ir.async_load(hass)
    assert await hass.config_entries.async_setup(device_class_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(posted_bodies(aioclient_mock)) == posted
    assert _card_ids(hass) == run_cards
    state = hass.states.get(sensor_id)
    assert state is not None
    assert state.state == str(CAP)
