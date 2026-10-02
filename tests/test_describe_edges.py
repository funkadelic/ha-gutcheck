"""Release-note ordering looks at a section's heading line only."""

from custom_components.gutcheck.describe import clean_release_notes


def test_a_risk_word_in_a_section_body_does_not_promote_it() -> None:
    """Only the heading line counts, so a later section that merely mentions removal stays in place."""
    fixes = "## Fixes\n\n- Fixed a crash\n- Fixed a typo\n\n"
    features = "## New features\n\n- Removed a stray log line\n- Added a sensor\n"
    text = f"Intro.\n\n{fixes}{features}"
    assert clean_release_notes(text) == (
        "Fixes - Fixed a crash - Fixed a typo New features - Removed a stray log line - Added a sensor Intro."
    )
