"""Guard the release-please version coupling across const.py, manifest.json and Sonar.

release-please's generic updater finds a version by an `x-release-please` marker on the
same line, or inside a start/end block, and does nothing when the marker is gone.
manifest.json is bumped separately by jsonpath, so losing a marker or an extra-files
entry lets a release succeed while the files drift apart.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
CONST_PATH = REPO_ROOT / "custom_components" / "gutcheck" / "const.py"
MANIFEST_PATH = REPO_ROOT / "custom_components" / "gutcheck" / "manifest.json"
SONAR_PATH = REPO_ROOT / "sonar-project.properties"

VERSION_LINE = re.compile(r'^VERSION: Final = "([^"]+)"  # x-release-please-version$', re.MULTILINE)
# A properties value runs to end of line, so the markers have to bracket the line
# rather than trail it. Anything between them is what release-please rewrites.
SONAR_VERSION_BLOCK = re.compile(
    r"^# x-release-please-start-version\nsonar\.projectVersion=(\S+)\n# x-release-please-end$",
    re.MULTILINE,
)


def _const_version() -> str:
    """The version string on const.py's annotated VERSION line."""
    match = VERSION_LINE.search(CONST_PATH.read_text(encoding="utf-8"))
    assert match, "const.py VERSION line not found; see the annotation test"
    return match.group(1)


def test_const_version_line_carries_the_release_please_annotation() -> None:
    """Without the trailing comment, release-please silently stops bumping const.py."""
    text = CONST_PATH.read_text(encoding="utf-8")
    assert VERSION_LINE.search(text), (
        "const.py's VERSION line lost the ' # x-release-please-version' annotation "
        "release-please uses to find it; version bumps will stop reaching this file"
    )


def test_manifest_version_matches_const_version() -> None:
    """A dropped extra-files entry would leave const.py behind while manifest.json bumps on."""
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["version"] == _const_version()


def test_sonar_version_sits_inside_a_release_please_block() -> None:
    """A trailing marker would be read as part of the value, so the block form is the only safe one."""
    text = SONAR_PATH.read_text(encoding="utf-8")
    assert SONAR_VERSION_BLOCK.search(text), (
        "sonar.projectVersion is not bracketed by x-release-please-start-version and "
        "x-release-please-end; releases will stop bumping it, or bake the marker into the value"
    )


def test_sonar_version_matches_const_version() -> None:
    """All three files carry one version, so a half-applied release bump fails here."""
    match = SONAR_VERSION_BLOCK.search(SONAR_PATH.read_text(encoding="utf-8"))
    assert match, "sonar.projectVersion block not found; see the block test"
    assert match.group(1) == _const_version()
