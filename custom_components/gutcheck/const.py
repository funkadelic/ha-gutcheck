"""Constants for the Gut Check integration."""

from datetime import timedelta
from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "gutcheck"
VERSION: Final = "0.1.3"  # x-release-please-version

API_URL: Final = "https://api.typesafe.ai/v1/systemone"
MODEL: Final = "jev-latest"

CONF_DAILY_BUDGET: Final = "daily_budget"
CONF_CRITICAL_LABEL: Final = "critical_label"
CONF_HEALTH_ENABLED: Final = "health_enabled"

DEFAULT_DAILY_BUDGET: Final = 100_000
CHARS_PER_TOKEN: Final = 4
REQUEST_TIMEOUT: Final = 30  # seconds

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
# Every recipe id this integration ships, so async_remove_entry can clean up
# each one's Store without needing a line added by hand for each new recipe.
ALL_RECIPE_IDS: Final = (RECIPE_HEALTH,)
RECIPE_INTERVAL: Final = timedelta(days=7)
FAILED_RUN_RETRY: Final = timedelta(hours=1)

ISSUE_UNAVAILABLE_ENTITY: Final = "unavailable_entity"
HEALTH_ISSUE_PREFIX: Final = "unavailable_"

BLOCKED_DOMAINS: Final = frozenset(
    {
        Platform.LOCK,
        Platform.ALARM_CONTROL_PANEL,
        Platform.COVER,
    }
)

# Choice answers only. Noul thresholds are a probability band modeled
# separately per question and never share this constant.
CHOICE_CONFIDENCE_THRESHOLD: Final = 0.5

OPTION_EXPECTED: Final = "expected"
OPTION_WORTH_FIXING: Final = "worth_fixing"
OPTION_SAFE_TO_REMOVE: Final = "safe_to_remove"
OPTION_NONE: Final = "none_of_these"

HEALTH_OPTIONS: Final = (OPTION_EXPECTED, OPTION_WORTH_FIXING, OPTION_SAFE_TO_REMOVE)

HEALTH_INSTRUCTIONS: Final = (
    "`entities[{index}]` describes one Home Assistant entity that is unavailable right now. "
    "Using only the fields of `entities[{index}]`, decide what the user should do about it."
)

HEALTH_CRITERIA: Final[dict[str, str | None]] = {
    OPTION_EXPECTED: (
        "Being unavailable is normal here and needs no action, for example its `entity_category` is "
        '"diagnostic" or "config" while its `device_other_entities_available` is true, or its domain and '
        "device class describe something that is often switched off or asleep."
    ),
    OPTION_WORTH_FIXING: (
        "It should be working and the user should look into it: its `restored` is false, so a loaded "
        "integration still provides it, and nothing suggests the outage is normal."
    ),
    OPTION_SAFE_TO_REMOVE: (
        "It is left over: its `restored` is true, so no loaded integration provides it any more, and its "
        "`unavailable_for` is long."
    ),
    OPTION_NONE: "The fields do not clearly fit any of the other options.",
}

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
