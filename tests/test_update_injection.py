"""A hostile release note cannot change the question, the outcome, or the card.

Every case here is publisher-written text treated as data. No assertion depends
on what a model returns. The defense is structural, so the assertions are about
what the code builds from a hostile note and what it does with a hostile answer.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.gutcheck.const import (
    DOMAIN,
    MODEL,
    OPTION_POSSIBLY_BREAKING,
    RELEASE_NOTES_MAX_CHARS,
    UPDATE_CONFIDENCE_THRESHOLD,
    UPDATE_OPTIONS,
    UPDATES_ISSUE_PREFIX,
)
from custom_components.gutcheck.describe import clean_release_notes
from custom_components.gutcheck.recipes.gate import classify, gate_score
from custom_components.gutcheck.recipes.shapes import Batch
from custom_components.gutcheck.recipes.updates import UpdateRecipe

from .conftest import load_fixture, update_item, update_result

HOSTILE: dict[str, dict[str, Any]] = load_fixture("injection", "hostile_release_notes.json")
CASE_NAMES = sorted(HOSTILE)

# Deliberately re-derived here rather than imported: a test that reused the
# production patterns would pass even if both sides drifted together.
_TAG = re.compile(r"<[^>]+>")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[[^\]]*\]\([^)]*\)")

# Answer shapes a note could try to talk the model into returning. Only one is
# a well-formed answer inside the scale; every other one must be refused.
HOSTILE_ANSWERS: dict[str, Any] = {
    "accepted_top_level": {"type": "score", "score": 2.0, "confidence": 0.9},
    "beyond_the_last_level": {"type": "score", "score": 3.0, "confidence": 0.99},
    "negative_level": {"type": "score", "score": -1.0, "confidence": 0.99},
    "string_score": {"type": "score", "score": "routine", "confidence": 0.99},
    "no_confidence_at_all": {"type": "score", "score": 0.0},
    "just_below_the_threshold": {"type": "score", "score": 2.0, "confidence": UPDATE_CONFIDENCE_THRESHOLD - 0.01},
    "invented_option_name": {"type": "choice", "choice": "safe_to_auto_install", "confidence": 0.99},
}
REFUSED = {name for name in HOSTILE_ANSWERS if name != "accepted_top_level"}


@pytest.mark.parametrize("case_name", CASE_NAMES)
def test_cleaning_bounds_every_hostile_note(case_name: str) -> None:
    """A hostile note cleans to capped, markup-free, single-spaced plain text."""
    cleaned = clean_release_notes(HOSTILE[case_name]["notes"])

    assert len(cleaned) <= RELEASE_NOTES_MAX_CHARS
    assert _TAG.search(cleaned) is None
    assert _IMAGE.search(cleaned) is None
    assert _LINK.search(cleaned) is None
    assert "  " not in cleaned
    assert "\n" not in cleaned
    assert "\t" not in cleaned
    assert cleaned == cleaned.strip()


def test_a_link_or_image_target_never_survives_cleaning() -> None:
    """Markdown keeps its link text and loses its target, so an injected host never travels."""
    cleaned = clean_release_notes(HOSTILE["markup_wrapper"]["notes"])

    assert "release notes" in cleaned
    assert "evil.example.com" not in cleaned
    assert "cdn.example.com" not in cleaned


def test_an_instruction_hidden_past_the_cap_is_cut() -> None:
    """The cap removes the tail an injection hides in, with no phrase blocklist involved."""
    notes = HOSTILE["instruction_in_the_tail"]["notes"]
    assert "ignore every level" in notes

    cleaned = clean_release_notes(notes)

    assert len(cleaned) == RELEASE_NOTES_MAX_CHARS
    assert "ignore every level" not in cleaned


def test_the_score_gate_refuses_every_hostile_answer_shape() -> None:
    """gate_score yields one of the three listed options or nothing; there is no fourth outcome."""
    for name, answer in HOSTILE_ANSWERS.items():
        chosen = gate_score(answer, UPDATE_OPTIONS, UPDATE_CONFIDENCE_THRESHOLD)
        if name in REFUSED:
            assert chosen is None, name
        else:
            assert chosen == OPTION_POSSIBLY_BREAKING, name


def test_hostile_answers_classify_into_three_buckets_or_unsure_with_nothing_lost() -> None:
    """The count keys are exactly the three options, every refused answer lands in unsure, nothing is invented."""
    subjects = {name: update_item(f"update.{name}", f"reg_{name}") for name in HOSTILE_ANSWERS}
    batch = Batch(state={"updates": []}, questions={}, subjects=subjects)
    payload = {"state": {"updates": []}, "model": MODEL, "questions": {}}
    response = {"model": MODEL, "answers": dict(HOSTILE_ANSWERS), "usage": {"input_tokens": 1, "output_tokens": 0}}

    result = classify(batch, response, UPDATE_OPTIONS, payload, UpdateRecipe(critical_label=None).gate)  # type: ignore[arg-type]

    assert set(result["counts"]) == set(UPDATE_OPTIONS)
    assert set(result["items"]) == set(UPDATE_OPTIONS)
    assert sum(result["counts"].values()) + len(result["unsure"]) == len(HOSTILE_ANSWERS)
    assert {str(item["entity_id"]) for item in result["unsure"]} == {f"update.{name}" for name in REFUSED}
    assert result["counts"][OPTION_POSSIBLY_BREAKING] == 1


@pytest.mark.parametrize("case_name", CASE_NAMES)
async def test_no_part_of_a_hostile_note_reaches_a_repairs_placeholder(hass: HomeAssistant, case_name: str) -> None:
    """Placeholders are the entity id and latest version only; a Repairs card renders Markdown."""
    case = HOSTILE[case_name]
    title = "Publisher controlled title"
    item = update_item("update.a", "reg_a", latest_version="2.0.0", release_url=case["url"])
    item["release_notes"] = clean_release_notes(case["notes"])
    item["title"] = title

    await UpdateRecipe(critical_label=None).async_act(hass, update_result({OPTION_POSSIBLY_BREAKING: [item]}))

    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"{UPDATES_ISSUE_PREFIX}reg_a")
    assert issue is not None
    assert issue.translation_placeholders == {"entity_id": "update.a", "latest_version": "2.0.0"}


async def test_a_script_scheme_release_url_never_becomes_a_card_link(hass: HomeAssistant) -> None:
    """A release url the publisher controls is validated before it can become a clickable link."""
    case = HOSTILE["script_scheme_release_url"]
    assert str(case["url"]).startswith("javascript:")
    item = update_item("update.b", "reg_b", release_url=case["url"])

    await UpdateRecipe(critical_label=None).async_act(hass, update_result({OPTION_POSSIBLY_BREAKING: [item]}))

    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"{UPDATES_ISSUE_PREFIX}reg_b")
    assert issue is not None
    assert issue.learn_more_url is None
