"""Constants for the Gut Check integration."""

from datetime import timedelta
from typing import Final

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN, Platform

DOMAIN: Final = "gutcheck"
VERSION: Final = "0.11.0"  # x-release-please-version

API_URL: Final = "https://api.typesafe.ai/v1/systemone"
MODEL: Final = "jev-latest"

CONF_DAILY_BUDGET: Final = "daily_budget"
CONF_CRITICAL_LABEL: Final = "critical_label"
CONF_HEALTH_ENABLED: Final = "health_enabled"
CONF_AREAS_ENABLED: Final = "areas_enabled"
CONF_DEVICE_CLASS_ENABLED: Final = "device_class_enabled"
CONF_CONFIG_ENTRIES_ENABLED: Final = "config_entries_enabled"
CONF_CRITICAL_LABEL_ENABLED: Final = "critical_label_enabled"
CONF_HIDE_DIAGNOSTIC_ENABLED: Final = "hide_diagnostic_enabled"
# The Configure checkbox and step id for changing back anything Gut Check set (a
# device class or a hidden sensor); never saved as an option.
CONF_CHANGE_BACK: Final = "change_back"
CONF_UNDO_SENSORS: Final = "sensors"
CONF_UNDO_HIDDEN_SENSORS: Final = "hidden_sensors"

# Sized for a large install's first day, when every enabled check reserves at
# once: about 150,000 on a 1,300-entity install with 149 unavailable entities.
DEFAULT_DAILY_BUDGET: Final = 500_000
CHARS_PER_TOKEN: Final = 4
# Budget reservations only, held until the API reports real usage; captured
# runs have billed as few as 2.3 characters per token, so 2 stays under the
# lowest captured ratio. Request caps and split.py keep CHARS_PER_TOKEN at 4.
BUDGET_CHARS_PER_TOKEN: Final = 2
REQUEST_TIMEOUT: Final = 30  # seconds
RELEASE_NOTES_FETCH_TIMEOUT: Final = 10  # seconds

PRICE_PER_MTOK_USD: Final = 0.042
STORE_VERSION: Final = 1
BUDGET_STORE_KEY: Final = f"{DOMAIN}.budget"
SIGNAL_BUDGET_UPDATED: Final = f"{DOMAIN}_budget_updated"
ATTR_DAILY_BUDGET: Final = "daily_budget"
ATTR_REMAINING: Final = "remaining"

REQUEST_TOKEN_LIMIT: Final = 64_000
STATE_TOKEN_LIMIT: Final = 32_000

MAX_RETRIES: Final = 3
BACKOFF_BASE: Final = 1.0  # seconds
MAX_RETRY_DELAY: Final = 60.0  # seconds

RECIPE_HEALTH: Final = "health"
RECIPE_UPDATES: Final = "updates"
RECIPE_AREAS: Final = "areas"
RECIPE_DEVICE_CLASS: Final = "device_class"
RECIPE_CONFIG_ENTRIES: Final = "config_entries"
RECIPE_CRITICAL_LABEL: Final = "critical_label"
RECIPE_HIDE_DIAGNOSTIC: Final = "hide_diagnostic"
# Every recipe id this integration ships, so async_remove_entry can clean up
# each one's Store without needing a line added by hand for each new recipe.
ALL_RECIPE_IDS: Final = (
    RECIPE_HEALTH,
    RECIPE_UPDATES,
    RECIPE_AREAS,
    RECIPE_DEVICE_CLASS,
    RECIPE_CONFIG_ENTRIES,
    RECIPE_CRITICAL_LABEL,
    RECIPE_HIDE_DIAGNOSTIC,
)
RECIPE_INTERVAL: Final = timedelta(days=7)
FAILED_RUN_RETRY: Final = timedelta(hours=1)

