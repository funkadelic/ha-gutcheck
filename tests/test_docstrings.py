"""Every module, class and function carries a docstring, private helpers included.

Ruff's pydocstyle rules only cover public names, so they cannot express this.
Nothing here checks docstring quality, only that one is present.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
CHECKED_TREES = ("custom_components", "tests")


def _undocumented(path: Path) -> list[str]:
    """Every module, class and function in one file that has no docstring."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    relative = path.relative_to(REPO_ROOT)
    missing = [] if ast.get_docstring(tree) is not None else [f"{relative}:1 module"]
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) and ast.get_docstring(node) is None:
            missing.append(f"{relative}:{node.lineno} {node.name}")
    return missing


def test_every_definition_has_a_docstring() -> None:
    """A missing docstring fails here rather than at review time."""
    missing = [
        entry for tree in CHECKED_TREES for path in sorted((REPO_ROOT / tree).rglob("*.py")) for entry in _undocumented(path)
    ]

    assert not missing, "undocumented definitions:\n" + "\n".join(missing)
