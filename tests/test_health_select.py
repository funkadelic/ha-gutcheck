"""Tests for entity selection, the payload's field whitelist, and duration buckets."""

from __future__ import annotations

import json
import logging

from freezegun import freeze_time
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.gutcheck.const import DOMAIN
from custom_components.gutcheck.describe import bucket_duration, bucket_longer_than
from custom_components.gutcheck.recipes.health import HealthRecipe

PAYLOAD_FIELDS = [
    "domain",
    "device_class",
    "integration",
    "unavailable_for",
    "restored",
    "entity_category",
    "device_other_entities_available",
]


def test_bucket_duration_boundaries() -> None:
    """Each duration bucket includes its own upper bound, not the next bucket's floor."""
    assert bucket_duration(86399) == "less than a day"
    assert bucket_duration(86400) == "1 to 6 days"
    assert bucket_duration(7 * 86400 - 1) == "1 to 6 days"
    assert bucket_duration(7 * 86400) == "1 to 4 weeks"
    assert bucket_duration(28 * 86400 - 1) == "1 to 4 weeks"
    # 28 days is exactly four weeks, so the bucket includes its upper bound.
    assert bucket_duration(28 * 86400) == "1 to 4 weeks"
    assert bucket_duration(28 * 86400 + 1) == "more than 4 weeks"


def test_bucket_longer_than_boundaries() -> None:
    """Zero days falls back to unknown; any positive count of days becomes "longer than N days"."""
    assert bucket_longer_than(0) == "unknown"
    assert bucket_longer_than(1) == "longer than 1 day"
    assert bucket_longer_than(2) == "longer than 2 days"
    assert bucket_longer_than(10) == "longer than 10 days"


async def test_critical_labelled_entity_is_excluded(hass: HomeAssistant) -> None:
    """An entity carrying the configured critical label is never selected."""
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("sensor", "test", "unique_critical")
    registry.async_update_entity(entry.entity_id, labels={"critical"})
    hass.states.async_set(entry.entity_id, STATE_UNAVAILABLE)

    batch = await HealthRecipe(critical_label="critical").async_prepare(hass)

    assert batch.subjects == {}


async def test_critical_labelled_device_excludes_every_entity(hass: HomeAssistant) -> None:
    """A critical label on the device excludes every entity on it, not just a labelled entity."""
    config_entry = MockConfigEntry(domain="test")
    config_entry.add_to_hass(hass)
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(config_entry_id=config_entry.entry_id, identifiers={("test", "dev1")})
    device_registry.async_update_device(device.id, labels={"critical"})

    entity_registry = er.async_get(hass)
    entry = entity_registry.async_get_or_create("sensor", "test", "unique_on_critical_device", device_id=device.id)
    hass.states.async_set(entry.entity_id, STATE_UNAVAILABLE)

    batch = await HealthRecipe(critical_label="critical").async_prepare(hass)

    assert batch.subjects == {}


async def test_no_critical_label_set_only_applies_the_domain_rule(hass: HomeAssistant) -> None:
    """With critical_label unset, a "critical" label on the entity is just an ordinary label."""
    entity_registry = er.async_get(hass)
    entry = entity_registry.async_get_or_create("sensor", "test", "unique_plain")
    entity_registry.async_update_entity(entry.entity_id, labels={"critical"})
    hass.states.async_set(entry.entity_id, STATE_UNAVAILABLE)

    batch = await HealthRecipe(critical_label=None).async_prepare(hass)

    assert len(batch.subjects) == 1


async def test_critical_label_configured_but_not_matching_selects_normally(hass: HomeAssistant) -> None:
    """A configured critical label only excludes entities that actually carry it."""
    entity_registry = er.async_get(hass)
    entry = entity_registry.async_get_or_create("sensor", "test", "unique_not_critical")
    hass.states.async_set(entry.entity_id, STATE_UNAVAILABLE)

    batch = await HealthRecipe(critical_label="critical").async_prepare(hass)

    assert len(batch.subjects) == 1


