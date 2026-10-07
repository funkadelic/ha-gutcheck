"""Three-kind advisory card sync for stuck config entries, and the live recovery listener."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.config_entries import SIGNAL_CONFIG_ENTRY_CHANGED, ConfigEntry, ConfigEntryChange
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from ..const import (
    CONFIG_ENTRY_ISSUE_PREFIX,
    DOMAIN,
    ISSUE_CONFIG_ENTRY_DEAD,
    ISSUE_CONFIG_ENTRY_NEEDS_REAUTH,
    ISSUE_CONFIG_ENTRY_STILL_FAILING,
)
from ..repairs import async_sync_issues
from .config_entry_const import CONFIG_ENTRY_NO_REASON, CONFIG_ENTRY_PAGE_URL
from .config_entry_describe import resolved
from .shapes import Item


def _wanted_issues(hass: HomeAssistant, items: list[Item], *, with_reason: bool = False) -> dict[str, dict[str, str]]:
    """The issue id and placeholders for each item whose entry still exists and has not recovered.

    Looks each entry up live by entry_id, so a title change since the run is
    followed and a since-resolved entry never gets a stale card raised. An
    entry Home Assistant is already reauthenticating is skipped: its own
    reauth card stays the only one. with_reason adds the stored reason, or
    the fixed no-reason word, for the card that shows it.
    """
    wanted: dict[str, dict[str, str]] = {}
    for item in items:
        if item.get("reauth_in_progress"):
            continue
        entry_id = str(item["entry_id"])
        if resolved(hass, entry_id):
            continue
        entry = hass.config_entries.async_get_entry(entry_id)
        assert entry is not None  # resolved() already confirmed the entry exists
        placeholders = {"title": entry.title or entry.domain, "integration": entry.domain}
        if with_reason:
            placeholders["reason"] = str(item["reason"]) if item.get("reason") else CONFIG_ENTRY_NO_REASON
        wanted[f"{CONFIG_ENTRY_ISSUE_PREFIX}{entry_id}"] = placeholders
    return wanted


def _learn_more_urls(wanted: dict[str, dict[str, str]]) -> dict[str, str]:
    """The integration page link for each wanted issue, built only from its own domain.

    Never routed through safe_url: a relative path has no host so that
    helper would reject it, and the domain is the slug Home Assistant's
    loader fixed, never text from the entry.
    """
    return {
        issue_id: CONFIG_ENTRY_PAGE_URL.format(domain=placeholders["integration"]) for issue_id, placeholders in wanted.items()
    }


class ConfigEntryIssueTracker:
    """Owns the recovery-tracking subscription for all three advisory card kinds."""

    def __init__(self) -> None:
        """Start with no active subscription."""
        self._unsub_recovery: CALLBACK_TYPE | None = None

    def sync(self, hass: HomeAssistant, needs_reauth: list[Item], dead: list[Item], still_failing: list[Item]) -> None:
        """Sync the three card kinds under one prefix, each call keeping the others' ids.

        One async_sync_issues call per kind under a shared prefix, each
        passing the other kinds' wanted ids as keep, so no call's stale sweep
        deletes another's cards and an entry moving between kinds keeps its
        id and its dismissal.
        """
        kinds = (
            (ISSUE_CONFIG_ENTRY_NEEDS_REAUTH, _wanted_issues(hass, needs_reauth)),
            (ISSUE_CONFIG_ENTRY_DEAD, _wanted_issues(hass, dead)),
            (ISSUE_CONFIG_ENTRY_STILL_FAILING, _wanted_issues(hass, still_failing, with_reason=True)),
        )
        for translation_key, wanted in kinds:
            others = {issue_id for _, other in kinds if other is not wanted for issue_id in other}
            async_sync_issues(
                hass,
                CONFIG_ENTRY_ISSUE_PREFIX,
                translation_key,
                wanted,
                _learn_more_urls(wanted),
                keep=others - wanted.keys(),
            )

    def watch(self, hass: HomeAssistant, entry_ids: set[str], on_recovered: Callable[[], None]) -> None:
        """Replace the recovery watch with one over entry_ids."""
        self.shutdown()
        if entry_ids:
            self._unsub_recovery = async_track_entry_recovery(hass, entry_ids, on_recovered)

    def shutdown(self) -> None:
        """Cancel the recovery subscription, if any."""
        if self._unsub_recovery is not None:
            self._unsub_recovery()
            self._unsub_recovery = None


@callback
def async_track_entry_recovery(hass: HomeAssistant, entry_ids: set[str], on_recovered: Callable[[], None]) -> CALLBACK_TYPE:
    """Delete a listed entry's card the moment it loads, is disabled, or is removed, then hand the recovery back.

    No immediate check is needed: sync never raises a card for an entry
    already resolved.
    """

    @callback
    def _handle_change(_change: ConfigEntryChange, entry: ConfigEntry) -> None:
        """Delete the entry's card, if it has one, and call back once a listed entry has recovered."""
        if entry.entry_id in entry_ids and resolved(hass, entry.entry_id):
            entry_ids.discard(entry.entry_id)
            ir.async_delete_issue(hass, DOMAIN, f"{CONFIG_ENTRY_ISSUE_PREFIX}{entry.entry_id}")
            on_recovered()

    return async_dispatcher_connect(hass, SIGNAL_CONFIG_ENTRY_CHANGED, _handle_change)
