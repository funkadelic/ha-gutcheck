"""Guard the Bronze quality scale claim against drift.

hassfest never opens quality_scale.yaml for a custom integration
(validate_iqs_file() in script/hassfest/quality_scale.py returns immediately
when not integration.core), so this test is the only check on the verdicts.

The 20-rule Bronze set below is hardcoded from home-assistant/core's own
ALL_RULES at the time this file was written. A future HA release that changes
the Bronze tier needs a manual bump here; this test's own failure message is
what would surface that drift.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent
QS_PATH = REPO_ROOT / "custom_components" / "gutcheck" / "quality_scale.yaml"
MANIFEST_PATH = REPO_ROOT / "custom_components" / "gutcheck" / "manifest.json"
README_PATH = REPO_ROOT / "README.md"

BRONZE_RULES = frozenset(
    {
        "action-setup",
        "appropriate-polling",
        "brands",
        "common-modules",
        "config-flow",
        "config-flow-test-coverage",
        "dependency-transparency",
        "docs-actions",
        "docs-conditions",
        "docs-high-level-description",
        "docs-installation-instructions",
        "docs-removal-instructions",
        "docs-triggers",
        "entity-event-setup",
        "entity-unique-id",
        "has-entity-name",
        "runtime-data",
        "test-before-configure",
        "test-before-setup",
        "unique-config-entry",
    }
)


def _rules() -> dict[str, object]:
    """The parsed rules mapping from the committed quality_scale.yaml."""
    return yaml.safe_load(QS_PATH.read_text(encoding="utf-8"))["rules"]  # type: ignore[no-any-return]


def test_the_rule_key_set_matches_the_bronze_tier_exactly() -> None:
    """The file lists exactly the Bronze rules, no more and no fewer."""
    rules = _rules()
    assert set(rules) == BRONZE_RULES, f"quality_scale.yaml rule keys drifted from the Bronze tier: {set(rules) ^ BRONZE_RULES}"


def test_every_rule_is_done_or_exempt_with_a_reason_when_exempt() -> None:
    """Every rule is done or exempt; a todo or missing status fails."""
    for name, entry in _rules().items():
        status = entry if isinstance(entry, str) else entry.get("status")
        assert status in ("done", "exempt"), f"{name}: status must be 'done' or 'exempt', got {status!r}"
        if status == "exempt":
            comment = entry.get("comment") if isinstance(entry, dict) else None
            assert comment, f"{name}: an exempt rule needs a non-empty comment"


def test_manifest_claims_the_same_tier_the_yaml_documents() -> None:
    """The manifest claims Bronze, and hassfest validates the manifest."""
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest.get("quality_scale") == "bronze"


def test_the_readme_still_carries_the_removal_heading_docs_removal_instructions_relies_on() -> None:
    """The README keeps the Remove heading that docs-removal-instructions points at."""
    lines = README_PATH.read_text(encoding="utf-8").splitlines()
    assert "## Remove" in lines
