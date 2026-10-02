"""Dashboard history cards across several dashboards: an unreadable one is skipped, and every readable one counts."""

from __future__ import annotations

from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.components.lovelace.dashboard import LovelaceStorage
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.gutcheck.recipes.recorder_churn_refs import async_history_card_entity_ids


def _graph(entity_id: str) -> dict:
    """One history graph card over the entity."""
    return {"type": "history-graph", "entities": [entity_id]}


async def _add_dashboard(hass: HomeAssistant, name: str, entity_id: str | None) -> None:
    """Register a storage dashboard called name, saved with a graph of entity_id unless that is None."""
    config = {"id": name, "mode": "storage", "title": name, "url_path": name, "require_admin": False, "show_in_sidebar": True}
    dashboard = LovelaceStorage(hass, config)
    hass.data[LOVELACE_DATA].dashboards[name] = dashboard
    if entity_id is not None:
        await dashboard.async_save({"views": [{"title": name, "cards": [_graph(entity_id)]}]})


async def test_an_unreadable_dashboard_does_not_hide_the_ones_after_it(hass: HomeAssistant) -> None:
    """The default dashboard was never saved and cannot be read; the saved one after it still counts."""
    assert await async_setup_component(hass, "lovelace", {})
    await _add_dashboard(hass, "second", "sensor.on_second")

    assert await async_history_card_entity_ids(hass) == {"sensor.on_second"}


async def test_every_readable_dashboard_adds_its_cards(hass: HomeAssistant) -> None:
    """Two dashboards with a history card each give both entities, not just the last one read."""
    assert await async_setup_component(hass, "lovelace", {})
    await hass.data[LOVELACE_DATA].dashboards[None].async_save({"views": [{"title": "Home", "cards": [_graph("sensor.first")]}]})
    await _add_dashboard(hass, "second", "sensor.second")

    assert await async_history_card_entity_ids(hass) == {"sensor.first", "sensor.second"}
