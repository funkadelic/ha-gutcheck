"""A dashboard history card vetoes an exclude card; every dashboard shape is read or safely skipped."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import (
    ATTR_COUNTS,
    ATTR_ITEMS,
    DOMAIN,
    RECIPE_RECORDER_CHURN,
    RECORDER_CHURN_ISSUE_PREFIX,
)
from custom_components.gutcheck.recipes.recorder_churn_const import REASON_HISTORY_CARD

from .conftest import (
    api_response,
    posted_bodies,
    recipe_sensor_entity_id,
    recorder_churn_answer,
    register_jev_responses_by_question,
    register_unit_sensor,
    restart_config_entry,
)

_PATCH = "custom_components.gutcheck.recipes.recorder_churn.async_churn"


def _heavy(hass: HomeAssistant, unique: str) -> er.RegistryEntry:
    """Register one heavy-writer sensor."""
    return register_unit_sensor(hass, unique, unit="W", name=unique.title())


async def _save_default_dashboard(hass: HomeAssistant, cards: list[dict[str, Any]]) -> None:
    """Set up lovelace and save one view holding the given cards as the default dashboard."""
    assert await async_setup_component(hass, "lovelace", {})
    await hass.data[LOVELACE_DATA].dashboards[None].async_save({"views": [{"title": "Home", "cards": cards}]})


async def test_a_history_graph_entity_gets_no_card_before_or_after_a_restart(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, recorder_churn_entry: MockConfigEntry
) -> None:
    """The graphed sensor is sent as on a dashboard and kept; its neighbour is carded; a restart changes nothing."""
    shown, other = _heavy(hass, "shown"), _heavy(hass, "other")
    await _save_default_dashboard(
        hass,
        [
            {"type": "vertical-stack", "cards": [{"type": "history-graph", "entities": [{"entity": shown.entity_id}]}]},
            {"type": "entities", "entities": [other.entity_id]},
        ],
    )
    answer = recorder_churn_answer("exclude", 0.9)
    register_jev_responses_by_question(aioclient_mock, {"r0": api_response({"r0": answer, "r1": answer})})
    recorder_churn_entry.add_to_hass(hass)
    counts = (7, {shown.entity_id: 70_000, other.entity_id: 7_000})
    with patch(_PATCH, AsyncMock(return_value=counts)):
        assert await hass.config_entries.async_setup(recorder_churn_entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)

        bodies = posted_bodies(aioclient_mock)
        assert len(bodies) == 1
        assert [item["on_dashboard"] for item in bodies[0]["state"]["entities"]] == [True, False]
        sensor_id = recipe_sensor_entity_id(hass, recorder_churn_entry, RECIPE_RECORDER_CHURN)

        def _check() -> None:
            """The shown sensor has no card and sits in keep; the other has an active card."""
            registry = ir.async_get(hass)
            assert registry.async_get_issue(DOMAIN, f"{RECORDER_CHURN_ISSUE_PREFIX}{shown.id}") is None
            card = registry.async_get_issue(DOMAIN, f"{RECORDER_CHURN_ISSUE_PREFIX}{other.id}")
            assert card is not None
            assert card.active
            state = hass.states.get(sensor_id)
            assert state is not None
            assert state.state == "1"
            assert state.attributes[ATTR_COUNTS]["exclude"] == 1
            assert state.attributes[ATTR_COUNTS]["keep"] == 1
            [kept] = state.attributes[ATTR_ITEMS]["keep"]
            assert kept["entity_id"] == shown.entity_id
            assert kept["reason"] == REASON_HISTORY_CARD
            assert kept["choice"] == "exclude"
            assert kept["confidence"] == 0.9

        _check()
        await restart_config_entry(hass, recorder_churn_entry)
        assert len(posted_bodies(aioclient_mock)) == 1
        _check()
