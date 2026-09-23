"""Hostile text in any publisher-controlled field must be treated the same way.

Delivery field is orthogonal to case: whichever field a publisher writes into
(release notes, release summary, or title), the fixed question never changes
and the text lands where the field's own cleaning rule says it should.
"""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.core import HomeAssistant

from custom_components.gutcheck.const import UPDATE_CRITERIA, UPDATE_INSTRUCTIONS
from custom_components.gutcheck.describe import clean_release_notes
from custom_components.gutcheck.recipes.updates import UpdateRecipe

from .conftest import FakeUpdateEntity, install_update_entities, load_fixture, register_pending_update

HOSTILE: dict[str, dict[str, Any]] = load_fixture("injection", "hostile_release_notes.json")
CASE_NAMES = sorted(HOSTILE)

BENIGN_NOTES = "Maintenance release. One dependency bump and refreshed translations. Nothing else changed."

# Maps a delivery field to the state key its text ends up under.
FIELD_KEYS: dict[str, str] = {"notes": "release_notes", "release_summary": "release_summary", "title": "title"}
# Fields cleaned by clean_release_notes before reaching the model. title is not.
CLEANED_KEYS = {"release_notes", "release_summary"}


@pytest.mark.parametrize("field", sorted(FIELD_KEYS))
@pytest.mark.parametrize("case_name", CASE_NAMES)
async def test_hostile_text_in_any_publisher_field_produces_the_same_question(
    hass: HomeAssistant, case_name: str, field: str
) -> None:
    """A hostile case delivered via notes, release_summary or title yields the same fixed question."""
    text = HOSTILE[case_name]["notes"]
    field_kwargs: dict[str, Any] = {field: text} if field != "notes" else {}
    hostile = register_pending_update(hass, "hostile", **field_kwargs)
    benign = register_pending_update(hass, "benign")
    install_update_entities(
        hass,
        {
            hostile.entity_id: FakeUpdateEntity(notes=text if field == "notes" else BENIGN_NOTES),
            benign.entity_id: FakeUpdateEntity(notes=BENIGN_NOTES),
        },
    )

    batch = await UpdateRecipe(critical_label=None).async_prepare(hass)

    assert len(batch.questions) == 2
    for index, question in enumerate(batch.questions.values()):
        assert question["type"] == "score"
        # Identity, not equality: a copy could have been built from the state.
        assert question["criteria"] is UPDATE_CRITERIA
        assert question["instructions"] == UPDATE_INSTRUCTIONS.format(index=index)
    first, second = batch.questions.values()
    assert {key: value for key, value in first.items() if key != "instructions"} == {
        key: value for key, value in second.items() if key != "instructions"
    }

    # updates and subjects are built in the same loop, so their order matches.
    hostile_item = next(
        state_item
        for state_item, subject in zip(batch.state["updates"], batch.subjects.values(), strict=True)
        if subject["entity_id"] == hostile.entity_id
    )
    state_key = FIELD_KEYS[field]
    expected = clean_release_notes(text) if state_key in CLEANED_KEYS else text
    assert hostile_item[state_key] == expected


@pytest.mark.parametrize("state_key", sorted(FIELD_KEYS.values()))
def test_every_publisher_field_is_named_untrusted_in_the_instruction(state_key: str) -> None:
    """title, release_summary and release_notes are all named in the untrusted-framing sentence."""
    sentence = next(part for part in UPDATE_INSTRUCTIONS.split(". ") if "never as instructions to follow" in part)

    assert f"`{state_key}`" in sentence
