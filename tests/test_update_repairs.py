"""Tests for the Repairs issue created from a possibly-breaking update, and its lifecycle."""

from __future__ import annotations

import pytest
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from custom_components.gutcheck.const import (
    DOMAIN,
    OPTION_FEATURE,
    OPTION_NONE,
    OPTION_POSSIBLY_BREAKING,
    OPTION_ROUTINE,
    UPDATES_ISSUE_PREFIX,
)
from custom_components.gutcheck.recipes.updates import UpdateRecipe
from custom_components.gutcheck.repairs import safe_url

from .conftest import update_item, update_result


async def test_possibly_breaking_creates_one_issue_with_a_validated_link(hass: HomeAssistant) -> None:
    """A possibly-breaking answer creates exactly one issue, id keyed on the registry id, link from release_url."""
    result = update_result(
        {
            OPTION_POSSIBLY_BREAKING: [
                update_item("update.a", "reg_a", latest_version="2.0.0", release_url="https://example.com/release")
            ]
        }
    )

    await UpdateRecipe(critical_label=None).async_act(hass, result)

    registry = ir.async_get(hass)
    issue_ids = [issue_id for domain, issue_id in registry.issues if domain == DOMAIN]
    assert issue_ids == [f"{UPDATES_ISSUE_PREFIX}reg_a"]

    issue = registry.async_get_issue(DOMAIN, f"{UPDATES_ISSUE_PREFIX}reg_a")
    assert issue is not None
    assert issue.is_fixable is False
    assert issue.learn_more_url == "https://example.com/release"
    assert issue.translation_placeholders == {"entity_id": "update.a", "latest_version": "2.0.0"}


async def test_routine_feature_and_none_create_no_issue(hass: HomeAssistant) -> None:
    """Only possibly_breaking creates an issue; every other bucket creates none."""
    result = update_result(
        {
            OPTION_ROUTINE: [update_item("update.routine", "reg_r")],
            OPTION_FEATURE: [update_item("update.feature", "reg_f")],
            OPTION_NONE: [update_item("update.none", "reg_n")],
        }
    )

    await UpdateRecipe(critical_label=None).async_act(hass, result)

    assert [issue_id for domain, issue_id in ir.async_get(hass).issues if domain == DOMAIN] == []


@pytest.mark.parametrize("release_url", ["javascript:alert(1)", "https://[::1"])
async def test_unsafe_release_url_produces_an_issue_with_no_link(hass: HomeAssistant, release_url: str) -> None:
    """A script-scheme or malformed release url never becomes a clickable link, and never fails the run."""
    result = update_result({OPTION_POSSIBLY_BREAKING: [update_item("update.a", "reg_a", release_url=release_url)]})

    await UpdateRecipe(critical_label=None).async_act(hass, result)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"{UPDATES_ISSUE_PREFIX}reg_a")
    assert issue is not None
    assert issue.learn_more_url is None


async def test_missing_release_url_produces_an_issue_with_no_link(hass: HomeAssistant) -> None:
    """No release url at all is the same as an unsafe one: no learn_more_url."""
    result = update_result({OPTION_POSSIBLY_BREAKING: [update_item("update.a", "reg_a", release_url=None)]})

    await UpdateRecipe(critical_label=None).async_act(hass, result)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"{UPDATES_ISSUE_PREFIX}reg_a")
    assert issue is not None
    assert issue.learn_more_url is None


async def test_entity_leaving_on_deletes_the_issue_with_no_further_request(hass: HomeAssistant) -> None:
    """The entity leaving STATE_ON (installed, skipped or superseded) clears the card at once."""
    hass.states.async_set("update.a", STATE_ON)
    recipe = UpdateRecipe(critical_label=None)
    await recipe.async_act(hass, update_result({OPTION_POSSIBLY_BREAKING: [update_item("update.a", "reg_a")]}))
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{UPDATES_ISSUE_PREFIX}reg_a") is not None

    hass.states.async_set("update.a", STATE_OFF)
    await hass.async_block_till_done()

    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{UPDATES_ISSUE_PREFIX}reg_a") is None


async def test_a_transient_unavailable_keeps_the_card_until_the_update_is_really_resolved(hass: HomeAssistant) -> None:
    """An entity going unavailable mid-life, as it does on every reload of its integration, must not clear the card."""
    hass.states.async_set("update.a", STATE_ON)
    recipe = UpdateRecipe(critical_label=None)
    await recipe.async_act(hass, update_result({OPTION_POSSIBLY_BREAKING: [update_item("update.a", "reg_a")]}))
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{UPDATES_ISSUE_PREFIX}reg_a") is not None

    hass.states.async_set("update.a", STATE_UNAVAILABLE)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{UPDATES_ISSUE_PREFIX}reg_a") is not None

    hass.states.async_set("update.a", STATE_UNKNOWN)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{UPDATES_ISSUE_PREFIX}reg_a") is not None

    hass.states.async_set("update.a", STATE_ON)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{UPDATES_ISSUE_PREFIX}reg_a") is not None

    hass.states.async_set("update.a", STATE_OFF)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{UPDATES_ISSUE_PREFIX}reg_a") is None


async def test_an_unavailable_entity_at_run_time_keeps_its_card_and_its_dismissal(hass: HomeAssistant) -> None:
    """A run that lands while the entity is unavailable re-arms tracking without sweeping the card or the ignore."""
    hass.states.async_set("update.a", STATE_ON)
    recipe = UpdateRecipe(critical_label=None)
    result = update_result({OPTION_POSSIBLY_BREAKING: [update_item("update.a", "reg_a")]})
    await recipe.async_act(hass, result)
    ir.async_ignore_issue(hass, DOMAIN, f"{UPDATES_ISSUE_PREFIX}reg_a", True)

    hass.states.async_set("update.a", STATE_UNAVAILABLE)
    await recipe.async_act(hass, result)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"{UPDATES_ISSUE_PREFIX}reg_a")
    assert issue is not None
    assert issue.dismissed_version is not None


