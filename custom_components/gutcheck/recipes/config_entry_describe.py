"""Target-state selection, first-seen carry, failing-time bucketing, and the model-visible shape."""

from __future__ import annotations

from datetime import datetime

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from ..const import CONFIG_ENTRY_REASON_MAX_CHARS, DOMAIN
from ..describe import bucket_longer_than, clean_text
from .shapes import Item, RecipeResult

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
    if previous is not None:
        for bucket in (*previous["items"].values(), previous["unsure"]):
            for prior in bucket:
                if prior.get("entry_id") != entry_id:
                    continue
                seen = prior.get("first_seen")
                if isinstance(seen, str):
                    parsed = dt_util.parse_datetime(seen)
                    if parsed is not None:
                        return parsed
                return now
    return now


def failing_for(seen: datetime, now: datetime) -> str:
    """Bucket the whole days since first_seen into a fixed word, never a raw number."""
    return bucket_longer_than(int((now - seen).total_seconds() // 86400))


def describe(entry: ConfigEntry, failing_for_words: str) -> Item:
    """One entry's model-visible state: never the title, entry id, data, options, source or unique id."""
    reason = clean_text(entry.reason, CONFIG_ENTRY_REASON_MAX_CHARS) if entry.reason else ""
    return {
        "integration": entry.domain,
        "config_entry_state": entry.state.value.replace("_", " "),
        "reason": reason or None,
        "failing_for": failing_for_words,
    }


def describe_subject(entry: ConfigEntry, seen: datetime, failing_for_words: str) -> Item:
    """One entry's code-only subject fields, which never reach the model."""
    return {
        "entry_id": entry.entry_id,
        "integration": entry.domain,
        "title": entry.title,
        "first_seen": seen.isoformat(),
        "failing_for": failing_for_words,
    }


def resolved(hass: HomeAssistant, entry_id: str) -> bool:
    """Whether this entry has recovered: gone, disabled, or loaded.

    Every other state, including setup_in_progress during a retry and
    not_loaded before startup, says nothing about recovery.
    """
    entry = hass.config_entries.async_get_entry(entry_id)
    return entry is None or entry.disabled_by is not None or entry.state is ConfigEntryState.LOADED
