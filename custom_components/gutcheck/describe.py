"""Pure duration and version-to-words helpers. No Home Assistant imports, no raw numbers out."""

import html
import re

from awesomeversion import AwesomeVersion

from .const import (
    RELEASE_NOTES_MAX_CHARS,
    VERSION_JUMP_MAJOR,
    VERSION_JUMP_MINOR,
    VERSION_JUMP_PATCH,
    VERSION_JUMP_UNKNOWN,
)

_DAY = 86400
_WEEK = 7 * _DAY
_FOUR_WEEKS = 28 * _DAY


def bucket_duration(seconds: float) -> str:
    """Bucket a duration in seconds into a named range."""
    if seconds < _DAY:
        return "less than a day"
    if seconds < _WEEK:
        return "1 to 6 days"
    if seconds <= _FOUR_WEEKS:
        return "1 to 4 weeks"
    return "more than 4 weeks"


def bucket_longer_than(days: int) -> str:
    """Describe a lower bound in whole days, for when the real duration is unknown."""
    if days <= 0:
        return "unknown"
    if days == 1:
        return "longer than 1 day"
    return f"longer than {days} days"


def version_jump(installed: str | None, latest: str | None) -> str:
    """Bucket the size of a version move: patch, minor, major, or unknown when it cannot be told.

    Compares only whole sections: the leading section differing is major, the
    next section differing (leading unchanged) is minor, and anything
    narrower is patch. A calendar version (year.month.day) buckets the same
    way for free: a new year is major, a new month within the same year is
    minor. This is the code-side answer to the documented model weakness at
    number and date comparison, so the model is never asked to compare
    versions itself.
    """
    if not installed or not latest:
        return VERSION_JUMP_UNKNOWN
    installed_version = AwesomeVersion(installed)
    latest_version = AwesomeVersion(latest)
    if not installed_version.valid or not latest_version.valid:
        return VERSION_JUMP_UNKNOWN
    if installed_version.section(0) != latest_version.section(0):
        return VERSION_JUMP_MAJOR
    if installed_version.section(1) != latest_version.section(1):
        return VERSION_JUMP_MINOR
    return VERSION_JUMP_PATCH


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_HEADING_RE = re.compile(r"(?m)^#{1,6}\s*")
# Only a run acting as a delimiter: foo_bar and 2 * 3 keep theirs.
_EMPHASIS_RE = re.compile(r"(?<!\w)(\*{1,3}|_{1,3})(?=\S)|(?<=\S)(\*{1,3}|_{1,3})(?!\w)")
_CODE_FENCE_RE = re.compile(r"```[a-zA-Z0-9]*\n?")
_INLINE_CODE_RE = re.compile(r"`([^`]*)`")
_WHITESPACE_RE = re.compile(r"\s+")


def clean_release_notes(text: str | None) -> str:
    """Clean a release note excerpt to plain text, capped at RELEASE_NOTES_MAX_CHARS.

    Fixed order, so the result is deterministic: unescape HTML entities,
    turn HTML tags into spaces, drop markdown images, keep a markdown link's text and
    drop its target, strip heading/emphasis/code-fence/inline-code markers,
    collapse whitespace, then truncate. This is for token economy and model
    clarity, not sanitizing: the excerpt only ever lands in a JSON request
    body and the last-payload attribute, is never rendered as HTML, and is
    never used as a Repairs placeholder.
    """
    if not text:
        return ""
    cleaned = html.unescape(text)
    cleaned = _HTML_TAG_RE.sub(" ", cleaned)
    cleaned = _MARKDOWN_IMAGE_RE.sub("", cleaned)
    cleaned = _MARKDOWN_LINK_RE.sub(r"\1", cleaned)
    cleaned = _HEADING_RE.sub("", cleaned)
    cleaned = _EMPHASIS_RE.sub("", cleaned)
    cleaned = _CODE_FENCE_RE.sub("", cleaned)
    cleaned = _INLINE_CODE_RE.sub(r"\1", cleaned)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    return cleaned[:RELEASE_NOTES_MAX_CHARS]