ISSUE_UNAVAILABLE_ENTITY: Final = "unavailable_entity"
HEALTH_ISSUE_PREFIX: Final = "unavailable_"
# Pinned: issue ids persist in Home Assistant's issue registry, so this
# shape is never changed once shipped, only added to.
UPDATES_ISSUE_PREFIX: Final = "update_"
ISSUE_POSSIBLY_BREAKING_UPDATE: Final = "possibly_breaking_update"
AREA_ISSUE_PREFIX: Final = "area_"
DEVICE_CLASS_ISSUE_PREFIX: Final = "device_class_"
CONFIG_ENTRY_ISSUE_PREFIX: Final = "config_entry_"
CRITICAL_LABEL_ISSUE_PREFIX: Final = "critical_label_"
HIDE_DIAGNOSTIC_ISSUE_PREFIX: Final = "hide_diagnostic_"
ISSUE_AREA_SUGGESTION: Final = "area_suggestion"
ISSUE_DEVICE_CLASS_SUGGESTION: Final = "device_class_suggestion"
ISSUE_CONFIG_ENTRY_NEEDS_REAUTH: Final = "config_entry_needs_reauth"
ISSUE_CONFIG_ENTRY_DEAD: Final = "config_entry_dead"
ISSUE_CRITICAL_LABEL_SUGGESTION: Final = "critical_label_suggestion"
ISSUE_HIDE_DIAGNOSTIC_SUGGESTION: Final = "hide_diagnostic_suggestion"

BLOCKED_DOMAINS: Final = frozenset(
    {
        Platform.LOCK,
        Platform.ALARM_CONTROL_PANEL,
        Platform.COVER,
    }
)

# An entity reports one of these on a reload, a device dropping off the
# network, or a source going quiet. Each says an answer is missing, never
# what the answer is, so neither ends a finding nor clears its card.
NO_VERDICT_STATES: Final = frozenset({STATE_UNAVAILABLE, STATE_UNKNOWN})

# Most subjects one request carries. Answers drift when many subjects share
# one request's state; ten per request measured accurate on the target
# install at about the same token cost.
SUBJECTS_PER_REQUEST: Final = 10

# Choice answers only. Noul thresholds are a probability band modeled
# separately per question and never share this constant.
CHOICE_CONFIDENCE_THRESHOLD: Final = 0.5

OPTION_EXPECTED: Final = "expected"
OPTION_WORTH_FIXING: Final = "worth_fixing"
OPTION_SAFE_TO_REMOVE: Final = "safe_to_remove"
OPTION_NONE: Final = "none_of_these"

HEALTH_OPTIONS: Final = (OPTION_EXPECTED, OPTION_WORTH_FIXING, OPTION_SAFE_TO_REMOVE)

# Probabilities arrive rounded to two decimals, so a valid spread can sum a little over 1.
PROBABILITY_ROUNDING_ALLOWANCE: Final = 0.02

CONF_UPDATES_ENABLED: Final = "updates_enabled"

OPTION_ROUTINE: Final = "routine"
OPTION_FEATURE: Final = "feature"
OPTION_POSSIBLY_BREAKING: Final = "possibly_breaking"

# A tuple, not a set: order is the score level, so index 0 is level 0.
UPDATE_OPTIONS: Final = (OPTION_ROUTINE, OPTION_FEATURE, OPTION_POSSIBLY_BREAKING)

RELEASE_NOTES_MAX_CHARS: Final = 1500

VERSION_JUMP_PATCH: Final = "patch-level change"
VERSION_JUMP_MINOR: Final = "minor version change"
VERSION_JUMP_MAJOR: Final = "major version change"
VERSION_JUMP_UNKNOWN: Final = "version change of unknown size"

VALIDATION_REQUEST: Final = {
    "state": {"check": "ping"},
    "model": MODEL,
    "questions": {
        "q": {
            "type": "noul",
            "instructions": 'Does `check` equal "ping"?',
        }
    },
}

ATTR_COUNTS: Final = "counts"
ATTR_ITEMS: Final = "items"
ATTR_UNSURE: Final = "unsure"
ATTR_LAST_PAYLOAD: Final = "last_payload"
ATTR_LAST_RUN: Final = "last_run"

OPTION_SUGGESTED: Final = "suggested"
# Set on a suggestion the per-run card cap held back, so a restore never raises its card.
ITEM_HELD_BACK: Final = "held_back"
# A single fixed bucket, never one per area: the coordinator seeds result
# buckets from this tuple, and an install's areas are neither fixed nor
# small. The chosen area rides along on the item's own choice field instead.
AREA_OPTIONS: Final = (OPTION_SUGGESTED,)

# 60 fits real device names. Shared by area and device class suggestions.
DEVICE_TEXT_MAX_CHARS: Final = 60

# Which device classes Gut Check set and which sensors it hid, each kept out
# of the recipe's own Store because every run rewrites that one while
# confirms land between runs.
DEVICE_CLASS_APPLIED_STORE_KEY: Final = f"{DOMAIN}.recipe_device_class_applied"
HIDE_DIAGNOSTIC_APPLIED_STORE_KEY: Final = f"{DOMAIN}.recipe_hide_diagnostic_applied"
