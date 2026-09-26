"""Timeline tests: the stuck integration check's cards across a real restart, and restore-time filtering."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import (
    CONFIG_ENTRY_ISSUE_PREFIX,
    DOMAIN,
    ISSUE_CONFIG_ENTRY_DEAD,
    RECIPE_CONFIG_ENTRIES,
    STORE_VERSION,
)
from custom_components.gutcheck.recipes.config_entry_const import CONFIG_ENTRY_OPTIONS, OPTION_DEAD, OPTION_NEEDS_REAUTH
from custom_components.gutcheck.recipes.shapes import recipe_store_key

from .conftest import (
    api_response,
    area_answer,
    posted_bodies,
    press_triage_run,
    register_jev_responses,
    restart_config_entry,
    triage_sensor_entity_id,
)

TRIAGE_STORE_KEY = recipe_store_key(RECIPE_CONFIG_ENTRIES)


def _issue_id(entry_id: str) -> str:
    """The advisory card's issue id for entry_id."""
    return f"{CONFIG_ENTRY_ISSUE_PREFIX}{entry_id}"


def _stored_item(entry_id: str, *, integration: str, first_seen: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """One stored item shaped like a completed run's classified subject, plus any carried fields."""
    item: dict[str, Any] = {
        "entry_id": entry_id,
        "integration": integration,
        "title": integration.title(),
        "first_seen": first_seen,
        "failing_for": "unknown",
    }
    if extra:
        item.update(extra)
    return item


def _seed_store(
    hass_storage: dict[str, Any],
    last_run: str,
    items: dict[str, list[dict[str, Any]]],
    unsure: list[dict[str, Any]] | None = None,
) -> None:
    """Seed the recipe's Store with a completed result, ready for restore."""
    hass_storage[TRIAGE_STORE_KEY] = {
        "version": STORE_VERSION,
        "minor_version": 1,
        "key": TRIAGE_STORE_KEY,
        "data": {
            "last_run": last_run,
            "counts": {option: len(items.get(option, [])) for option in CONFIG_ENTRY_OPTIONS},
            "items": {option: items.get(option, []) for option in CONFIG_ENTRY_OPTIONS},
            "unsure": unsure or [],
            "last_payload": None,
        },
    }


def _seed_stale_issue(hass: HomeAssistant, entry_id: str, *, integration: str) -> None:
    """Create a stale advisory card, as if a run before this restore had raised it."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        _issue_id(entry_id),
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_CONFIG_ENTRY_DEAD,
        translation_placeholders={"title": integration, "integration": integration},
    )


async def test_a_restart_within_the_week_restores_the_card_active_and_untouched(
    hass: HomeAssistant,
    freezer: Any,
    aioclient_mock: AiohttpClientMocker,
    triage_entry: MockConfigEntry,
    failing_entry: Any,
) -> None:
    """A restart posts nothing; the card comes back active, not dismissed, with the same placeholders and link."""
    freezer.move_to("2026-01-01T00:00:00-08:00")
    entry = await failing_entry("restart_hub", ConfigEntryError("device offline"), title="Restart Hub", entry_id="restart_entry")

    register_jev_responses(aioclient_mock, [api_response({"c0": area_answer(OPTION_DEAD, 0.9, CONFIG_ENTRY_OPTIONS)})])
    triage_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(triage_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    issue_id = _issue_id(entry.entry_id)
    registry = ir.async_get(hass)
    before = registry.async_get_issue(DOMAIN, issue_id)
    assert before is not None

    freezer.move_to("2026-01-02T00:00:00-08:00")
    await restart_config_entry(hass, triage_entry)

    assert len(posted_bodies(aioclient_mock)) == 1
    after = registry.async_get_issue(DOMAIN, issue_id)
    assert after is not None
    assert after.active
    assert after.dismissed_version is None
    assert after.translation_placeholders == before.translation_placeholders
    assert after.learn_more_url == before.learn_more_url
    state = hass.states.get(triage_sensor_entity_id(hass, triage_entry))
    assert state is not None
    assert state.state == "1"


async def test_an_ignored_card_stays_ignored_and_uncounted_after_a_restart(
    hass: HomeAssistant,
    freezer: Any,
    aioclient_mock: AiohttpClientMocker,
    triage_entry: MockConfigEntry,
    failing_entry: Any,
) -> None:
    """An ignored card survives a restart, still dismissed and still not counted in the sensor's state."""
    freezer.move_to("2026-01-01T00:00:00-08:00")
    entry = await failing_entry(
        "ignored_restart_hub", ConfigEntryError("device offline"), title="Ignored", entry_id="ignored_restart_entry"
    )

    register_jev_responses(aioclient_mock, [api_response({"c0": area_answer(OPTION_DEAD, 0.9, CONFIG_ENTRY_OPTIONS)})])
    triage_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(triage_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    issue_id = _issue_id(entry.entry_id)
    ir.async_ignore_issue(hass, DOMAIN, issue_id, True)

    freezer.move_to("2026-01-02T00:00:00-08:00")
    await restart_config_entry(hass, triage_entry)

    registry = ir.async_get(hass)
    issue = registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.dismissed_version is not None
    state = hass.states.get(triage_sensor_entity_id(hass, triage_entry))
    assert state is not None
    assert state.state == "0"


async def test_a_pending_entry_kept_from_restore_keeps_its_card_through_a_failed_retry(
    hass: HomeAssistant,
    freezer: Any,
    hass_storage: dict[str, Any],
    aioclient_mock: AiohttpClientMocker,
    triage_entry: MockConfigEntry,
    failing_entry: Any,
) -> None:
    """An entry not yet set up at restore time keeps its item and card; a failed retry once it is set up leaves the card."""
    freezer.move_to("2026-01-01T00:00:00-08:00")
    entry = await failing_entry(
        "pending_hub", ConfigEntryError("device offline"), title="Pending Hub", entry_id="pending_entry", setup=False
    )

    now = dt_util.utcnow().isoformat()
    _seed_store(hass_storage, now, {OPTION_DEAD: [_stored_item(entry.entry_id, integration="pending_hub", first_seen=now)]})

    triage_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(triage_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert posted_bodies(aioclient_mock) == []

    issue_id = _issue_id(entry.entry_id)
    registry = ir.async_get(hass)
    issue = registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.active

    # The entry is now set up for the first time and fails again: the card stays.
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert registry.async_get_issue(DOMAIN, issue_id) is not None
    assert posted_bodies(aioclient_mock) == []


async def test_a_pending_entry_kept_from_restore_clears_live_once_it_loads(
    hass: HomeAssistant,
    freezer: Any,
    hass_storage: dict[str, Any],
    aioclient_mock: AiohttpClientMocker,
    triage_entry: MockConfigEntry,
    failing_entry: Any,
) -> None:
    """An entry not yet set up at restore time keeps its card, which then clears live once it actually loads."""
    freezer.move_to("2026-01-01T00:00:00-08:00")
    entry = await failing_entry("pending_hub2", None, title="Pending Hub 2", entry_id="pending_entry2", setup=False)

    now = dt_util.utcnow().isoformat()
    _seed_store(hass_storage, now, {OPTION_DEAD: [_stored_item(entry.entry_id, integration="pending_hub2", first_seen=now)]})

    triage_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(triage_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    issue_id = _issue_id(entry.entry_id)
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, issue_id) is not None

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert entry.state is ConfigEntryState.LOADED
    assert registry.async_get_issue(DOMAIN, issue_id) is None
    assert posted_bodies(aioclient_mock) == []


async def test_restore_drops_removed_disabled_and_loaded_entries_from_every_bucket_and_unsure(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    aioclient_mock: AiohttpClientMocker,
    triage_entry: MockConfigEntry,
    failing_entry: Any,
) -> None:
    """A removed, a disabled and a loaded entry each drop from every bucket and unsure, recomputing counts, cards gone."""
    removed_entry_id = "gone_config_entry"
    disabled_entry = MockConfigEntry(
        domain="disabled_hub", title="Disabled", entry_id="disabled_config_entry", disabled_by=ConfigEntryDisabler.USER
    )
    disabled_entry.add_to_hass(hass)
    loaded_entry = await failing_entry("loaded_hub", None, title="Loaded", entry_id="loaded_config_entry")
    still_dead = MockConfigEntry(domain="still_dead_hub", title="Still Dead", entry_id="still_dead_config_entry")
    still_dead.add_to_hass(hass)

    for stale_entry_id, integration in (
        (removed_entry_id, "gone_hub"),
        (disabled_entry.entry_id, "disabled_hub"),
        (loaded_entry.entry_id, "loaded_hub"),
    ):
        _seed_stale_issue(hass, stale_entry_id, integration=integration)

    now = dt_util.utcnow().isoformat()
    _seed_store(
        hass_storage,
        now,
        {
            OPTION_DEAD: [
                _stored_item(removed_entry_id, integration="gone_hub", first_seen=now),
                _stored_item(disabled_entry.entry_id, integration="disabled_hub", first_seen=now),
                _stored_item(loaded_entry.entry_id, integration="loaded_hub", first_seen=now),
                _stored_item(still_dead.entry_id, integration="still_dead_hub", first_seen=now),
            ]
        },
        unsure=[_stored_item(disabled_entry.entry_id, integration="disabled_hub", first_seen=now)],
    )

    triage_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(triage_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert posted_bodies(aioclient_mock) == []

    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, _issue_id(removed_entry_id)) is None
    assert registry.async_get_issue(DOMAIN, _issue_id(disabled_entry.entry_id)) is None
    assert registry.async_get_issue(DOMAIN, _issue_id(loaded_entry.entry_id)) is None
    assert registry.async_get_issue(DOMAIN, _issue_id(still_dead.entry_id)) is not None

    state = hass.states.get(triage_sensor_entity_id(hass, triage_entry))
    assert state is not None
    assert state.attributes["counts"][OPTION_DEAD] == 1
    assert {item["entry_id"] for item in state.attributes["items"][OPTION_DEAD]} == {still_dead.entry_id}
    assert state.attributes["unsure"] == []


async def test_a_restored_reauth_skipped_item_keeps_reauth_in_progress_and_raises_no_card(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    aioclient_mock: AiohttpClientMocker,
    triage_entry: MockConfigEntry,
    failing_entry: Any,
) -> None:
    """A restored reauth-in-progress item stays marked as such and never gets a Gut Check card."""
    entry = await failing_entry(
        "reauth_pending_hub", ConfigEntryError("invalid credentials"), title="Reauth Pending", entry_id="reauth_pending_entry"
    )

    now = dt_util.utcnow().isoformat()
    item = _stored_item(
        entry.entry_id, integration="reauth_pending_hub", first_seen=now, extra={"reauth_in_progress": True, "confidence": 1.0}
    )
    _seed_store(hass_storage, now, {OPTION_NEEDS_REAUTH: [item]})

    triage_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(triage_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert posted_bodies(aioclient_mock) == []
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, _issue_id(entry.entry_id)) is None
    state = hass.states.get(triage_sensor_entity_id(hass, triage_entry))
    assert state is not None
    assert state.attributes["items"][OPTION_NEEDS_REAUTH][0]["reauth_in_progress"] is True


async def test_first_seen_survives_a_restart_and_buckets_correctly_on_the_next_run(
    hass: HomeAssistant,
    freezer: Any,
    hass_storage: dict[str, Any],
    aioclient_mock: AiohttpClientMocker,
    triage_entry: MockConfigEntry,
    failing_entry: Any,
) -> None:
    """An entry first seen 8 days ago in a restored result is sent with failing_for 'longer than 1 week', same first_seen."""
    freezer.move_to("2026-01-09T00:00:00-08:00")
    first_seen_at = "2026-01-01T08:00:00+00:00"
    entry = await failing_entry("carried_hub", ConfigEntryError("timed out"), title="Carried Hub", entry_id="carried_entry")

    _seed_store(
        hass_storage,
        dt_util.utcnow().isoformat(),
        {OPTION_DEAD: [_stored_item(entry.entry_id, integration="carried_hub", first_seen=first_seen_at)]},
    )

    triage_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(triage_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert posted_bodies(aioclient_mock) == []

    register_jev_responses(aioclient_mock, [api_response({"c0": area_answer(OPTION_DEAD, 0.9, CONFIG_ENTRY_OPTIONS)})])
    await press_triage_run(hass, triage_entry)

    body = posted_bodies(aioclient_mock)[0]
    assert body["state"]["entries"][0]["failing_for"] == "longer than 1 week"
