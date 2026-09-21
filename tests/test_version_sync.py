"""Guard the release-please version coupling between const.py and manifest.json.

release-please's generic updater locates VERSION in const.py by the trailing
`# x-release-please-version` comment, and does nothing if that comment is
missing. manifest.json is bumped separately by jsonpath. Losing either the
comment or the extra-files entry lets a release succeed while the two files
drift apart.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
CONST_PATH = REPO_ROOT / "custom_components" / "gutcheck" / "const.py"
MANIFEST_PATH = REPO_ROOT / "custom_components" / "gutcheck" / "manifest.json"

VERSION_LINE = re.compile(r'^VERSION: Final = "([^"]+)"  # x-release-please-version$', re.MULTILINE)


def test_const_version_line_carries_the_release_please_annotation() -> None:
    """Without the trailing comment, release-please silently stops bumping const.py."""
    text = CONST_PATH.read_text(encoding="utf-8")
    assert VERSION_LINE.search(text), (
        "const.py's VERSION line lost the ' # x-release-please-version' annotation "
        "release-please uses to find it; version bumps will stop reaching this file"
    )


def test_manifest_version_matches_const_version() -> None:
    """A dropped extra-files entry would leave const.py behind while manifest.json bumps on."""
    match = VERSION_LINE.search(CONST_PATH.read_text(encoding="utf-8"))
    assert match, "const.py VERSION line not found; see the annotation test"
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["version"] == match.group(1)
