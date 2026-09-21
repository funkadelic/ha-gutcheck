"""Carry-forward decision for one described update: ask again, or reuse a prior classification."""

from __future__ import annotations

from ..const import OPTION_POSSIBLY_BREAKING, VERSION_JUMP_MAJOR
from .shapes import Item, RecipeResult


def carry_bucket(
    previous: RecipeResult | None,
    options: tuple[str, ...],
    state_item: Item,
    subject: Item,
) -> tuple[str, Item] | None:
    """The bucket this update carries into with no question, or None to ask about it.

    An empty-notes, major-version-jump update is decided in code: both
    inputs are already known, so it carries into possibly breaking with no
    confidence or score, showing plainly that no model answer stands
    behind it. Otherwise, a prior accepted item whose installed, latest and
    skipped version all match carries forward with the prior confidence
    and score. Anything else, including no prior item or one that landed
    in unsure, means asking again.
    """
    if not state_item["release_notes"] and state_item["version_jump"] == VERSION_JUMP_MAJOR:
        return OPTION_POSSIBLY_BREAKING, dict(subject)
    if previous is None:
        return None
    registry_id = subject["registry_id"]
    for option in options:
        for prior in previous["items"].get(option, []):
            if prior.get("registry_id") != registry_id:
                continue
            if (
                prior.get("installed_version") == subject.get("installed_version")
                and prior.get("latest_version") == subject.get("latest_version")
                and prior.get("skipped_version") == subject.get("skipped_version")
            ):
                carried_item = dict(subject)
                carried_item["confidence"] = prior.get("confidence")
                carried_item["score"] = prior.get("score")
                return option, carried_item
            return None
    return None
