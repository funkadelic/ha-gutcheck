"""Update card helpers: link filtering, name resolution past a removed entity, and the card's text key."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.gutcheck.const import DOMAIN, ISSUE_POSSIBLY_BREAKING_UPDATE, UPDATES_ISSUE_PREFIX
from custom_components.gutcheck.recipes.update_repairs import UpdateIssueTracker, _learn_more_urls, _with_current_names

from .conftest import register_pending_update, update_item


def test_a_release_url_that_is_not_text_is_dropped() -> None:
    """A numeric url from a misbehaving integration gives no link rather than an error."""
    items = [{**update_item("update.a", "ra"), "release_url": 12345}, update_item("update.b", "rb")]

    assert _learn_more_urls(items) == {f"{UPDATES_ISSUE_PREFIX}rb": "https://example.com/release"}


async def test_an_entity_removed_since_the_run_does_not_stop_later_ones_resolving(hass: HomeAssistant) -> None:
    """A finding whose entity is gone keeps its stored id as its name, and the next one still gets its current name."""
    live = register_pending_update(hass, "live", title="Live")
    gone = update_item("update.gone", "missing-registry-id")

    resolved = _with_current_names(hass, [gone, update_item(live.entity_id, live.id)])

    assert [item["entity_id"] for item in resolved] == ["update.gone", live.entity_id]
    assert resolved[0]["name"] == "update.gone"
    assert resolved[1]["name"] == (live.name or live.original_name or live.entity_id)


async def test_the_card_uses_the_possibly_breaking_text_and_shutdown_is_repeatable(hass: HomeAssistant) -> None:
    """The issue carries the update review's translation key, and stopping the tracker twice is harmless."""
    entry = register_pending_update(hass, "tracked")
    tracker = UpdateIssueTracker()

    tracker.sync(hass, [update_item(entry.entity_id, entry.id)])

    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"{UPDATES_ISSUE_PREFIX}{entry.id}")
    assert issue is not None
    assert issue.translation_key == ISSUE_POSSIBLY_BREAKING_UPDATE
    tracker.shutdown()
    tracker.shutdown()
