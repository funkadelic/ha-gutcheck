"""Tests for device selection, the area-option rules, and device-question ordering."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.gutcheck.const import AREA_NAME_MAX_CHARS, OPTION_NONE
from custom_components.gutcheck.recipes.area_describe import area_options
from custom_components.gutcheck.recipes.areas import AreaRecipe

from .conftest import AreaEntitySpec, create_areas, register_area_device


async def test_a_service_device_is_never_asked_about(hass: HomeAssistant) -> None:
    """A service device (a hub, an account) is excluded, whatever entities it has."""
    create_areas(hass, "Kitchen")
    register_area_device(hass, "hub", entry_type=dr.DeviceEntryType.SERVICE, entities=["sensor"])

    batch = await AreaRecipe(critical_label=None).async_prepare(hass)

    assert batch.subjects == {}


async def test_a_disabled_device_is_never_asked_about(hass: HomeAssistant) -> None:
    """A disabled device is excluded."""
    create_areas(hass, "Kitchen")
    register_area_device(hass, "disabled", disabled_by=dr.DeviceEntryDisabler.USER, entities=["sensor"])

    batch = await AreaRecipe(critical_label=None).async_prepare(hass)

    assert batch.subjects == {}


async def test_a_device_already_in_an_area_is_never_asked_about(hass: HomeAssistant) -> None:
    """A device that already has an area is excluded."""
    areas = create_areas(hass, "Kitchen")
    register_area_device(hass, "placed", area=areas["Kitchen"], entities=["sensor"])

    batch = await AreaRecipe(critical_label=None).async_prepare(hass)

    assert batch.subjects == {}


async def test_a_device_with_no_entities_is_never_asked_about(hass: HomeAssistant) -> None:
    """A device with no entities at all is excluded."""
    create_areas(hass, "Kitchen")
    register_area_device(hass, "bare")

    batch = await AreaRecipe(critical_label=None).async_prepare(hass)

    assert batch.subjects == {}


async def test_a_device_carrying_the_critical_label_is_never_asked_about(hass: HomeAssistant) -> None:
    """A device carrying the configured critical label is excluded."""
    create_areas(hass, "Kitchen")
    register_area_device(hass, "critical", labels=frozenset({"critical"}), entities=["sensor"])

    batch = await AreaRecipe(critical_label="critical").async_prepare(hass)

    assert batch.subjects == {}


async def test_a_disabled_entity_carrying_the_critical_label_still_excludes_its_device(hass: HomeAssistant) -> None:
    """A disabled entity's critical label still counts: excludes are checked disabled entities included."""
    create_areas(hass, "Kitchen")
    critical_entity = AreaEntitySpec(domain="sensor", disabled_by=er.RegistryEntryDisabler.USER, labels=frozenset({"critical"}))
    register_area_device(hass, "disabled_critical", entities=[critical_entity])

    batch = await AreaRecipe(critical_label="critical").async_prepare(hass)

    assert batch.subjects == {}


async def test_a_device_tracker_entity_excludes_its_device(hass: HomeAssistant) -> None:
    """A device with any device_tracker entity is excluded: it is portable."""
    create_areas(hass, "Kitchen")
    register_area_device(hass, "portable", entities=["device_tracker"])

    batch = await AreaRecipe(critical_label=None).async_prepare(hass)

    assert batch.subjects == {}


async def test_an_entity_with_its_own_area_excludes_its_device(hass: HomeAssistant) -> None:
    """A device with any entity already placed in its own area is excluded."""
    areas = create_areas(hass, "Kitchen")
    placed_entity = AreaEntitySpec(domain="sensor", area=areas["Kitchen"])
    register_area_device(hass, "entity_placed", entities=[placed_entity])

    batch = await AreaRecipe(critical_label=None).async_prepare(hass)

    assert batch.subjects == {}


async def test_a_lock_only_device_is_still_asked_about(hass: HomeAssistant) -> None:
    """A device whose only entity is a lock is still asked: an area is metadata, not control."""
    create_areas(hass, "Kitchen")
    register_area_device(hass, "lock_device", entities=["lock"])

    batch = await AreaRecipe(critical_label=None).async_prepare(hass)

    assert len(batch.subjects) == 1


