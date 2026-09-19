"""Tests for the Repairs issue created from a health check run, and its sanitizer."""

from __future__ import annotations

import json

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.gutcheck.const import (
    DOMAIN,
    HEALTH_ISSUE_PREFIX,
    ISSUE_UNAVAILABLE_ENTITY,
    OPTION_EXPECTED,
    OPTION_SAFE_TO_REMOVE,
    OPTION_WORTH_FIXING,
)
from custom_components.gutcheck.recipes.base import Item, RecipeResult
from custom_components.gutcheck.recipes.health import HealthRecipe
from custom_components.gutcheck.repairs import sanitize_placeholder


def _item(entity_id: str, registry_id: str, unavailable_for: str = "1 to 6 days") -> Item:
    return {
        "entity_id": entity_id,
        "registry_id": registry_id,
        "restored": False,
        "confidence": 0.9,
        "unavailable_for": unavailable_for,
    }


def _result(items: dict[str, list[Item]]) -> RecipeResult:
    return {"last_run": "", "counts": {}, "items": items, "unsure": [], "last_payload": None}


async def test_only_worth_fixing_creates_an_issue(hass: HomeAssistant) -> None:
    """expected/safe_to_remove/none_of_these produce no issue; worth_fixing produces exactly one."""
    result = _result(
        {
            OPTION_EXPECTED: [_item("sensor.expected", "reg_b")],
            OPTION_WORTH_FIXING: [_item("sensor.worth_fixing", "reg_a")],
            OPTION_SAFE_TO_REMOVE: [_item("sensor.safe", "reg_c")],
        }
    )

    await HealthRecipe(critical_label=None).async_act(hass, result)

    registry = ir.async_get(hass)
    issue_ids = [issue_id for domain, issue_id in registry.issues if domain == DOMAIN]
    assert issue_ids == [f"{HEALTH_ISSUE_PREFIX}reg_a"]

    issue = registry.async_get_issue(DOMAIN, f"{HEALTH_ISSUE_PREFIX}reg_a")
    assert issue is not None
    assert issue.severity == ir.IssueSeverity.WARNING
    assert issue.is_fixable is False
    assert issue.is_persistent is False
    assert issue.translation_key == ISSUE_UNAVAILABLE_ENTITY
    assert issue.translation_placeholders == {
        "entity_id": sanitize_placeholder("sensor.worth_fixing"),
        "unavailable_for": "1 to 6 days",
    }


async def test_placeholders_are_sanitized(hass: HomeAssistant) -> None:
    """A placeholder reaches the issue only after sanitize_placeholder."""
    raw_entity_id = "sensor.under_score*bold"
    result = _result({OPTION_WORTH_FIXING: [_item(raw_entity_id, "reg_x")]})

    await HealthRecipe(critical_label=None).async_act(hass, result)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"{HEALTH_ISSUE_PREFIX}reg_x")
    assert issue is not None
    assert issue.translation_placeholders is not None
    assert issue.translation_placeholders["entity_id"] == sanitize_placeholder(raw_entity_id)
    assert issue.translation_placeholders["entity_id"] != raw_entity_id


def test_sanitize_placeholder_breaks_a_link() -> None:
    assert "](http" not in sanitize_placeholder("[click here](http://evil.example)")


def test_sanitize_placeholder_breaks_an_image() -> None:
    assert "![alt]" not in sanitize_placeholder("![alt](http://evil.example/img.png)")


def test_sanitize_placeholder_breaks_html() -> None:
    assert "<script>" not in sanitize_placeholder("<script>alert(1)</script>")


def test_sanitize_placeholder_collapses_a_newline() -> None:
    assert sanitize_placeholder("first line\nsecond line") == "first line second line"


def test_sanitize_placeholder_caps_length() -> None:
    assert len(sanitize_placeholder("x" * 150)) == 100


def test_translations_have_the_unavailable_entity_issue() -> None:
    with open("custom_components/gutcheck/translations/en.json", encoding="utf-8") as handle:
        data = json.load(handle)

    issue = data["issues"]["unavailable_entity"]
    assert "title" in issue
    assert "description" in issue
    assert "fix_flow" not in issue

    text = issue["title"] + issue["description"]
    assert "{entity_id}" in text
    assert "{unavailable_for}" in text
