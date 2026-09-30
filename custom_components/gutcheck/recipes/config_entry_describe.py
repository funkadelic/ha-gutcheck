"""Target-state selection, first-seen carry, failing-time bucketing, and the model-visible shape."""

from __future__ import annotations

import re
from datetime import datetime

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from ..const import DOMAIN
from ..describe import bucket_longer_than, clean_text
from .config_entry_const import CONFIG_ENTRY_REASON_MAX_CHARS, CONFIG_ENTRY_UNSURE_CARD_AFTER, REDACTED, REDACTED_EMAIL
from .shapes import Item, RecipeResult

# Linear on any input: each match starts at "://" or at the start of a run, and possessive quantifiers never backtrack.
_URL_USERINFO_RE = re.compile(r"://[^\s/@]++@")
_URL_QUERY_RE = re.compile(r"(://[^\s?#]++)[?#]\S*+")
_EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]++@[\w-]++\.[\w.-]++")

TARGET_STATES = frozenset({ConfigEntryState.SETUP_RETRY, ConfigEntryState.SETUP_ERROR})
# An ignored or user-disabled entry is never set up, so it stays not loaded and never matches.


def select(hass: HomeAssistant) -> list[ConfigEntry]:
    """Every other integration's config entry currently stuck in setup_retry or setup_error."""
    return sorted(
        (entry for entry in hass.config_entries.async_entries() if entry.domain != DOMAIN and entry.state in TARGET_STATES),
        key=lambda entry: entry.entry_id,
    )


def reauth_active(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Whether Home Assistant's own reauth flow is already running for this entry.

    HA only creates its own reauth issue for an active reauth flow, and
    deletes it the moment the flow ends, so this one check alone covers
    both "HA's reauth issue is shown" and "HA's reauth flow is running".
    """
    return any(entry.async_get_active_flows(hass, {SOURCE_REAUTH}))


def first_seen(previous: RecipeResult | None, entry_id: str, now: datetime) -> datetime:
    """The stored first_seen for this entry_id from any bucket of the prior run, or now.

    Checked across every option bucket plus unsure: an entry that was
    previously unsure still keeps the same first_seen, since being unsure
    is no information about how long the underlying failure has lasted. A
    missing, non-string or unparseable first_seen reads as first seen now.

    Ponytail: first-seen survives only through the stored result, so a gap
    longer than the restore window resets it. A dedicated Store if that
    ever matters.
    """
    if previous is None:
        return now
    buckets = (*previous["items"].values(), previous["unsure"])
    prior = next((item for bucket in buckets for item in bucket if item.get("entry_id") == entry_id), None)
    return (_parsed_first_seen(prior) if prior is not None else None) or now


def _parsed_first_seen(item: Item) -> datetime | None:
    """The item's first_seen as a datetime, or None when missing, non-string or unparseable."""
    seen = item.get("first_seen")
    return dt_util.parse_datetime(seen) if isinstance(seen, str) else None


def failing_for(seen: datetime, now: datetime) -> str:
    """Bucket the whole days since first_seen into a fixed word, never a raw number."""
    return bucket_longer_than(int((now - seen).total_seconds() // 86400))


def redact_reason(reason: str) -> str:
    """Drop a URL's sign-in part, query and fragment, and every email address, before anything else sees it."""
    reason = _URL_USERINFO_RE.sub(f"://{REDACTED}@", reason)
    reason = _URL_QUERY_RE.sub(rf"\1?{REDACTED}", reason)
    return _EMAIL_RE.sub(REDACTED_EMAIL, reason)


def reason_text(entry: ConfigEntry) -> str | None:
    """The redacted, cleaned reason exactly as the model sees it, or None when there is none."""
    return (clean_text(redact_reason(entry.reason), CONFIG_ENTRY_REASON_MAX_CHARS) if entry.reason else "") or None


def describe(entry: ConfigEntry, failing_for_words: str) -> Item:
    """One entry's model-visible state: never the title, entry id, data, options, source or unique id."""
    return {
        "integration": entry.domain,
        "config_entry_state": entry.state.value.replace("_", " "),
        "reason": reason_text(entry),
        "failing_for": failing_for_words,
    }


def describe_subject(entry: ConfigEntry, seen: datetime, failing_for_words: str) -> Item:
    """One entry's code-only subject fields, which never reach the model."""
    return {
        "entry_id": entry.entry_id,
        "integration": entry.domain,
        "title": entry.title,
        "reason": reason_text(entry),
        "first_seen": seen.isoformat(),
        "failing_for": failing_for_words,
    }


def long_unsure(result: RecipeResult) -> list[Item]:
    """The unsure items first seen at least CONFIG_ENTRY_UNSURE_CARD_AFTER before the run."""
    cutoff = datetime.fromisoformat(result["last_run"]) - CONFIG_ENTRY_UNSURE_CARD_AFTER
    # Items stored before "reason" existed wait for the next run rather than showing a false "none".
    return [
        item
        for item in result["unsure"]
        if "reason" in item and (seen := _parsed_first_seen(item)) is not None and seen <= cutoff
    ]


def resolved(hass: HomeAssistant, entry_id: str) -> bool:
    """Whether this entry has recovered: gone, disabled, or loaded.

    Every other state, including setup_in_progress during a retry and
    not_loaded before startup or after a plain unload, says nothing about
    recovery; the next run drops an entry that is no longer stuck.
    """
    entry = hass.config_entries.async_get_entry(entry_id)
    return entry is None or entry.disabled_by is not None or entry.state is ConfigEntryState.LOADED