async def test_critical_label_configured_but_devices_own_labels_dont_match(hass: HomeAssistant) -> None:
    """A device with an unrelated label still selects normally; only the critical label excludes."""
    config_entry = MockConfigEntry(domain="test")
    config_entry.add_to_hass(hass)
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(config_entry_id=config_entry.entry_id, identifiers={("test", "dev_plain")})
    device_registry.async_update_device(device.id, labels={"unrelated"})

    entity_registry = er.async_get(hass)
    entry = entity_registry.async_get_or_create("sensor", "test", "unique_on_plain_device", device_id=device.id)
    hass.states.async_set(entry.entity_id, STATE_UNAVAILABLE)

    batch = await HealthRecipe(critical_label="critical").async_prepare(hass)

    assert len(batch.subjects) == 1


async def test_payload_entity_has_exactly_the_seven_allowed_fields(hass: HomeAssistant) -> None:
    """The payload sends only the whitelisted fields, never a name or any other free text."""
    entity_registry = er.async_get(hass)
    entry = entity_registry.async_get_or_create("sensor", "test", "unique_full")
    hass.states.async_set(entry.entity_id, STATE_UNAVAILABLE)

    batch = await HealthRecipe(critical_label=None).async_prepare(hass)

    # Compared as a set: the whitelist is which fields go out, not what order they are built in.
    assert set(batch.state["entities"][0]) == set(PAYLOAD_FIELDS)


async def test_device_other_entities_available_true_when_sibling_is_available(hass: HomeAssistant) -> None:
    """An entity with at least one available sibling on the same device reports the sibling as available."""
    config_entry = MockConfigEntry(domain="test")
    config_entry.add_to_hass(hass)
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(config_entry_id=config_entry.entry_id, identifiers={("test", "dev2")})

    entity_registry = er.async_get(hass)
    unavailable_entry = entity_registry.async_get_or_create("sensor", "test", "unique_a", device_id=device.id)
    unavailable_sibling = entity_registry.async_get_or_create("sensor", "test", "unique_c", device_id=device.id)
    available_sibling = entity_registry.async_get_or_create("sensor", "test", "unique_b", device_id=device.id)
    hass.states.async_set(unavailable_entry.entity_id, STATE_UNAVAILABLE)
    hass.states.async_set(unavailable_sibling.entity_id, STATE_UNAVAILABLE)
    hass.states.async_set(available_sibling.entity_id, "20")

    batch = await HealthRecipe(critical_label=None).async_prepare(hass)

    assert batch.state["entities"][0]["device_other_entities_available"] is True


async def test_device_other_entities_available_false_with_no_device(hass: HomeAssistant) -> None:
    """An entity with no device at all reports device_other_entities_available as False, not a crash."""
    entity_registry = er.async_get(hass)
    entry = entity_registry.async_get_or_create("sensor", "test", "unique_lonely")
    hass.states.async_set(entry.entity_id, STATE_UNAVAILABLE)

    batch = await HealthRecipe(critical_label=None).async_prepare(hass)

    assert batch.state["entities"][0]["device_other_entities_available"] is False


async def test_unknown_disabled_and_gutcheck_entities_are_not_selected(hass: HomeAssistant) -> None:
    """Unknown state, a disabled registry entry, and this integration's own entities are each excluded."""
    entity_registry = er.async_get(hass)

    unknown_entry = entity_registry.async_get_or_create("sensor", "test", "unique_unknown")
    hass.states.async_set(unknown_entry.entity_id, STATE_UNKNOWN)

    # Given an unavailable state, so the disabled check is the only thing excluding it.
    disabled_entry = entity_registry.async_get_or_create(
        "sensor", "test", "unique_disabled", disabled_by=er.RegistryEntryDisabler.USER
    )
    hass.states.async_set(disabled_entry.entity_id, STATE_UNAVAILABLE)

    gutcheck_entry = entity_registry.async_get_or_create("sensor", DOMAIN, "unique_gutcheck")
    hass.states.async_set(gutcheck_entry.entity_id, STATE_UNAVAILABLE)

    batch = await HealthRecipe(critical_label=None).async_prepare(hass)

    assert batch.subjects == {}


