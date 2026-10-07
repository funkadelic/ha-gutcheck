"""Release notes are ordered by risk before the length cap cuts them."""

import pytest

from custom_components.gutcheck.const import RELEASE_NOTES_MAX_CHARS
from custom_components.gutcheck.describe import clean_release_notes, clean_text

from .conftest import load_fixture


def test_spook_change_list_survives_the_cap() -> None:
    """A captured real body opens with long prose, so the plain cap loses the change list."""
    body = load_fixture("captured", "spook_release_notes.json")["body"]
    assert "Offer to clear the statistics" not in clean_text(body, RELEASE_NOTES_MAX_CHARS)
    cleaned = clean_release_notes(body)
    assert "Offer to clear the statistics" in cleaned
    assert cleaned.startswith("Your ignored repairs come back once")
    assert len(cleaned) <= RELEASE_NOTES_MAX_CHARS


@pytest.mark.parametrize(
    "heading",
    ["Breaking changes", "⚠️ BREAKING", "Breaks older configs", "Deprecations", "Removed", "Migration guide"],
)
def test_late_risk_heading_comes_first(heading: str) -> None:
    """A risk heading late in the notes leads, then other sections, then the intro."""
    text = f"Intro prose.\n\n## New features\n\n- Added a thing\n\n## {heading}\n\n- Dropped the old option\n"
    assert clean_release_notes(text) == f"{heading} - Dropped the old option New features - Added a thing Intro prose."


def test_notes_without_headings_are_unchanged() -> None:
    """Notes with no column-0 heading clean exactly as before."""
    text = "Fixed issue #12 in the parser.\n#1234 tracked it.\n  # indented, not a heading\n\nMore prose."
    assert clean_release_notes(text) == clean_text(text, RELEASE_NOTES_MAX_CHARS)


@pytest.mark.parametrize(
    ("fixture", "kept"),
    [
        ("hacs_plugin_release_notes.json", "Match the full forecast period for the current temperature"),
        ("hacs_integration_release_notes.json", "Bump minimum HA version to 2026.6"),
    ],
)
def test_the_hacs_footer_is_dropped_and_the_notes_kept(fixture: str, kept: str) -> None:
    """HACS's restart or clear-the-cache reminder never reaches the model; the change list does."""
    cleaned = clean_release_notes(load_fixture("captured", fixture)["release_notes"])
    assert "You need to" not in cleaned
    assert not cleaned.endswith("---")
    assert kept in cleaned
