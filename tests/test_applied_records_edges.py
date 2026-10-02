"""Applied records: what is read back from the Store, what forget writes, and the change-back counts."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.gutcheck.const import STORE_VERSION
from custom_components.gutcheck.recipes.applied_records import AppliedRecords, async_undo_records, undo_choices

from .conftest import register_unit_sensor

KEY = "gutcheck_test_records"


async def test_load_keeps_only_text_to_text_entries(hass: HomeAssistant, hass_storage: dict[str, Any]) -> None:
    """A stored value that is not text is dropped on load, whatever its key."""
    hass_storage[KEY] = {"version": STORE_VERSION, "key": KEY, "data": {"good": "battery", "bad": 5, "worse": None}}
    records = AppliedRecords(hass, KEY)

    await records.async_load()

    assert records.ids() == ("good",)


async def test_forget_saves_what_is_left_under_the_store_version(hass: HomeAssistant, hass_storage: dict[str, Any]) -> None:
    """After forgetting one of two ids, the Store holds the other at the current version."""
    records = AppliedRecords(hass, KEY)
    records.record("a", "battery")
    records.record("b", "humidity")

    await records.async_forget(["a"])

    assert hass_storage[KEY]["version"] == STORE_VERSION
    assert hass_storage[KEY]["data"] == {"b": "humidity"}


async def test_choices_skip_a_removed_sensor_and_still_list_the_ones_after_it(hass: HomeAssistant) -> None:
    """A recorded id that left the registry is passed over, not a stop."""
    kept = register_unit_sensor(hass, "kept", unit="%", name="Kept")
    records = AppliedRecords(hass, KEY)
    records.record("removed-id", "battery")
    records.record(kept.id, "battery")

    assert [choice["value"] for choice in undo_choices(hass, records)] == [kept.id]


async def test_change_back_counts_each_cleared_and_each_left_pick(hass: HomeAssistant) -> None:
    """Two picks cleared, then two left (one not ours, one never recorded) give (2, 2); both recorded ones are rejected."""
    ours = [register_unit_sensor(hass, f"ours{index}", unit="%", name=f"Ours {index}") for index in range(2)]
    other = register_unit_sensor(hass, "other", unit="%", name="Other")
    unrecorded = register_unit_sensor(hass, "unrecorded", unit="%", name="Unrecorded")
    records = AppliedRecords(hass, KEY)
    for sensor in ours:
        records.record(sensor.id, "mine")
    records.record(other.id, "theirs")
    cleared: list[str] = []
    rejected: list[str] = []

    result = await async_undo_records(
        records,
        er.async_get(hass),
        [sensor.id for sensor in (*ours, other, unrecorded)],
        still_ours=lambda _entry, recorded: recorded == "mine",
        clear=lambda entry: cleared.append(entry.id),
        reject=lambda registry_id, _recorded: rejected.append(registry_id),
    )

    assert result == (2, 2)
    assert cleared == [sensor.id for sensor in ours]
    assert sorted(rejected) == sorted([*(sensor.id for sensor in ours), other.id])
    assert records.ids() == ()
