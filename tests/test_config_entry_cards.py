"""Both card kinds under one prefix, and the live clear on recovery, disable or removal."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntryDisabler
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import (
    CONFIG_ENTRY_ISSUE_PREFIX,
    DOMAIN,
    ISSUE_CONFIG_ENTRY_DEAD,
    ISSUE_CONFIG_ENTRY_NEEDS_REAUTH,
    OPTION_NONE,
)
from custom_components.gutcheck.recipes.config_entry_const import (
    CONFIG_ENTRY_OPTIONS,
    OPTION_DEAD,
    OPTION_NEEDS_REAUTH,
    OPTION_TRANSIENT,
)
from custom_components.gutcheck.recipes.config_entry_repairs import ConfigEntryIssueTracker

from .conftest import api_response, area_answer, posted_bodies, press_triage_run, register_jev_responses, triage_sensor_entity_id


def _issue_id(entry_id: str) -> str:
    """The advisory card's issue id for entry_id."""
    return f"{CONFIG_ENTRY_ISSUE_PREFIX}{entry_id}"


async def test_two_card_kinds_coexist_under_one_prefix_and_sensor_counts_both(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    triage_entry: MockConfigEntry,
    failing_entry: Any,
) -> None:
    """Needs-reauth and dead each raise their own card, each linking to its own integration's page."""
    # entry_id order (select() sorts on it) drives c0/c1, so ids are prefixed to keep it obvious.
    reauth = await failing_entry(
        "reauth_hub", ConfigEntryError("invalid credentials"), title="Reauth Hub", entry_id="a_reauth_entry"
    )
    dead = await failing_entry("dead_hub", ConfigEntryError("device discontinued"), title="Dead Hub", entry_id="b_dead_entry")

    register_jev_responses(
        aioclient_mock,
        [
            api_response(
                {
                    "c0": area_answer(OPTION_NEEDS_REAUTH, 0.9, CONFIG_ENTRY_OPTIONS),
                    "c1": area_answer(OPTION_DEAD, 0.9, CONFIG_ENTRY_OPTIONS),
                }
            )
        ],
    )
    triage_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(triage_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    registry = ir.async_get(hass)
    reauth_issue = registry.async_get_issue(DOMAIN, _issue_id(reauth.entry_id))
    dead_issue = registry.async_get_issue(DOMAIN, _issue_id(dead.entry_id))
    assert reauth_issue is not None
    assert reauth_issue.translation_key == ISSUE_CONFIG_ENTRY_NEEDS_REAUTH
    assert reauth_issue.learn_more_url == "/config/integrations/integration/reauth_hub"
    assert dead_issue is not None
    assert dead_issue.translation_key == ISSUE_CONFIG_ENTRY_DEAD
    assert dead_issue.learn_more_url == "/config/integrations/integration/dead_hub"

    state = hass.states.get(triage_sensor_entity_id(hass, triage_entry))
    assert state is not None
    assert state.state == "2"


async def test_kind_flip_keeps_dismissed_version(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    triage_entry: MockConfigEntry,
    failing_entry: Any,
) -> None:
    """An ignored needs_reauth card switching to dead keeps its issue id and its dismissal."""
    entry = await failing_entry("flip_hub", ConfigEntryError("invalid credentials"), title="Flip Hub", entry_id="flip_entry")

    register_jev_responses(aioclient_mock, [api_response({"c0": area_answer(OPTION_NEEDS_REAUTH, 0.9, CONFIG_ENTRY_OPTIONS)})])
    triage_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(triage_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    issue_id = _issue_id(entry.entry_id)
    ir.async_ignore_issue(hass, DOMAIN, issue_id, True)

    aioclient_mock.clear_requests()
    register_jev_responses(aioclient_mock, [api_response({"c0": area_answer(OPTION_DEAD, 0.9, CONFIG_ENTRY_OPTIONS)})])
    await press_triage_run(hass, triage_entry)

    registry = ir.async_get(hass)
    issue = registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_key == ISSUE_CONFIG_ENTRY_DEAD
    assert issue.dismissed_version is not None

    state = hass.states.get(triage_sensor_entity_id(hass, triage_entry))
    assert state is not None
    assert state.state == "0"


async def test_transient_and_unsure_raise_no_card_and_clear_a_stale_one(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    triage_entry: MockConfigEntry,
    failing_entry: Any,
) -> None:
    """A dead card is deleted the run after its entry comes back transient, with a confident none raising nothing at all."""
    dead_then_transient = await failing_entry("flaky_hub", ConfigEntryError("timed out"), title="Flaky", entry_id="flaky_entry")
    none_answer_entry = await failing_entry("vague_hub", ConfigEntryError("something odd"), title="Vague", entry_id="vague_entry")

    register_jev_responses(
        aioclient_mock,
        [
            api_response(
                {
                    "c0": area_answer(OPTION_DEAD, 0.9, CONFIG_ENTRY_OPTIONS),
                    "c1": area_answer(OPTION_NONE, 0.9, CONFIG_ENTRY_OPTIONS),
                }
            )
        ],
    )
    triage_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(triage_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, _issue_id(dead_then_transient.entry_id)) is not None
    assert registry.async_get_issue(DOMAIN, _issue_id(none_answer_entry.entry_id)) is None

    aioclient_mock.clear_requests()
    register_jev_responses(
        aioclient_mock,
        [
            api_response(
                {
                    "c0": area_answer(OPTION_TRANSIENT, 0.9, CONFIG_ENTRY_OPTIONS),
                    "c1": area_answer(OPTION_NONE, 0.9, CONFIG_ENTRY_OPTIONS),
                }
            )
        ],
    )
    await press_triage_run(hass, triage_entry)

    assert registry.async_get_issue(DOMAIN, _issue_id(dead_then_transient.entry_id)) is None


async def test_placeholders_never_carry_reason_and_an_empty_or_markdown_title_is_handled(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    triage_entry: MockConfigEntry,
    failing_entry: Any,
) -> None:
    """No placeholder carries the reason text; an empty title falls back to the domain; markdown escapes."""
    blank_title = await failing_entry("blank_hub", ConfigEntryError("a secret reason marker"), title="", entry_id="blank_entry")
    markdown_title = await failing_entry(
        "markdown_hub", ConfigEntryError("offline"), title="[Evil](https://evil.example)", entry_id="markdown_entry"
    )

    register_jev_responses(
        aioclient_mock,
        [
            api_response(
                {
                    "c0": area_answer(OPTION_DEAD, 0.9, CONFIG_ENTRY_OPTIONS),
                    "c1": area_answer(OPTION_DEAD, 0.9, CONFIG_ENTRY_OPTIONS),
                }
            )
        ],
    )
    triage_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(triage_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    registry = ir.async_get(hass)
    blank_issue = registry.async_get_issue(DOMAIN, _issue_id(blank_title.entry_id))
    markdown_issue = registry.async_get_issue(DOMAIN, _issue_id(markdown_title.entry_id))
    assert blank_issue is not None
    assert blank_issue.translation_placeholders == {"title": "blank_hub", "integration": "blank_hub"}
    assert markdown_issue is not None
    assert markdown_issue.translation_placeholders is not None
    assert "a secret reason marker" not in str(blank_issue.translation_placeholders)
    assert "[Evil]" not in markdown_issue.translation_placeholders["title"]
    assert "\\[Evil\\]" in markdown_issue.translation_placeholders["title"]


async def test_a_card_is_never_raised_for_a_gone_disabled_or_loaded_entry_at_sync_time(
    hass: HomeAssistant, failing_entry: Any
) -> None:
    """sync skips an item whose entry no longer exists, is disabled, or has already loaded; a stuck one still gets its card."""
    disabled = await failing_entry("disabled_hub", ConfigEntryError("offline"), title="Disabled", entry_id="disabled_entry")
    assert await hass.config_entries.async_set_disabled_by(disabled.entry_id, ConfigEntryDisabler.USER)
    await failing_entry("loaded_hub", None, title="Loaded", entry_id="loaded_entry")
    await failing_entry("stuck_hub", ConfigEntryError("offline"), title="Stuck", entry_id="stuck_entry")
    await hass.async_block_till_done()

    tracker = ConfigEntryIssueTracker()
    items = [
        {"entry_id": entry_id, "integration": domain, "title": domain, "first_seen": "", "failing_for": ""}
        for entry_id, domain in (
            ("does_not_exist", "ghost_hub"),
            ("disabled_entry", "disabled_hub"),
            ("loaded_entry", "loaded_hub"),
            ("stuck_entry", "stuck_hub"),
        )
    ]
    tracker.sync(hass, [], items)
    tracker.shutdown()

    assert [issue_id for domain, issue_id in ir.async_get(hass).issues if domain == DOMAIN] == [_issue_id("stuck_entry")]


async def test_setup_retry_card_survives_a_failed_retry_and_clears_when_it_succeeds(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    triage_entry: MockConfigEntry,
    failing_entry: Any,
) -> None:
    """The dead card stays through a retry that fails again, and clears live once a retry succeeds, with no extra POST."""
    entry = await failing_entry(
        "retry_hub",
        ConfigEntryNotReady("still offline"),
        ConfigEntryNotReady("still offline"),
        None,
        title="Retry Hub",
        entry_id="retry_entry",
    )

    register_jev_responses(aioclient_mock, [api_response({"c0": area_answer(OPTION_DEAD, 0.9, CONFIG_ENTRY_OPTIONS)})])
    triage_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(triage_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    registry = ir.async_get(hass)
    issue_id = _issue_id(entry.entry_id)
    assert registry.async_get_issue(DOMAIN, issue_id) is not None

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert registry.async_get_issue(DOMAIN, issue_id) is not None
    assert len(posted_bodies(aioclient_mock)) == 1

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=60))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert registry.async_get_issue(DOMAIN, issue_id) is None
    assert len(posted_bodies(aioclient_mock)) == 1


async def test_disabling_and_removing_a_carded_entry_each_delete_its_card_with_no_post(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    triage_entry: MockConfigEntry,
    failing_entry: Any,
) -> None:
    """Disabling one carded entry and removing another each delete its card live, with no further POST."""
    to_disable = await failing_entry(
        "disable_hub", ConfigEntryError("device offline"), title="Disable Hub", entry_id="disable_entry"
    )
    to_remove = await failing_entry("remove_hub", ConfigEntryError("device offline"), title="Remove Hub", entry_id="remove_entry")

    register_jev_responses(
        aioclient_mock,
        [
            api_response(
                {
                    "c0": area_answer(OPTION_DEAD, 0.9, CONFIG_ENTRY_OPTIONS),
                    "c1": area_answer(OPTION_DEAD, 0.9, CONFIG_ENTRY_OPTIONS),
                }
            )
        ],
    )
    triage_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(triage_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(posted_bodies(aioclient_mock)) == 1

    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, _issue_id(to_disable.entry_id)) is not None
    assert registry.async_get_issue(DOMAIN, _issue_id(to_remove.entry_id)) is not None

    assert await hass.config_entries.async_set_disabled_by(to_disable.entry_id, ConfigEntryDisabler.USER)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert registry.async_get_issue(DOMAIN, _issue_id(to_disable.entry_id)) is None

    assert await hass.config_entries.async_remove(to_remove.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert registry.async_get_issue(DOMAIN, _issue_id(to_remove.entry_id)) is None

    assert len(posted_bodies(aioclient_mock)) == 1


async def test_unloading_gut_check_cancels_the_listener_so_a_later_load_leaves_the_card(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    triage_entry: MockConfigEntry,
    failing_entry: Any,
) -> None:
    """After Gut Check unloads, its recovery listener is gone, so a carded entry loading leaves the card in place."""
    entry = await failing_entry(
        "orphan_hub", ConfigEntryError("device offline"), None, title="Orphan Hub", entry_id="orphan_entry"
    )

    register_jev_responses(aioclient_mock, [api_response({"c0": area_answer(OPTION_DEAD, 0.9, CONFIG_ENTRY_OPTIONS)})])
    triage_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(triage_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    registry = ir.async_get(hass)
    issue_id = _issue_id(entry.entry_id)
    assert registry.async_get_issue(DOMAIN, issue_id) is not None

    assert await hass.config_entries.async_unload(triage_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert registry.async_get_issue(DOMAIN, issue_id) is not None


async def test_change_event_for_an_entry_with_no_card_touches_no_issue(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    triage_entry: MockConfigEntry,
    failing_entry: Any,
) -> None:
    """A healthy, unrelated entry loading fires a change the tracker never watched, and touches no issue."""
    kept = await failing_entry("kept_hub", ConfigEntryError("device offline"), title="Kept", entry_id="kept_entry")

    register_jev_responses(aioclient_mock, [api_response({"c0": area_answer(OPTION_DEAD, 0.9, CONFIG_ENTRY_OPTIONS)})])
    triage_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(triage_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    registry = ir.async_get(hass)
    kept_issue_id = _issue_id(kept.entry_id)
    assert registry.async_get_issue(DOMAIN, kept_issue_id) is not None

    # A healthy, unrelated entry loading fires SIGNAL_CONFIG_ENTRY_CHANGED for an
    # entry the tracker never watched; the kept card must stay untouched.
    await failing_entry("unrelated_hub", None, title="Unrelated", entry_id="unrelated_entry")

    assert registry.async_get_issue(DOMAIN, kept_issue_id) is not None