async def test_a_latest_version_bump_while_still_on_leaves_the_issue_in_place(hass: HomeAssistant) -> None:
    """A new version arriving does not clear the card; only entering off does."""
    hass.states.async_set("update.a", STATE_ON, {"latest_version": "2.0.0"})
    recipe = UpdateRecipe(critical_label=None)
    await recipe.async_act(hass, update_result({OPTION_POSSIBLY_BREAKING: [update_item("update.a", "reg_a")]}))
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{UPDATES_ISSUE_PREFIX}reg_a") is not None

    hass.states.async_set("update.a", STATE_ON, {"latest_version": "3.0.0"})
    await hass.async_block_till_done()

    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{UPDATES_ISSUE_PREFIX}reg_a") is not None


async def test_entity_already_off_before_the_answer_arrived_deletes_the_issue_immediately(hass: HomeAssistant) -> None:
    """A recovery that happened before this run's act() is caught by the immediate sweep, not the next change."""
    hass.states.async_set("update.b", STATE_ON)
    recipe = UpdateRecipe(critical_label=None)
    await recipe.async_act(hass, update_result({OPTION_POSSIBLY_BREAKING: [update_item("update.b", "reg_b")]}))

    hass.states.async_set("update.b", STATE_OFF)

    await recipe.async_act(hass, update_result({OPTION_POSSIBLY_BREAKING: [update_item("update.b", "reg_b")]}))

    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{UPDATES_ISSUE_PREFIX}reg_b") is None


async def test_restored_result_recreates_the_card_and_rearms_clearing_with_no_request(hass: HomeAssistant) -> None:
    """restore() re-creates the card and re-arms recovery, without calling the API."""
    hass.states.async_set("update.a", STATE_ON)
    recipe = UpdateRecipe(critical_label=None)
    result = update_result({OPTION_POSSIBLY_BREAKING: [update_item("update.a", "reg_a")]})

    await recipe.restore(hass, result)
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{UPDATES_ISSUE_PREFIX}reg_a") is not None

    hass.states.async_set("update.a", STATE_OFF)
    await hass.async_block_till_done()

    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{UPDATES_ISSUE_PREFIX}reg_a") is None


async def test_restore_drops_a_newly_critical_entity_from_its_bucket_counts_and_repairs(hass: HomeAssistant) -> None:
    """A restored item whose entity is now labelled critical leaves every bucket, its count and Repairs."""
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("update", "test", "critical_later")
    hass.states.async_set(entry.entity_id, STATE_ON)
    recipe = UpdateRecipe(critical_label="critical")
    result = update_result({OPTION_POSSIBLY_BREAKING: [update_item(entry.entity_id, entry.id)]})
    result["counts"] = {OPTION_POSSIBLY_BREAKING: 1}

    registry.async_update_entity(entry.entity_id, labels={"critical"})
    await recipe.restore(hass, result)

    assert result["items"][OPTION_POSSIBLY_BREAKING] == []
    assert result["counts"][OPTION_POSSIBLY_BREAKING] == 0
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{UPDATES_ISSUE_PREFIX}{entry.id}") is None


async def test_restore_drops_a_newly_critical_entity_from_routine_too(hass: HomeAssistant) -> None:
    """Every bucket is filtered on restore, not only possibly-breaking."""
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("update", "test", "critical_routine")
    recipe = UpdateRecipe(critical_label="critical")
    result = update_result({OPTION_ROUTINE: [update_item(entry.entity_id, entry.id)]})
    result["counts"] = {OPTION_ROUTINE: 1}

    registry.async_update_entity(entry.entity_id, labels={"critical"})
    await recipe.restore(hass, result)

    assert result["items"][OPTION_ROUTINE] == []
    assert result["counts"][OPTION_ROUTINE] == 0


async def test_restore_drops_a_newly_critical_entity_from_unsure_too(hass: HomeAssistant) -> None:
    """The unsure list is also filtered on restore, unlike the health recipe's."""
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("update", "test", "critical_unsure")
    recipe = UpdateRecipe(critical_label="critical")
    result = update_result({})
    result["unsure"] = [update_item(entry.entity_id, entry.id)]

    registry.async_update_entity(entry.entity_id, labels={"critical"})
    await recipe.restore(hass, result)

    assert result["unsure"] == []


async def test_no_placeholder_contains_release_note_text_or_title(hass: HomeAssistant) -> None:
    """Placeholders are the entity id and the latest version only; nothing publisher-written reaches them."""
    release_notes = "This release drops the legacy config format entirely"
    title = "Major overhaul of everything"
    item = update_item("update.a", "reg_a", latest_version="2.0.0")
    item["release_notes"] = release_notes
    item["title"] = title
    result = update_result({OPTION_POSSIBLY_BREAKING: [item]})

    await UpdateRecipe(critical_label=None).async_act(hass, result)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"{UPDATES_ISSUE_PREFIX}reg_a")
    assert issue is not None
    assert issue.translation_placeholders is not None
    for value in issue.translation_placeholders.values():
        assert release_notes not in value
        assert title not in value


def test_safe_url_accepts_only_http_and_https() -> None:
    """Only http and https schemes with a network location are accepted."""
    assert safe_url("https://a.example/x") == "https://a.example/x"
    assert safe_url("http://a.example/x") == "http://a.example/x"
    assert safe_url("javascript:alert(1)") is None
    assert safe_url("data:text/html,x") is None
    assert safe_url("ftp://a.example/x") is None
    assert safe_url("not-a-url") is None
    assert safe_url(None) is None
    assert safe_url("") is None
