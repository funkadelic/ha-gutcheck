"""What a restart re-checks before it reopens an exclude card, with no API call."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.energy.data import async_get_manager
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.entityfilter import INCLUDE_EXCLUDE_BASE_FILTER_SCHEMA, convert_include_exclude_filter
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import (
    ATTR_COUNTS,
    ATTR_ITEMS,
    DOMAIN,
    RECIPE_RECORDER_CHURN,
    RECORDER_CHURN_ISSUE_PREFIX,
    STORE_VERSION,
)
from custom_components.gutcheck.recipes.recorder_churn_const import (
    REASON_ENERGY,
    REASON_HISTORY_CARD,
    RECORDER_CHURN_OPTIONS,
)
from custom_components.gutcheck.recipes.shapes import recipe_store_key

from .conftest import (
    posted_bodies,
    recipe_sensor_entity_id,
    recorder_churn_answer,
    register_jev_answers,
    register_unit_sensor,
    restart_config_entry,
)

_PATCH = "custom_components.gutcheck.recipes.recorder_churn.async_churn"


def _two_heavy(hass: HomeAssistant) -> tuple[er.RegistryEntry, er.RegistryEntry]:
    """Register two heavy-writer sensors, the first the heavier."""
    return (
        register_unit_sensor(hass, "first", unit="W", name="First"),
        register_unit_sensor(hass, "second", unit="W", name="Second"),
    )


async def _run_twice_excluded(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry, sensors: tuple[er.RegistryEntry, ...]
) -> None:
    """Set the entry up with both sensors answered exclude and assert both cards open after the one request."""
    register_jev_answers(
        aioclient_mock, {"r0": recorder_churn_answer("exclude", 0.9), "r1": recorder_churn_answer("exclude", 0.9)}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(posted_bodies(aioclient_mock)) == 1
    assert all(_card(hass, sensor) is not None for sensor in sensors)


def _card(hass: HomeAssistant, sensor: er.RegistryEntry) -> ir.IssueEntry | None:
    """The sensor's exclude card, or None."""
    return ir.async_get(hass).async_get_issue(DOMAIN, f"{RECORDER_CHURN_ISSUE_PREFIX}{sensor.id}")


def _counts(*sensors: er.RegistryEntry) -> tuple[int, dict[str, int]]:
    """A 7-day count with the first sensor heaviest."""
    return 7, {sensor.entity_id: 70_000 - index * 700 for index, sensor in enumerate(sensors)}


async def _assert_moved_to_keep(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry, moved: er.RegistryEntry, reason: str
) -> None:
    """After the restart: no new request, the moved sensor kept with its reason and no card, the other still carded."""
    assert len(posted_bodies(aioclient_mock)) == 1
    assert _card(hass, moved) is None
    state = hass.states.get(recipe_sensor_entity_id(hass, entry, RECIPE_RECORDER_CHURN))
    assert state is not None
    assert state.state == "1"
    assert state.attributes[ATTR_COUNTS]["exclude"] == 1
    [kept] = state.attributes[ATTR_ITEMS]["keep"]
    assert (kept["entity_id"], kept["reason"]) == (moved.entity_id, reason)


async def test_an_entity_the_recorder_stopped_recording_gets_no_card_after_the_restart(
    recorder_mock: Any,
    enable_custom_integrations: None,
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    recorder_churn_entry: MockConfigEntry,
) -> None:
    """The user follows the card, excludes the entity and restarts: its card stays gone, the other card reopens."""
    followed, other = _two_heavy(hass)
    with patch(_PATCH, AsyncMock(return_value=_counts(followed, other))):
        await _run_twice_excluded(hass, aioclient_mock, recorder_churn_entry, (followed, other))

        exclude = INCLUDE_EXCLUDE_BASE_FILTER_SCHEMA({"exclude": {"entities": [followed.entity_id]}})
        recorder_mock.entity_filter = convert_include_exclude_filter(exclude).get_filter()
        await restart_config_entry(hass, recorder_churn_entry)

    assert len(posted_bodies(aioclient_mock)) == 1
    assert _card(hass, followed) is None
    card = _card(hass, other)
    assert card is not None
    assert card.active


async def test_an_entity_added_to_energy_after_the_run_loses_its_card_on_restart(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, recorder_churn_entry: MockConfigEntry
) -> None:
    """Energy preferences saved after the run move the entity to keep at the next restore."""
    meter, other = _two_heavy(hass)
    with patch(_PATCH, AsyncMock(return_value=_counts(meter, other))):
        await _run_twice_excluded(hass, aioclient_mock, recorder_churn_entry, (meter, other))

        manager = await async_get_manager(hass)
        await manager.async_update({"device_consumption": [{"stat_consumption": meter.entity_id}]})
        await restart_config_entry(hass, recorder_churn_entry)

    await _assert_moved_to_keep(hass, aioclient_mock, recorder_churn_entry, meter, REASON_ENERGY)


