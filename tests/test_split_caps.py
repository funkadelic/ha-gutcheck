"""Per-request caps in split_batch: the smallest real cap, and no cap at all."""

from __future__ import annotations

import pytest

from custom_components.gutcheck.sizing import estimate_tokens, request_fits
from custom_components.gutcheck.split import _request, split_batch

from .test_split import LIMIT, _captured_batch


def test_a_cap_of_one_sends_every_subject_alone() -> None:
    """A cap of 1 still counts as a cap, so a run that fits one request is cut to single-subject requests."""
    batch, payload, _response = _captured_batch()
    assert request_fits(payload)
    batch.max_per_request = 1

    payloads = split_batch(batch)

    assert [len(request["questions"]) for request in payloads] == [1] * len(payload["questions"])


def test_an_uncapped_oversized_run_packs_as_many_subjects_as_fit(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no cap, only the size limit decides where a request ends, so slices hold several subjects."""
    batch, payload, _response = _captured_batch()
    batch.max_per_request = 0
    monkeypatch.setattr(LIMIT, estimate_tokens(payload) // 2)

    payloads = split_batch(batch)

    assert 2 <= len(payloads) < len(payload["questions"])
    assert all(request_fits(request) for request in payloads)
    assert max(len(request["questions"]) for request in payloads) > 1
    ids, start = list(batch.questions), 0
    for request in payloads[:-1]:
        end = start + len(request["questions"])
        assert not request_fits(_request(batch, ids, start, end + 1)), "a request stopped before it was full"
        start = end
