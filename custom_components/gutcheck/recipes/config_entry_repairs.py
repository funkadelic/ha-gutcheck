"""Two-kind advisory card sync for stuck config entries, and the live recovery listener."""

from __future__ import annotations

from homeassistant.config_entries import SIGNAL_CONFIG_ENTRY_CHANGED, ConfigEntry, ConfigEntryChange
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from ..const import CONFIG_ENTRY_ISSUE_PREFIX, DOMAIN, ISSUE_CONFIG_ENTRY_DEAD, ISSUE_CONFIG_ENTRY_NEEDS_REAUTH
from ..repairs import async_sync_issues
from .config_entry_const import CONFIG_ENTRY_PAGE_URL
from .config_entry_describe import resolved
from .shapes import Item


def _wanted_issues(hass: HomeAssistant, items: list[Item]) -> dict[str, dict[str, str]]:
    """The issue id and placeholders for each item whose entry still exists and has not recovered.

    Looks each entry up live by entry_id, so a title change since the run is
    followed and a since-resolved entry never gets a stale card raised. An
    entry Home Assistant is already reauthenticating is skipped: its own
    reauth card stays the only one.
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
        wanted[f"{CONFIG_ENTRY_ISSUE_PREFIX}{entry_id}"] = {"title": entry.title or entry.domain, "integration": entry.domain}
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


def _watched_entry_ids(*wanted_maps: dict[str, dict[str, str]]) -> dict[str, str]:
    """Every wanted issue's entry_id mapped to its issue id, across both kinds."""
    return {issue_id[len(CONFIG_ENTRY_ISSUE_PREFIX) :]: issue_id for wanted in wanted_maps for issue_id in wanted}


class ConfigEntryIssueTracker:
    """Owns the recovery-tracking subscription for both advisory card kinds."""

    def __init__(self) -> None:
        """Start with no active subscription."""
        self._unsub_recovery: CALLBACK_TYPE | None = None

    def sync(self, hass: HomeAssistant, needs_reauth: list[Item], dead: list[Item]) -> None:
        """Sync both card kinds under one prefix, each call keeping the other's ids, then re-arm recovery.

        Two async_sync_issues calls under one shared prefix, each passing the
        other's wanted ids as keep, so neither call's stale sweep deletes the
        other's cards and an entry moving between kinds keeps its id and its
        dismissal.
        """
        reauth_wanted = _wanted_issues(hass, needs_reauth)
        dead_wanted = _wanted_issues(hass, dead)
        async_sync_issues(
            hass,
            CONFIG_ENTRY_ISSUE_PREFIX,
            ISSUE_CONFIG_ENTRY_NEEDS_REAUTH,
            reauth_wanted,
            _learn_more_urls(reauth_wanted),
            keep=dead_wanted.keys(),
        )
        async_sync_issues(
            hass,
            CONFIG_ENTRY_ISSUE_PREFIX,
            ISSUE_CONFIG_ENTRY_DEAD,
            dead_wanted,
            _learn_more_urls(dead_wanted),
            keep=reauth_wanted.keys(),
        )
        self.shutdown()
        watched = _watched_entry_ids(reauth_wanted, dead_wanted)
        if watched:
            self._unsub_recovery = async_track_entry_recovery(hass, watched)

    def shutdown(self) -> None:
        """Cancel the recovery subscription, if any."""
        if self._unsub_recovery is not None:
            self._unsub_recovery()
            self._unsub_recovery = None


@callback
def async_track_entry_recovery(hass: HomeAssistant, watched: dict[str, str]) -> CALLBACK_TYPE:
    """Delete a watched entry's issue the moment it loads, is disabled, or is removed.

    watched maps entry_id to issue_id. No immediate check is needed: sync
    never raises a card for an entry already resolved.
    """

    @callback
    def _handle_change(_change: ConfigEntryChange, entry: ConfigEntry) -> None:
        """Delete the watched entry's issue once it loads, is disabled, or is removed."""
        issue_id = watched.get(entry.entry_id)
        if issue_id is not None and resolved(hass, entry.entry_id):
            ir.async_delete_issue(hass, DOMAIN, issue_id)

    return async_dispatcher_connect(hass, SIGNAL_CONFIG_ENTRY_CHANGED, _handle_change)
