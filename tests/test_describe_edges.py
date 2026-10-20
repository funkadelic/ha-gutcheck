"""Release-note ordering looks at a section's heading line only."""

from custom_components.gutcheck.const import DEVICE_TEXT_MAX_CHARS
from custom_components.gutcheck.describe import clean_name, clean_release_notes


def test_a_risk_word_in_a_section_body_does_not_promote_it() -> None:
    """Only the heading line counts, so a later section that merely mentions removal stays in place."""
    fixes = "## Fixes\n\n- Fixed a crash\n- Fixed a typo\n\n"
    features = "## New features\n\n- Removed a stray log line\n- Added a sensor\n"
    text = f"Intro.\n\n{fixes}{features}"
    assert clean_release_notes(text) == (
        "Fixes - Fixed a crash - Fixed a typo New features - Removed a stray log line - Added a sensor Intro."
    )


def test_a_name_has_its_email_removed_before_the_cap_can_cut_it_in_half() -> None:
    """An email straddling the cap is replaced whole, so no fragment of the address survives."""
    name = "x" * (DEVICE_TEXT_MAX_CHARS - 15) + " someone@example.com"

    cleaned = clean_name(name)

    assert cleaned is not None
    assert "someone" not in cleaned
    assert len(cleaned) <= DEVICE_TEXT_MAX_CHARS


def test_an_empty_name_is_none() -> None:
    """No name and an all-whitespace name both read as missing."""
    assert clean_name(None) is None
    assert clean_name("   ") is None
