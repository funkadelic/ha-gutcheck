"""Tests for classify()'s choice-carrying, and the area recipe's own confidence gate."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from custom_components.gutcheck.const import OPTION_SUGGESTED
from custom_components.gutcheck.recipes.area_const import AREA_CONFIDENCE_THRESHOLD
from custom_components.gutcheck.recipes.areas import AreaRecipe
from custom_components.gutcheck.recipes.gate import classify
from custom_components.gutcheck.recipes.shapes import Batch

from .conftest import area_answer, create_areas, register_area_device


def _choice_question(criteria: dict[str, str | None]) -> dict[str, object]:
    """A minimal choice question carrying the given criteria."""
    return {"type": "choice", "instructions": "pick one", "criteria": criteria}


def _score_question() -> dict[str, object]:
    """A minimal score question, for the "never for a score question" case."""
    return {"type": "score", "instructions": "score it", "criteria": ["a", "b"]}


def _answer(choice: object, confidence: float = 0.9) -> dict[str, object]:
    """A choice-shaped answer carrying the given (possibly malformed) choice."""
    return {"type": "choice", "choice": choice, "confidence": confidence, "probabilities": {}}


def _classify_unsure(questions: dict[str, object], answer: dict[str, object]) -> dict[str, object]:
    """Run classify() with a gate that always calls unsure, and return the one resulting entry."""
    subjects = {"d0": {"registry_id": "dev_a"}}
    batch = Batch(state={}, questions=questions, subjects=subjects)
    response = {"model": "jev-latest", "answers": {"d0": answer}, "usage": {"input_tokens": 5, "output_tokens": 0}}
    payload = {"state": {}, "model": "jev-latest", "questions": {}}
    result = classify(batch, response, (), payload, lambda _answer: None)
    return result["unsure"][0]


def test_classify_carries_a_choice_that_is_one_of_the_criteria_keys() -> None:
    """A choice listed in the asked question's own criteria rides along on the classified entry."""
    subjects = {"d0": {"registry_id": "dev_a"}}
    questions = {"d0": _choice_question({"Kitchen": None, "Garage": None})}
    batch = Batch(state={}, questions=questions, subjects=subjects)
    response = {
        "model": "jev-latest",
        "answers": {"d0": _answer("Kitchen")},
        "usage": {"input_tokens": 5, "output_tokens": 0},
    }
    payload = {"state": {}, "model": "jev-latest", "questions": {}}

    result = classify(batch, response, (OPTION_SUGGESTED,), payload, lambda _answer: OPTION_SUGGESTED)

    assert result["items"][OPTION_SUGGESTED][0]["choice"] == "Kitchen"


def test_classify_does_not_carry_a_choice_outside_the_criteria() -> None:
    """A choice absent from the asked question's own criteria is not carried onto the entry."""
    entry = _classify_unsure({"d0": _choice_question({"Kitchen": None})}, _answer("Attic"))

    assert "choice" not in entry


def test_classify_does_not_raise_on_a_list_valued_choice() -> None:
    """An unhashable choice value is ignored rather than raising inside the criteria membership check."""
    entry = _classify_unsure({"d0": _choice_question({"Kitchen": None})}, _answer(["Kitchen"]))

    assert "choice" not in entry


def test_classify_never_adds_a_choice_for_a_score_question() -> None:
    """A score question's answer never gains a choice field, whatever it contains."""
    score_answer = {"type": "score", "score": 1.0, "legend": {}, "probabilities": {}, "confidence": 0.9}
    entry = _classify_unsure({"d0": _score_question()}, score_answer)

    assert "choice" not in entry


async def test_gate_rejects_everything_before_any_prepare(hass: HomeAssistant) -> None:
    """With no prepare ever run, every answer is rejected: there is nothing yet to allow."""
    recipe = AreaRecipe(critical_label=None)

    assert recipe.gate(area_answer("Kitchen", 0.99, ["Kitchen"])) is None


async def test_gate_accepts_a_listed_area_at_the_threshold_and_rejects_just_below(hass: HomeAssistant) -> None:
    """After a prepare with real areas, a listed area at exactly the threshold is accepted; a hair below is not."""
    create_areas(hass, "Kitchen", "Garage")
    register_area_device(hass, "plug", entities=["sensor"])
    recipe = AreaRecipe(critical_label=None)
    await recipe.async_prepare(hass)

    assert recipe.gate(area_answer("Kitchen", AREA_CONFIDENCE_THRESHOLD, ["Kitchen", "Garage"])) == OPTION_SUGGESTED
    assert recipe.gate(area_answer("Kitchen", AREA_CONFIDENCE_THRESHOLD - 0.0001, ["Kitchen", "Garage"])) is None
