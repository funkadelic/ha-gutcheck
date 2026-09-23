"""A release-notes fetch that never returns must time out and fall back, not hang the run."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant

from custom_components.gutcheck.recipes.updates import UpdateRecipe

from .conftest import FakeUpdateEntity, install_update_entities, register_pending_update


async def test_a_release_notes_fetch_that_never_returns_falls_back_to_the_summary(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hung release-notes call times out and the run still scores on release_summary alone."""
    monkeypatch.setattr("custom_components.gutcheck.recipes.update_describe.RELEASE_NOTES_FETCH_TIMEOUT", 0.01)
    entry = register_pending_update(hass, release_summary="Fallback summary.")
    install_update_entities(hass, {entry.entity_id: FakeUpdateEntity(hangs=True)})

    batch = await UpdateRecipe(critical_label=None).async_prepare(hass)

    assert batch.state["updates"][0]["release_notes"] == "Fallback summary."
