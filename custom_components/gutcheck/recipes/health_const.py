"""Recipe-local constants for the home health check: lean hint, leftover rule and the model question."""

from typing import Final

from ..const import OPTION_EXPECTED, OPTION_NONE, OPTION_SAFE_TO_REMOVE, OPTION_WORTH_FIXING

# Lean hint on unsure health entries. Compared against summed probabilities,
# not the API's confidence field, which runs about 0.1 below the top probability.
HEALTH_LEAN_THRESHOLD: Final = 0.7
LEAN_NEEDS_ATTENTION: Final = "needs_attention"

# A restored entity gone at least this many days, with its integration loaded
# or no config entry, is sorted as safe to remove without asking. Capped at the
# recorder's retention (purge_keep_days) when that is shorter.
HEALTH_LEFTOVER_DAYS: Final = 31

HEALTH_INSTRUCTIONS: Final = (
    "`entities[{index}]` describes one Home Assistant entity that is unavailable right now. "
    "Using only the fields of `entities[{index}]`, decide what the user should do about it."
)

HEALTH_CRITERIA: Final[dict[str, str | None]] = {
    OPTION_EXPECTED: (
        'Its `config_entry_state` is "loaded" or missing, `restored` is false, and either '
        "`device_other_entities_available` is true (the device still reports, only this entity is idle) or "
        "its domain and device class describe something often switched off or asleep."
    ),
    OPTION_WORTH_FIXING: (
        'Its `config_entry_state` is "setup error", "setup retry" or "migration error"; or '
        '`config_entry_state` is "loaded" or missing, `restored` is false, '
        "`device_other_entities_available` is false, and its domain and device class do not describe "
        "something often switched off or asleep."
    ),
    OPTION_SAFE_TO_REMOVE: (
        "Its `restored` is true, its `config_entry_state` is "
        '"loaded" or missing, and its `unavailable_for` is "1 to 4 weeks", "more than 4 weeks", '
        '"longer than 1 week" or "longer than 4 weeks".'
    ),
    OPTION_NONE: (
        "Anything else, for example any other `config_entry_state`, or `restored` is true with "
        '`unavailable_for` "less than a day", "1 to 6 days", "longer than 1 day" or "unknown".'
    ),
}
