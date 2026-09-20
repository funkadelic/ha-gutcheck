"""Tests for the Repairs issue created from a health check run, and its sanitizer."""

from __future__ import annotations

import json
from pathlib import Path

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
from custom_components.gutcheck.recipes.health import HealthRecipe
from custom_components.gutcheck.repairs import sanitize_placeholder

from .conftest import health_item, health_result


async def test_only_worth_fixing_creates_an_issue(hass: HomeAssistant) -> None:
    """expected/safe_to_remove/none_of_these produce no issue; worth_fixing produces exactly one."""
    result = health_result(
        {
            OPTION_EXPECTED: [health_item("sensor.expected", "reg_b")],
            OPTION_WORTH_FIXING: [health_item("sensor.worth_fixing", "reg_a")],
            OPTION_SAFE_TO_REMOVE: [health_item("sensor.safe", "reg_c")],
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
    raw_entity_id = "sensor.link[x](y)"
    result = health_result({OPTION_WORTH_FIXING: [health_item(raw_entity_id, "reg_x")]})

    await HealthRecipe(critical_label=None).async_act(hass, result)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"{HEALTH_ISSUE_PREFIX}reg_x")
    assert issue is not None
    assert issue.translation_placeholders is not None
    assert issue.translation_placeholders["entity_id"] == sanitize_placeholder(raw_entity_id)
    assert issue.translation_placeholders["entity_id"] != raw_entity_id


def test_sanitize_placeholder_leaves_an_entity_id_unchanged() -> None:
    assert sanitize_placeholder("sensor.home_123_1min_inverted") == "sensor.home_123_1min_inverted"


def test_sanitize_placeholder_breaks_a_code_span() -> None:
    assert sanitize_placeholder("a`b") == "a\\`b"


def test_sanitize_placeholder_breaks_a_link() -> None:
    assert sanitize_placeholder("[click here](http://evil.example)") == r"\[click here\](http://evil.example)"


def test_sanitize_placeholder_breaks_an_image() -> None:
    assert "![alt]" not in sanitize_placeholder("![alt](http://evil.example/img.png)")


def test_sanitize_placeholder_breaks_html() -> None:
    assert "<script>" not in sanitize_placeholder("<script>alert(1)</script>")


def test_sanitize_placeholder_collapses_a_newline() -> None:
    assert sanitize_placeholder("first line\nsecond line") == "first line second line"


def test_sanitize_placeholder_caps_length() -> None:
    assert len(sanitize_placeholder("x" * 150)) == 100


def test_sanitize_placeholder_never_ends_in_a_dangling_backslash() -> None:
    """Truncating an escaped string can cut a backslash off the character it escapes."""
    for active in "\\`[]<>":
        result = sanitize_placeholder("x" * 99 + active)
        assert result.endswith(f"\\{active}"), f"{active!r} lost its escape to the length cap"


def test_translations_have_the_unavailable_entity_issue() -> None:
    path = Path(__file__).parent.parent / "custom_components" / "gutcheck" / "translations" / "en.json"
    data = json.loads(path.read_text(encoding="utf-8"))

    issue = data["issues"]["unavailable_entity"]
    assert "title" in issue
    assert "description" in issue
    assert "fix_flow" not in issue

    text = issue["title"] + issue["description"]
    assert "{entity_id}" in text
    assert "{unavailable_for}" in text
