"""Health recipe edges: the lean at its sum limit, the safety count it reports, restored cards, and shutdown."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from custom_components.gutcheck.const import (
    DOMAIN,
    HEALTH_ISSUE_PREFIX,
    ISSUE_UNAVAILABLE_ENTITY,
    OPTION_EXPECTED,
    OPTION_WORTH_FIXING,
    PROBABILITY_ROUNDING_ALLOWANCE,
)
from custom_components.gutcheck.recipes.health import HealthRecipe
from custom_components.gutcheck.recipes.health_const import LEAN_NEEDS_ATTENTION

from .conftest import health_item, health_result, register_unavailable_entity


def test_a_spread_summing_exactly_to_the_rounding_allowance_still_leans() -> None:
    """Only a sum past 1 plus the allowance is malformed; at it the needs-attention side still wins."""
    # Both operands are close enough that the subtraction and the later addition are exact.
    expected = (1.0 + PROBABILITY_ROUNDING_ALLOWANCE) - 0.75
    answer = {
        "type": "choice",
        "choice": OPTION_WORTH_FIXING,
        "confidence": 0.4,
        "probabilities": {OPTION_WORTH_FIXING: 0.75, OPTION_EXPECTED: expected},
    }
    assert 0.75 + expected == 1.0 + PROBABILITY_ROUNDING_ALLOWANCE

    assert HealthRecipe(None).lean(answer) == LEAN_NEEDS_ATTENTION


async def test_prepare_reports_how_many_entities_the_safety_rules_excluded(hass: HomeAssistant, caplog) -> None:
    """Two blocked-domain entities and one selectable entity log excluded=2 selected=1."""
    caplog.set_level(logging.DEBUG, logger="custom_components.gutcheck.recipes.health")
    registry = er.async_get(hass)
    registry.async_get_or_create("lock", "test", "front_door")
    registry.async_get_or_create("alarm_control_panel", "test", "panel")
    register_unavailable_entity(hass)

    await HealthRecipe(None).async_prepare(hass)

    assert "selected=1" in caplog.text
    assert "excluded_by_safety_rules=2" in caplog.text


async def test_a_restored_finding_raises_its_card_with_the_health_translation(hass: HomeAssistant) -> None:
    """Restoring a worth-fixing finding recreates its issue under the unavailable-entity text."""
    recipe = HealthRecipe(None)
    entity_id = register_unavailable_entity(hass)
    registry_id = er.async_get(hass).async_get(entity_id).id  # type: ignore[union-attr]
    result = health_result({OPTION_WORTH_FIXING: [health_item(entity_id, registry_id)]})

    await recipe.restore(hass, result)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"{HEALTH_ISSUE_PREFIX}{registry_id}")
    assert issue is not None
    assert issue.translation_key == ISSUE_UNAVAILABLE_ENTITY
    recipe.shutdown()


async def test_shutdown_can_be_called_again_after_a_recovery_watch_was_cleared(hass: HomeAssistant) -> None:
    """A second shutdown, after the watch was already dropped, does nothing and does not fail."""
    recipe = HealthRecipe(None)
    entity_id = register_unavailable_entity(hass)
    registry_id = er.async_get(hass).async_get(entity_id).id  # type: ignore[union-attr]
    await recipe.async_act(hass, health_result({OPTION_WORTH_FIXING: [health_item(entity_id, registry_id)]}))

    recipe.shutdown()
    recipe.shutdown()