async def test_a_removed_entity_stays_in_exclude_on_restart_even_if_energy_still_names_it(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, recorder_churn_entry: MockConfigEntry
) -> None:
    """A stale Energy reference to a removed entity does not move it to keep; it just loses its card."""
    gone, other = _two_heavy(hass)
    with patch(_PATCH, AsyncMock(return_value=_counts(gone, other))):
        await _run_twice_excluded(hass, aioclient_mock, recorder_churn_entry, (gone, other))

        manager = await async_get_manager(hass)
        await manager.async_update({"device_consumption": [{"stat_consumption": gone.entity_id}]})
        er.async_get(hass).async_remove(gone.entity_id)
        await restart_config_entry(hass, recorder_churn_entry)

    assert len(posted_bodies(aioclient_mock)) == 1
    assert _card(hass, gone) is None
    state = hass.states.get(recipe_sensor_entity_id(hass, recorder_churn_entry, RECIPE_RECORDER_CHURN))
    assert state is not None
    assert state.attributes[ATTR_COUNTS]["exclude"] == 2
    assert state.attributes[ATTR_ITEMS]["keep"] == []


async def test_an_entity_graphed_after_the_run_loses_its_card_on_restart(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, recorder_churn_entry: MockConfigEntry
) -> None:
    """A history graph saved after the run moves the entity to keep at the next restore."""
    graphed, other = _two_heavy(hass)
    with patch(_PATCH, AsyncMock(return_value=_counts(graphed, other))):
        await _run_twice_excluded(hass, aioclient_mock, recorder_churn_entry, (graphed, other))

        assert await async_setup_component(hass, "lovelace", {})
        cards = [{"type": "history-graph", "entities": [graphed.entity_id]}]
        await hass.data[LOVELACE_DATA].dashboards[None].async_save({"views": [{"cards": cards}]})
        await restart_config_entry(hass, recorder_churn_entry)

    await _assert_moved_to_keep(hass, aioclient_mock, recorder_churn_entry, graphed, REASON_HISTORY_CARD)


async def test_a_failed_energy_read_on_restart_keeps_the_stored_cards_and_loads(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    recorder_churn_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unreadable Energy store at restart skips the re-check: the entry loads and both cards reopen."""
    sensors = _two_heavy(hass)
    with patch(_PATCH, AsyncMock(return_value=_counts(*sensors))):
        await _run_twice_excluded(hass, aioclient_mock, recorder_churn_entry, sensors)
        with patch(
            "custom_components.gutcheck.recipes.recorder_churn_keep.async_energy_entity_ids",
            AsyncMock(side_effect=HomeAssistantError("corrupt store detail")),
        ):
            await restart_config_entry(hass, recorder_churn_entry)

    assert recorder_churn_entry.state is ConfigEntryState.LOADED
    assert len(posted_bodies(aioclient_mock)) == 1
    assert all(_card(hass, sensor) is not None for sensor in sensors)
    assert "HomeAssistantError" in caplog.text
    assert "corrupt store detail" not in caplog.text


@pytest.mark.parametrize("missing", [None, "entity_id", "registry_id", "bucket"])
async def test_a_stored_exclude_item_missing_a_read_field_is_rejected_without_failing_setup(
    hass: HomeAssistant, hass_storage: dict[str, Any], recorder_churn_entry: MockConfigEntry, missing: str | None
) -> None:
    """A complete stored item restores its card; one missing a field restore reads falls back to a fresh run."""
    sensor = register_unit_sensor(hass, "stored", unit="W", name="Stored")
    item: dict[str, Any] = {
        "entity_id": sensor.entity_id,
        "registry_id": sensor.id,
        "changes_per_day": 9_000,
        "bucket": "very heavy",
        "confidence": 0.9,
    }
    item.pop(missing or "", None)
    key = recipe_store_key(RECIPE_RECORDER_CHURN)
    items = {option: [item] if option == "exclude" else [] for option in RECORDER_CHURN_OPTIONS}
    hass_storage[key] = {
        "version": STORE_VERSION,
        "minor_version": 1,
        "key": key,
        "data": {
            "last_run": dt_util.utcnow().isoformat(),
            "counts": {option: len(bucket) for option, bucket in items.items()},
            "items": items,
            "unsure": [],
            "last_payload": None,
        },
    }
    recorder_churn_entry.add_to_hass(hass)
    with patch(_PATCH, AsyncMock(return_value=(7, {}))):
        assert await hass.config_entries.async_setup(recorder_churn_entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)

    assert recorder_churn_entry.state is ConfigEntryState.LOADED
    assert (_card(hass, sensor) is not None) is (missing is None)
