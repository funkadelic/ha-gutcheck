"""Tests against a real home health check run captured from the target install.

The capture is a list of requests and a list of answers in the same order: the
recipe asks ten entities at a time, so a run of 149 goes out as 15 requests.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.gutcheck.client import validate_response
from custom_components.gutcheck.const import (
    CHOICE_CONFIDENCE_THRESHOLD,
    HEALTH_OPTIONS,
    OPTION_EXPECTED,
    OPTION_SAFE_TO_REMOVE,
    OPTION_WORTH_FIXING,
    SUBJECTS_PER_REQUEST,
)
from custom_components.gutcheck.recipes.gate import classify, gate_choice
from custom_components.gutcheck.recipes.health import HealthRecipe
from custom_components.gutcheck.recipes.health_const import HEALTH_CRITERIA, HEALTH_INSTRUCTIONS, LEAN_NEEDS_ATTENTION
from custom_components.gutcheck.recipes.shapes import Batch
from custom_components.gutcheck.split import split_batch

from .conftest import captured_whole, choice_answer, load_captured

ASKED = 149
SIZES = [SUBJECTS_PER_REQUEST] * 14 + [ASKED - SUBJECTS_PER_REQUEST * 14]
FIELDS = {
    "domain",
    "device_class",
    "integration",
    "unavailable_for",
    "restored",
    "entity_category",
    "device_other_entities_available",
}

# One representative age per captured bucket; the recorder query is the only thing stubbed.
BUCKET_AGE = {
    "less than a day": timedelta(hours=1),
    "1 to 6 days": timedelta(days=3),
    "1 to 4 weeks": timedelta(days=14),
    "more than 4 weeks": timedelta(days=29),
}
ENTRY_STATES = {"loaded": ConfigEntryState.LOADED, "setup retry": ConfigEntryState.SETUP_RETRY}


def test_every_captured_health_response_passes_validate_response() -> None:
    """Every real captured response satisfies validate_response's documented shape."""
    responses = load_captured("health")[1]
    assert len(responses) == len(SIZES)
    for response in responses:
        assert validate_response(response) == response


def test_the_captured_health_payloads_and_responses_come_from_the_same_run() -> None:
    """Regenerating one fixture without the other would make the tests below lie."""
    payloads, responses = load_captured("health")
    assert len(payloads) == len(responses)
    for payload, response in zip(payloads, responses, strict=True):
        assert set(payload["questions"]) == set(response["answers"]), (
            "health_payload.json and health_response.json disagree on question ids; recapture both together"
        )


def test_every_health_answer_is_a_choice_with_probabilities_matching_its_question() -> None:
    """Every captured answer's probabilities cover exactly its own question's criteria, no more, no less."""
    for payload, response in zip(*load_captured("health"), strict=True):
        for question_id, question in payload["questions"].items():
            answer = response["answers"][question_id]
            assert answer["type"] == "choice"
            assert set(answer["probabilities"]) == set(question["criteria"])


def test_the_captured_health_requests_are_ten_entities_at_a_time() -> None:
    """The capture is 149 entities as fourteen requests of ten and one of nine, one question per entity."""
    payloads = load_captured("health")[0]
    assert [len(payload["questions"]) for payload in payloads] == SIZES
    for payload in payloads:
        assert len(payload["state"]["entities"]) == len(payload["questions"])
        assert all(FIELDS <= set(item) <= FIELDS | {"config_entry_state"} for item in payload["state"]["entities"])


def test_the_captured_health_questions_match_the_current_wording() -> None:
    """A change to the health question's wording leaves the captured pair stale until it is recaptured.

    Each request counts its own entities from zero, so the instruction index
    is the question's position within its request, while the key stays global.
    """
    for payload in load_captured("health")[0]:
        for local, question in enumerate(payload["questions"].values()):
            assert question["instructions"] == HEALTH_INSTRUCTIONS.format(index=local)
            assert question["criteria"] == HEALTH_CRITERIA


def test_choice_answer_builder_matches_a_captured_answer_key_set() -> None:
    """choice_answer() builds an answer with the same keys as a real captured choice answer."""
    captured_answer = next(iter(load_captured("health")[1][0]["answers"].values()))
    assert set(choice_answer("expected", 0.9)) == set(captured_answer)


