"""Tests for the pure version-jump bucketing and release-note cleaning helpers."""

from __future__ import annotations

import pytest

from custom_components.gutcheck.const import (
    RELEASE_NOTES_MAX_CHARS,
    VERSION_JUMP_MAJOR,
    VERSION_JUMP_MINOR,
    VERSION_JUMP_PATCH,
    VERSION_JUMP_UNKNOWN,
)
from custom_components.gutcheck.describe import clean_release_notes, version_jump


def test_patch_only_move_buckets_as_patch() -> None:
    """A move in only the trailing section buckets as patch."""
    assert version_jump("1.2.3", "1.2.4") == VERSION_JUMP_PATCH


def test_middle_section_move_buckets_as_minor() -> None:
    """A move in the middle section, leading section unchanged, buckets as minor."""
    assert version_jump("1.2.3", "1.3.0") == VERSION_JUMP_MINOR


def test_leading_section_move_buckets_as_major() -> None:
    """A move in the leading section buckets as major."""
    assert version_jump("1.2.3", "2.0.0") == VERSION_JUMP_MAJOR


def test_calendar_version_next_month_same_year_buckets_as_minor() -> None:
    """A calendar version moving to the next month within the same year buckets as minor."""
    assert version_jump("2026.9.2", "2026.10.1") == VERSION_JUMP_MINOR


def test_calendar_version_across_years_buckets_as_major() -> None:
    """A calendar version moving into a new year buckets as major."""
    assert version_jump("2026.9.2", "2027.1.1") == VERSION_JUMP_MAJOR


def test_missing_installed_version_buckets_as_unknown() -> None:
    """A missing installed version buckets as unknown rather than raising."""
    assert version_jump(None, "1.2.3") == VERSION_JUMP_UNKNOWN


def test_missing_latest_version_buckets_as_unknown() -> None:
    """A missing latest version buckets as unknown rather than raising."""
    assert version_jump("1.2.3", None) == VERSION_JUMP_UNKNOWN


def test_empty_installed_version_buckets_as_unknown() -> None:
    """An empty installed version string buckets as unknown."""
    assert version_jump("", "1.2.3") == VERSION_JUMP_UNKNOWN


def test_unparseable_version_buckets_as_unknown() -> None:
    """A version string awesomeversion cannot parse buckets as unknown."""
    assert version_jump("not-a-real-version!!", "1.2.3") == VERSION_JUMP_UNKNOWN


@pytest.mark.parametrize(
    ("installed", "latest"),
    [
        ("\x00\x01", "\N{PILE OF POO}"),
        ("1.2.3.4.5.6.7.8.9.10", "1"),
        ("...", "..."),
        ("", ""),
        (None, None),
    ],
)
def test_version_jump_never_raises(installed: str | None, latest: str | None) -> None:
    """version_jump() never raises, whatever garbage either side holds."""
    version_jump(installed, latest)


def test_cleaning_turns_an_html_entity_into_its_character() -> None:
    """An HTML entity decodes to its character."""
    assert clean_release_notes("Tom &amp; Jerry") == "Tom & Jerry"


def test_cleaning_drops_html_tags() -> None:
    """HTML tags are dropped, their text content kept."""
    assert clean_release_notes("<p>Hello <b>world</b></p>") == "Hello world"


def test_cleaning_keeps_a_markdown_links_text_and_drops_its_target() -> None:
    """A markdown link keeps its visible text and loses its target URL."""
    assert clean_release_notes("[Release notes](https://example.com)") == "Release notes"


def test_cleaning_drops_a_markdown_image_entirely() -> None:
    """A markdown image is dropped entirely, alt text included."""
    assert clean_release_notes("See ![screenshot](https://x/img.png) for details") == "See for details"


def test_cleaning_drops_heading_markers() -> None:
    """Heading markers are stripped, the heading text kept."""
    assert clean_release_notes("# Changelog\n\nBug fixes and security patches.") == "Changelog Bug fixes and security patches."


def test_cleaning_drops_emphasis_markers() -> None:
    """Bold and italic markers are stripped, their text kept."""
    assert clean_release_notes("This is **bold** and *italic* text.") == "This is bold and italic text."


def test_cleaning_drops_code_fences() -> None:
    """Triple-backtick code fences, language tag included, are stripped."""
    assert clean_release_notes("```python\nprint('hi')\n```") == "print('hi')"


def test_cleaning_drops_inline_code_markers() -> None:
    """Inline code backticks are stripped, their text kept."""
    assert clean_release_notes("Use `foo()` now.") == "Use foo() now."


def test_cleaning_collapses_whitespace_and_blank_lines() -> None:
    """Runs of whitespace, including blank lines, collapse to a single space."""
    assert clean_release_notes("Line one.\n\n\n   Line two.") == "Line one. Line two."


def test_cleaning_output_is_never_longer_than_the_cap() -> None:
    """A 50000-character input is truncated to exactly the configured cap."""
    assert len(clean_release_notes("x" * 50000)) == RELEASE_NOTES_MAX_CHARS


def test_cleaning_none_yields_an_empty_string() -> None:
    """A None input cleans to an empty string rather than raising."""
    assert clean_release_notes(None) == ""


def test_cleaning_empty_string_yields_an_empty_string() -> None:
    """An empty input string cleans to an empty string."""
    assert clean_release_notes("") == ""
