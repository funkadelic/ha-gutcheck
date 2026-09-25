"""Carry-forward decision for one described update: ask again, or reuse a prior classification."""

from __future__ import annotations

from ..const import (
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


def carry_prior(previous: RecipeResult | None, options: tuple[str, ...], subject: Item) -> tuple[str, Item] | None:
    """The prior bucket this update carries into with no question, or None to ask about it.

    A prior accepted item whose installed, latest and skipped version all
    match carries forward with the prior confidence and score. Anything
    else, including no prior item or one that landed in unsure, means
    asking again. Needs no release notes, so it runs before the fetch.
    """
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


def decided_in_code(state_item: Item) -> bool:
    """Whether an empty-notes, major-version-jump update goes straight to possibly breaking.

    Both inputs are already known, so no model answer stands behind it and
    it carries with no confidence or score.
    """
    return not state_item["release_notes"] and state_item["version_jump"] == VERSION_JUMP_MAJOR


def most_significant_first(pairs: list[tuple[Item, Item]], cap: int) -> tuple[list[tuple[Item, Item]], list[tuple[Item, Item]]]:
    """The cap most significant version-jump pairs to ask about this run, and the ones deferred past it.

    A per-run cap keeps the request under the state token limit on a large
    install. Only the question is deferred: the caller carries a deferred
    update's prior classification forward, so it keeps its place in the
    result and its Repairs card until a later run asks about it.
    """
    ordered = sorted(pairs, key=lambda pair: _JUMP_RANK.get(str(pair[0].get("version_jump")), -1), reverse=True)
    return ordered[:cap], ordered[cap:]


def carry_unasked(previous: RecipeResult | None, options: tuple[str, ...], registry_ids: set[str]) -> list[tuple[str, Item]]:
    """The prior bucket and item for every update not asked about this run, unchanged.

    An update nobody asked about this run still exists, and what the last
    run said about it is the best information there is. The stored item
    carries over exactly as it stands, so the classification and the
    version it was about stay together. An update with no prior
    classification has nothing to carry and simply waits for a run that
    asks about it.
    """
    if previous is None:
        return []
    return [
        (option, dict(item))
        for option in options
        for item in previous["items"].get(option, [])
        if item.get("registry_id") in registry_ids
    ]
