"""Carry-forward decision for one described update: ask again, or reuse a prior classification."""

from __future__ import annotations

from ..const import (
    OPTION_POSSIBLY_BREAKING,
    VERSION_JUMP_MAJOR,
    VERSION_JUMP_MINOR,
    VERSION_JUMP_PATCH,
    VERSION_JUMP_UNKNOWN,
)
from .shapes import Item, RecipeResult

_JUMP_RANK: dict[str, int] = {
    VERSION_JUMP_MAJOR: 3,
    VERSION_JUMP_MINOR: 2,
    VERSION_JUMP_PATCH: 1,
    VERSION_JUMP_UNKNOWN: 0,
}


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


def most_significant_first(pairs: list[tuple[Item, Item]], cap: int) -> list[tuple[Item, Item]]:
    """The cap most significant version-jump pairs to ask about this run, most significant first.

    A per-run cap keeps the request under the state token limit on a large
    install; anything past the cap is simply not asked this run and is
    picked up on the next one, since an unasked update is not lost.
    """
    ordered = sorted(pairs, key=lambda pair: _JUMP_RANK.get(str(pair[0].get("version_jump")), -1), reverse=True)
    return ordered[:cap]