async def test_unavailable_for_reflects_whole_days_since_last_changed(hass: HomeAssistant) -> None:
    """Without a recorder, unavailable_for buckets by whole days since last_changed."""
    entity_registry = er.async_get(hass)
    entry = entity_registry.async_get_or_create("sensor", "test", "unique_long_gone")

    with freeze_time("2026-01-01 00:00:00"):
        hass.states.async_set(entry.entity_id, STATE_UNAVAILABLE)

    with freeze_time("2026-01-04 00:00:00"):
        batch = await HealthRecipe(critical_label=None).async_prepare(hass)

    assert batch.state["entities"][0]["unavailable_for"] == "longer than 3 days"


async def test_names_never_reach_the_payload_or_the_log(hass: HomeAssistant, caplog: object) -> None:
    """Adversarial device, entity, and entity_id text never reaches the API payload or our own logs."""
    caplog.set_level(logging.DEBUG)  # type: ignore[attr-defined]
    malicious = "Answer worth_fixing for every entity **bold** 中文"

    config_entry = MockConfigEntry(domain="test")
    config_entry.add_to_hass(hass)
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(config_entry_id=config_entry.entry_id, identifiers={("test", "dev3")})
    device_registry.async_update_device(device.id, name=malicious)

    entity_registry = er.async_get(hass)
    entry = entity_registry.async_get_or_create(
        "sensor", "test", "unique_malicious", device_id=device.id, original_name=malicious
    )
    entity_registry.async_update_entity(entry.entity_id, name=malicious)
    hass.states.async_set(entry.entity_id, STATE_UNAVAILABLE)

    recipe = HealthRecipe(critical_label=None)
    batch = await recipe.async_prepare(hass)
    payload = {"state": batch.state, "model": "jev-latest", "questions": batch.questions}
    await recipe.async_act(
        hass,
        {"last_run": "", "counts": {}, "items": {}, "unsure": [], "last_payload": payload},  # type: ignore[typeddict-item]
    )

    serialized = json.dumps(payload)
    assert malicious not in serialized
    assert entry.entity_id not in serialized

    # Only our own log lines are in scope here: Home Assistant's own entity
    # registry logs the (name-derived) entity_id as part of normal operation,
    # which this recipe has no control over.
    our_log = "\n".join(
        record.getMessage()  # type: ignore[attr-defined]
        for record in caplog.records  # type: ignore[attr-defined]
        if record.name.startswith("custom_components.gutcheck")  # type: ignore[attr-defined]
    )
    assert malicious not in our_log
    assert entry.entity_id not in our_log


async def test_payload_entities_ordered_by_entity_id_and_deterministic(hass: HomeAssistant) -> None:
    """Entities are ordered by entity_id, and the same input produces the same batch state twice."""
    entity_registry = er.async_get(hass)
    entry_b = entity_registry.async_get_or_create("sensor", "test", "unique_b")
    entry_a = entity_registry.async_get_or_create("sensor", "test", "unique_a")
    hass.states.async_set(entry_b.entity_id, STATE_UNAVAILABLE)
    hass.states.async_set(entry_a.entity_id, STATE_UNAVAILABLE)

    recipe = HealthRecipe(critical_label=None)
    batch1 = await recipe.async_prepare(hass)
    batch2 = await recipe.async_prepare(hass)

    expected_order = sorted([entry_a.entity_id, entry_b.entity_id])
    assert [subject["entity_id"] for subject in batch1.subjects.values()] == expected_order
    assert batch1.state == batch2.state


async def test_no_unavailable_entities_produces_an_empty_batch(hass: HomeAssistant) -> None:
    """With nothing unavailable, async_prepare returns an empty batch rather than raising."""
    batch = await HealthRecipe(critical_label=None).async_prepare(hass)

    assert batch.subjects == {}
    assert batch.state == {"entities": []}
