"""Carry-forward and ordering helpers for updates, with the shapes a real result can have."""

from __future__ import annotations

from custom_components.gutcheck.const import (
    OPTION_POSSIBLY_BREAKING,
    UPDATE_OPTIONS,
    VERSION_JUMP_MAJOR,
    VERSION_JUMP_PATCH,
    VERSION_JUMP_UNKNOWN,
)
from custom_components.gutcheck.recipes.update_cadence import carry_prior, carry_unasked, most_significant_first

from .conftest import update_result

SUBJECT = {"registry_id": "r2", "installed_version": "1.0.0", "latest_version": "2.0.0", "skipped_version": None}


def _prior(registry_id: str) -> dict[str, object]:
    """A stored accepted item for the given update, matching SUBJECT's versions."""
    return {**SUBJECT, "registry_id": registry_id, "confidence": 0.9, "score": 2.0}


def test_a_prior_in_a_later_bucket_after_a_different_update_is_found() -> None:
    """Earlier options with no items and another update ahead in the same bucket do not hide the match."""
    previous = update_result({OPTION_POSSIBLY_BREAKING: [_prior("r1"), _prior("r2")]})

    carried = carry_prior(previous, UPDATE_OPTIONS, SUBJECT)

    assert carried is not None
    assert carried[0] == OPTION_POSSIBLY_BREAKING
    assert carried[1]["registry_id"] == "r2"


def test_unasked_updates_carry_when_some_buckets_are_absent_from_the_result() -> None:
    """A stored result with only one bucket still yields its unasked items."""
    previous = update_result({OPTION_POSSIBLY_BREAKING: [_prior("r1")]})

    assert carry_unasked(previous, UPDATE_OPTIONS, {"r1"}) == [(OPTION_POSSIBLY_BREAKING, _prior("r1"))]


def test_a_version_jump_outside_the_known_buckets_ranks_below_every_known_one() -> None:
    """Patch, then unknown, then a jump with no recognised label: the order the cap defers from the end."""
    patch = ({"version_jump": VERSION_JUMP_PATCH}, {"registry_id": "patch"})
    unknown = ({"version_jump": VERSION_JUMP_UNKNOWN}, {"registry_id": "unknown"})
    unlabelled = ({}, {"registry_id": "unlabelled"})
    major = ({"version_jump": VERSION_JUMP_MAJOR}, {"registry_id": "major"})

    asked, deferred = most_significant_first([unlabelled, patch, unknown, major], 2)

    assert [subject["registry_id"] for _, subject in asked] == ["major", "patch"]
    assert [subject["registry_id"] for _, subject in deferred] == ["unknown", "unlabelled"]