def test_captured_answers_classify_with_every_entity_accounted_for() -> None:
    """classify() places every captured entity into a count bucket or unsure, none dropped; prints the real spread."""
    responses = load_captured("health")[1]
    whole, merged = captured_whole("health", "entities")
    subjects = {question_id: {"entity_id": question_id} for question_id in whole["questions"]}
    batch = Batch(state=whole["state"], questions=whole["questions"], subjects=subjects)

    def _gate(answer: object) -> str | None:
        """The plain choice gate at the health recipe's own threshold."""
        return gate_choice(answer, HEALTH_OPTIONS, CHOICE_CONFIDENCE_THRESHOLD)

    result = classify(batch, merged, HEALTH_OPTIONS, whole, _gate)  # type: ignore[arg-type]

    counts = result["counts"]
    assert (counts[OPTION_EXPECTED], counts[OPTION_WORTH_FIXING], counts[OPTION_SAFE_TO_REMOVE]) == (58, 13, 69)
    assert sum(counts.values()) + len(result["unsure"]) == ASKED
    assert len(result["unsure"]) == 9

    confidences = sorted(answer["confidence"] for response in responses for answer in response["answers"].values())
    input_tokens = sum(response["usage"]["input_tokens"] for response in responses)
    assert (confidences[0], confidences[-1]) == (0.25, 1.0)
    assert input_tokens == 66_045
    print(f"real run: counts={counts} unsure={len(result['unsure'])}")
    print(f"real confidence spread: min={confidences[0]} median={confidences[len(confidences) // 2]} max={confidences[-1]}")
    print(f"real input_tokens: {input_tokens} over {len(responses)} requests")


def test_no_captured_unsure_answer_leans_and_no_confident_entry_carries_a_lean() -> None:
    """None of the nine unsure answers clears the lean threshold on one side; lean stays off confident entries."""
    whole, merged = captured_whole("health", "entities")
    subjects = {question_id: {"entity_id": question_id} for question_id in whole["questions"]}
    batch = Batch(state=whole["state"], questions=whole["questions"], subjects=subjects)
    recipe = HealthRecipe(None)

    result = classify(batch, merged, HEALTH_OPTIONS, whole, recipe.gate, lean=recipe.lean)  # type: ignore[arg-type]

    leans = [item["lean"] for item in result["unsure"] if "lean" in item]
    assert set(leans) <= {LEAN_NEEDS_ATTENTION, OPTION_EXPECTED}
    assert leans == []
    for bucket in result["items"].values():
        assert all("lean" not in item for item in bucket)


def test_the_captured_health_requests_are_what_split_batch_sends() -> None:
    """Rebuilding the capture as one batch and splitting it reproduces the captured requests exactly."""
    payloads = load_captured("health")[0]
    whole = captured_whole("health", "entities")[0]
    batch = Batch(
        state=whole["state"],
        questions=whole["questions"],
        subjects={},
        list_key="entities",
        template=HEALTH_INSTRUCTIONS,
    )
    assert split_batch(batch) == payloads


async def test_the_real_entity_shapes_round_trip_through_prepare_and_split(hass: HomeAssistant) -> None:
    """Registering entities shaped like the capture reproduces its items and request sizes.

    Only the recorder query is stubbed: each entity gets one representative age
    inside its captured duration bucket, so the exact days are not the capture's.
    Order differs from the capture (the recipe asks in entity id order), so
    items are compared as multisets via a canonical JSON form.
    """
    payloads = load_captured("health")[0]
    items = [item for payload in payloads for item in payload["state"]["entities"]]
    entries = {}
    for name, state in ENTRY_STATES.items():
        entries[name] = MockConfigEntry(domain="test")
        entries[name].add_to_hass(hass)
        entries[name].mock_state(hass, state)
    registry = er.async_get(hass)
    device = dr.async_get(hass).async_get_or_create(config_entry_id=entries["loaded"].entry_id, identifiers={("test", "shared")})
    sibling = registry.async_get_or_create("sensor", "test", "sibling", device_id=device.id)
    hass.states.async_set(sibling.entity_id, "1")

    since: dict[str, Any] = {}
    now = dt_util.utcnow()
    for index, item in enumerate(items):
        config_entry = entries.get(item.get("config_entry_state", ""))
        entry = registry.async_get_or_create(
            item["domain"],
            item["integration"],
            f"h{index}",
            config_entry=config_entry,
            device_id=device.id if item["device_other_entities_available"] else None,
            original_device_class=item["device_class"],
            entity_category=EntityCategory(item["entity_category"]) if item["entity_category"] else None,
        )
        if item["restored"]:
            entry.write_unavailable_state(hass)
        else:
            hass.states.async_set(entry.entity_id, STATE_UNAVAILABLE)
        since[entry.entity_id] = now - BUCKET_AGE[item["unavailable_for"]]

    with patch("custom_components.gutcheck.recipes.health.async_unavailable_since", return_value=(40, since)):
        batch = await HealthRecipe(None).async_prepare(hass)

    assert batch.carried == {OPTION_SAFE_TO_REMOVE: []}
    sent = split_batch(batch)
    assert [len(request["questions"]) for request in sent] == SIZES
    for request in sent:
        assert len(request["state"]["entities"]) == len(request["questions"])
        for local, question in enumerate(request["questions"].values()):
            assert question["instructions"] == HEALTH_INSTRUCTIONS.format(index=local)
            assert question["criteria"] == HEALTH_CRITERIA

    expected = sorted(json.dumps(item, sort_keys=True) for item in items)
    actual = sorted(json.dumps(item, sort_keys=True) for request in sent for item in request["state"]["entities"])
    assert actual == expected
