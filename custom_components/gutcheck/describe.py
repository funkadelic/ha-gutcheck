"""Pure duration-to-words helpers. No Home Assistant imports, no raw numbers out."""

_DAY = 86400
_WEEK = 7 * _DAY
_FOUR_WEEKS = 28 * _DAY


def bucket_duration(seconds: float) -> str:
    """Bucket a duration in seconds into a named range."""
    if seconds < _DAY:
        return "less than a day"
    if seconds < _WEEK:
        return "1 to 6 days"
    if seconds < _FOUR_WEEKS:
        return "1 to 4 weeks"
    return "more than 4 weeks"


def bucket_longer_than(days: int) -> str:
    """Describe a lower bound in whole days, for when the real duration is unknown."""
    if days <= 0:
        return "unknown"
    if days == 1:
        return "longer than 1 day"
    return f"longer than {days} days"
