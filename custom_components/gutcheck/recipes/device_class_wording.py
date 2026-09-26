"""Per-question instructions: the shared base, plus only the boundary case a sensor's own unit needs.

Jev reads instructions literally, so a boundary case is spelled out only for
the sensors it actually applies to, never appended to every question: a
shared addition measurably lowered confidence on unrelated sensors in the
same run (a "%" or "m" note bled into a "ppm" sensor's own answer).
"""

from __future__ import annotations

from typing import Final

from ..const import DEVICE_CLASS_INSTRUCTIONS, DEVICE_CLASS_REUSED_UNIT_SYMBOLS

PERCENT_NOTE: Final = (
    " A value in % alone is not Battery, Humidity, Moisture or Power factor unless the other fields "
    "say it measures exactly that. Choose none of these when the fields show it measures something no "
    "listed device class names, such as the remaining life of a filter or other consumable, a "
    "processor, memory or disk usage, or a device's load."
)

REUSED_SYMBOL_NOTE: Final = (
    " Choose none of these when the fields show `unit` stands for something else, for example months "
    'written as "m", the symbol for meters.'
)


def template_for(unit: str | None) -> str:
    """The unformatted instructions template (with `{index}` still literal) a sensor with this unit gets.

    Unformatted so split.py can re-index it for a slice's own local position.
    """
    template = DEVICE_CLASS_INSTRUCTIONS
    if unit == "%":
        template += PERCENT_NOTE
    if unit in DEVICE_CLASS_REUSED_UNIT_SYMBOLS:
        template += REUSED_SYMBOL_NOTE
    return template


def instructions_for(unit: str | None, index: int) -> str:
    """This sensor's own instructions, formatted for its question's index."""
    return template_for(unit).format(index=index)
