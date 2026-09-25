"""Unit tests for splitting a run into several requests and merging their answers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from homeassistant.core import HomeAssistant

from custom_components.gutcheck.budget import estimate_tokens, request_fits
from custom_components.gutcheck.const import AREA_INSTRUCTIONS, HEALTH_INSTRUCTIONS, MODEL, UPDATE_INSTRUCTIONS
from custom_components.gutcheck.models import SystemOneRequest, SystemOneResponse
from custom_components.gutcheck.recipes.areas import AreaRecipe
from custom_components.gutcheck.recipes.gate import classify
from custom_components.gutcheck.recipes.health import HealthRecipe
from custom_components.gutcheck.recipes.shapes import Batch
from custom_components.gutcheck.recipes.updates import UpdateRecipe
from custom_components.gutcheck.split import merge, split_batch

from .conftest import create_areas, load_fixture, register_area_device, register_pending_update

LIMIT = "custom_components.gutcheck.budget.REQUEST_TOKEN_LIMIT"


def _captured_batch() -> tuple[Batch, SystemOneRequest, SystemOneResponse]:
    """The captured health pair, with a batch built from its payload."""
    payload = load_fixture("captured", "health_payload.json")
    response = load_fixture("captured", "health_response.json")
    batch = Batch(
        state=payload["state"],
        questions=payload["questions"],
        subjects={question_id: {"entity_id": question_id} for question_id in payload["questions"]},
        list_key="entities",
        template=HEALTH_INSTRUCTIONS,
    )
    return batch, payload, response


def _answering(payload: SystemOneRequest, response: SystemOneResponse) -> SystemOneResponse:
    """The captured response cut down to one request's own answers."""
    answers = {question_id: response["answers"][question_id] for question_id in payload["questions"]}
    return {"model": MODEL, "answers": answers, "usage": {"input_tokens": 1, "output_tokens": 0}}


def _outcome(batch: Batch, response: SystemOneResponse) -> tuple[Any, Any, Any]:
    """Counts, items and unsure from classifying response through the health recipe's own gate and lean."""
    recipe = HealthRecipe(None)
    result = classify(batch, response, recipe.options, [], recipe.gate, lean=recipe.lean)
    return result["counts"], result["items"], result["unsure"]


def test_a_run_that_fits_goes_out_untouched() -> None:
    """A batch that fits one request is sent as its own state and questions, not a re-rendered copy."""
    batch, _payload, _response = _captured_batch()

    payloads = split_batch(batch)

    assert len(payloads) == 1
    assert payloads[0]["state"] is batch.state
    assert payloads[0]["questions"] is batch.questions


def test_captured_run_forced_to_split_keeps_every_subject_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """At half the captured size, each request fits and the slices rebuild the captured run in order."""
    batch, payload, _response = _captured_batch()
    monkeypatch.setattr(LIMIT, estimate_tokens(payload) // 2)

    payloads = split_batch(batch)

    assert len(payloads) >= 2
    assert all(request_fits(request) for request in payloads)
    assert [entity for request in payloads for entity in request["state"]["entities"]] == payload["state"]["entities"]
    assert [question_id for request in payloads for question_id in request["questions"]] == list(payload["questions"])
    for request in payloads:
        for local, (question_id, question) in enumerate(request["questions"].items()):
            assert question["instructions"] == HEALTH_INSTRUCTIONS.format(index=local)
            assert question.get("criteria") == payload["questions"][question_id].get("criteria")


def test_split_answers_classify_the_same_as_the_unsplit_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Merged per-request answers give the same counts, items and unsure as the captured single response."""
    batch, payload, response = _captured_batch()
    monkeypatch.setattr(LIMIT, estimate_tokens(payload) // 2)
    payloads = split_batch(batch)
    assert len(payloads) >= 2

    merged = merge(payloads, [_answering(request, response) for request in payloads])

    assert _outcome(batch, merged) == _outcome(batch, response)


def test_merge_keeps_each_request_to_its_own_answers() -> None:
    """A response cannot overwrite another request's answer; usage sums, and malformed fields do not raise."""
    payloads: list[Any] = [{"questions": {"q0": {}, "q2": {}}}, {"questions": {"q1": {}}}]
    responses: list[Any] = [
        {"answers": {"q0": "first", "q1": "stray"}, "usage": {"input_tokens": 10, "output_tokens": 5}},
        {"model": "other", "answers": {"q0": "stray", "q1": "second"}, "usage": {"input_tokens": 20, "output_tokens": "7"}},
    ]

    merged = merge(payloads, responses)

    assert merged == {
        "model": MODEL,
        "answers": {"q0": "first", "q1": "second"},
        "usage": {"input_tokens": 30, "output_tokens": 5},
    }


async def _area_batch(hass: HomeAssistant) -> Batch:
    """Three unplaced devices and one area."""
    create_areas(hass, "Kitchen")
    for index in range(3):
        register_area_device(hass, f"device_{index}", entities=["sensor"])
    return await AreaRecipe(critical_label=None).async_prepare(hass)


async def _update_batch(hass: HomeAssistant) -> Batch:
    """Three pending updates."""
    for index in range(3):
        register_pending_update(hass, f"update_{index}")
    return await UpdateRecipe(critical_label=None).async_prepare(hass)


@pytest.mark.parametrize(
    ("build", "list_key", "template"),
    [(_area_batch, "devices", AREA_INSTRUCTIONS), (_update_batch, "updates", UPDATE_INSTRUCTIONS)],
)
async def test_areas_and_updates_reindex_through_the_same_split(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    build: Callable[[HomeAssistant], Awaitable[Batch]],
    list_key: str,
    template: str,
) -> None:
    """One token short of the whole run, each recipe splits on its own state list with local indexes."""
    batch = await build(hass)
    monkeypatch.setattr(LIMIT, estimate_tokens({"state": batch.state, "model": MODEL, "questions": batch.questions}) - 1)

    payloads = split_batch(batch)

    assert len(payloads) >= 2
    for request in payloads:
        assert list(request["state"]) == [list_key]
        instructions = [question["instructions"] for question in request["questions"].values()]
        assert instructions == [template.format(index=local) for local in range(len(instructions))]
