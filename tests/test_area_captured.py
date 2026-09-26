"""Tests against a real area-suggestions response captured from the target install."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant

from custom_components.gutcheck.client import validate_response
from custom_components.gutcheck.const import OPTION_NONE, OPTION_SUGGESTED
from custom_components.gutcheck.recipes.area_const import AREA_CONFIDENCE_THRESHOLD, AREA_INSTRUCTIONS
from custom_components.gutcheck.recipes.area_describe import area_criteria
from custom_components.gutcheck.recipes.areas import AreaRecipe
from custom_components.gutcheck.recipes.gate import classify, gate_choice
from custom_components.gutcheck.recipes.shapes import Batch

from .conftest import AreaEntitySpec, area_answer, create_areas, register_area_device

FIXTURES = Path(__file__).parent / "fixtures" / "captured"


def _load(name: str) -> dict[str, Any]:
    """The captured fixture JSON at `name`, parsed."""
    return json.loads((FIXTURES / name).read_text())


def test_area_response_passes_validate_response() -> None:
    """A real captured area-suggestions response satisfies validate_response's documented shape."""
    response = _load("area_response.json")
    assert validate_response(response) == response


def test_the_captured_area_payload_and_response_come_from_the_same_run() -> None:
    """Regenerating one fixture without the other would make the tests below lie."""
    payload = _load("area_payload.json")
    response = _load("area_response.json")
    assert set(payload["questions"]) == set(response["answers"]), (
        "area_payload.json and area_response.json disagree on question ids; recapture both together"
    )


def test_every_area_answer_is_a_choice_with_probabilities_matching_its_question() -> None:
    """Every captured answer's probabilities cover exactly its question's criteria, no more, no less."""
    payload = _load("area_payload.json")
    response = _load("area_response.json")
    for question_id, question in payload["questions"].items():
        answer = response["answers"][question_id]
        assert answer["type"] == "choice"
        assert set(answer["probabilities"]) == set(question["criteria"])


def test_the_captured_area_question_matches_the_current_wording() -> None:
    """A change to the area question's wording leaves the captured pair stale until it is recaptured."""
    payload = _load("area_payload.json")
    first_criteria = next(iter(payload["questions"].values()))["criteria"]
    for index, question in enumerate(payload["questions"].values()):
        assert question["instructions"] == AREA_INSTRUCTIONS.format(index=index)
        assert question["criteria"] == first_criteria


def test_area_answer_builder_matches_a_captured_answer_key_set() -> None:
    """area_answer() builds an answer with the same keys as a real captured choice answer."""
    response = _load("area_response.json")
    captured_answer = next(iter(response["answers"].values()))
    built_answer = area_answer("expected", 0.9, ["Kitchen", "Garage"])
    assert set(built_answer) == set(captured_answer)


def test_captured_area_answers_classify_with_every_device_accounted_for() -> None:
    """classify() places every captured device into suggested or unsure, none dropped, and prints the real spread."""
    payload = _load("area_payload.json")
    response = _load("area_response.json")
    area_names = tuple(name for name in payload["questions"]["d0"]["criteria"] if name != OPTION_NONE)
    subjects = {question_id: {"registry_id": question_id} for question_id in payload["questions"]}
    batch = Batch(state=payload["state"], questions=payload["questions"], subjects=subjects)

    def _gate(answer: object) -> str | None:
        """The area recipe's own gate: a confident, listed area, never none_of_these."""
        return OPTION_SUGGESTED if gate_choice(answer, area_names, AREA_CONFIDENCE_THRESHOLD) is not None else None

    result = classify(batch, response, (OPTION_SUGGESTED,), payload, _gate)  # type: ignore[arg-type]

    suggested = result["items"][OPTION_SUGGESTED]
    unsure = result["unsure"]
    assert len(suggested) + len(unsure) == len(payload["questions"])
    for item in suggested:
        assert item["choice"] in area_names

    confidences = sorted(response["answers"][qid]["confidence"] for qid in payload["questions"])
    print(f"real run: suggested={len(suggested)} unsure={len(unsure)}")
    print(f"real confidence spread: min={confidences[0]} median={confidences[len(confidences) // 2]} max={confidences[-1]}")


async def test_the_real_device_shapes_round_trip_through_async_prepare(hass: HomeAssistant) -> None:
    """Registering devices and areas shaped like the capture reproduces its device items and criteria.

    Order differs from the capture (the recipe asks in device registry id
    order, not the capture's original order), so devices are compared as
    multisets via a canonical JSON form, not by position.
    """
    payload = _load("area_payload.json")
    devices = payload["state"]["devices"]
    area_names = [name for name in payload["questions"]["d0"]["criteria"] if name != OPTION_NONE]
    create_areas(hass, *area_names)

    for index, item in enumerate(devices):
        entity_domains: list[str] = item["entity_domains"]
        device_classes: list[str] = item["device_classes"]
        entities: list[AreaEntitySpec] = [AreaEntitySpec(domain=domain) for domain in entity_domains]
        entities.extend(AreaEntitySpec(domain=entity_domains[0], device_class=device_class) for device_class in device_classes)
        register_area_device(
            hass,
            f"d{index}",
            domain=item["integration"],
            name=item["name"],
            manufacturer=item["manufacturer"],
            model=item["model"],
            entities=entities,
        )

    batch = await AreaRecipe(None).async_prepare(hass)

    assert len(batch.state["devices"]) == len(devices)
    expected = sorted(json.dumps(item, sort_keys=True) for item in devices)
    actual = sorted(json.dumps(item, sort_keys=True) for item in batch.state["devices"])
    assert actual == expected

    expected_criteria = area_criteria({name: name for name in area_names})
    assert all(question["criteria"] == expected_criteria for question in batch.questions.values())