async def test_a_cover_only_device_is_still_asked_about(hass: HomeAssistant) -> None:
    """A device whose only entity is a cover is still asked."""
    create_areas(hass, "Kitchen")
    register_area_device(hass, "cover_device", entities=["cover"])

    batch = await AreaRecipe(critical_label=None).async_prepare(hass)

    assert len(batch.subjects) == 1


async def test_an_alarm_panel_only_device_is_still_asked_about(hass: HomeAssistant) -> None:
    """A device whose only entity is an alarm control panel is still asked."""
    create_areas(hass, "Kitchen")
    register_area_device(hass, "alarm_device", entities=["alarm_control_panel"])

    batch = await AreaRecipe(critical_label=None).async_prepare(hass)

    assert len(batch.subjects) == 1


async def test_with_one_area_each_question_offers_exactly_two_options(hass: HomeAssistant) -> None:
    """With exactly one area, a question's criteria hold that area plus none of these, and nothing more."""
    create_areas(hass, "Kitchen")
    register_area_device(hass, "plug", entities=["sensor"])

    batch = await AreaRecipe(critical_label=None).async_prepare(hass)

    question = next(iter(batch.questions.values()))
    assert set(question["criteria"].keys()) == {"Kitchen", OPTION_NONE}


async def test_two_areas_that_clean_to_the_same_name_keep_only_the_lower_id(hass: HomeAssistant) -> None:
    """Two areas whose cleaned names collide leave only the lower area id's option."""
    areas = create_areas(hass, "Kitchen", "**Kitchen**")
    lower_id = min(areas["Kitchen"], areas["**Kitchen**"], key=str)

    options = area_options(hass)

    assert options == {"Kitchen": lower_id}


async def test_an_area_whose_cleaned_name_is_empty_is_left_out(hass: HomeAssistant) -> None:
    """An area whose cleaned name cleans to nothing is left out of the criteria entirely."""
    create_areas(hass, "Kitchen", "***")
    register_area_device(hass, "plug", entities=["sensor"])

    batch = await AreaRecipe(critical_label=None).async_prepare(hass)

    question = next(iter(batch.questions.values()))
    assert set(question["criteria"].keys()) == {"Kitchen", OPTION_NONE}


async def test_an_area_name_is_capped_at_its_own_max_chars(hass: HomeAssistant) -> None:
    """An area name longer than AREA_NAME_MAX_CHARS is cut down to it before it ever becomes an option."""
    long_name = "Kitchen " + "x" * 100
    create_areas(hass, long_name)
    register_area_device(hass, "plug", entities=["sensor"])

    batch = await AreaRecipe(critical_label=None).async_prepare(hass)

    question = next(iter(batch.questions.values()))
    option = next(name for name in question["criteria"] if name != OPTION_NONE)
    assert len(option) <= AREA_NAME_MAX_CHARS


async def test_with_no_areas_the_batch_has_no_questions(hass: HomeAssistant) -> None:
    """With no areas at all, nothing is asked and no device selection even runs."""
    register_area_device(hass, "plug", entities=["sensor"])

    batch = await AreaRecipe(critical_label=None).async_prepare(hass)

    assert batch.questions == {}
    assert batch.state == {"devices": []}


async def test_devices_are_asked_in_device_registry_id_order(hass: HomeAssistant) -> None:
    """Two runs over the same, unchanged registries post the devices in the same, id-sorted order."""
    create_areas(hass, "Kitchen")
    first = register_area_device(hass, "a", entities=["sensor"])
    second = register_area_device(hass, "b", entities=["sensor"])
    expected_order = sorted([first.id, second.id])

    first_batch = await AreaRecipe(critical_label=None).async_prepare(hass)
    second_batch = await AreaRecipe(critical_label=None).async_prepare(hass)

    first_order = [subject["registry_id"] for subject in first_batch.subjects.values()]
    second_order = [subject["registry_id"] for subject in second_batch.subjects.values()]
    assert first_order == expected_order
    assert second_order == expected_order
