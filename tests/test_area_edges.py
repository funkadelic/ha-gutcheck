"""Area recipe edges: area names that are skipped, device text caps, disabled entities, card helpers and guards."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from custom_components.gutcheck.const import (
    AREA_ISSUE_PREFIX,
    DEVICE_TEXT_MAX_CHARS,
    DOMAIN,
    ISSUE_AREA_SUGGESTION,
    OPTION_NONE,
    OPTION_SUGGESTED,
)
from custom_components.gutcheck.recipes.area_cards import _resolve, _still_qualifies, sync_area_cards
from custom_components.gutcheck.recipes.area_const import AREA_NONE_DESCRIPTION
from custom_components.gutcheck.recipes.area_describe import area_criteria, area_options, describe
from custom_components.gutcheck.recipes.area_repairs import assign_area
from custom_components.gutcheck.recipes.areas import AreaRecipe
from custom_components.gutcheck.recipes.safety import SafetyRules

from .conftest import AreaEntitySpec, create_areas, health_result, register_area_device


async def test_an_area_named_like_the_none_choice_does_not_hide_the_ones_after_it(hass: HomeAssistant) -> None:
    """The none-of-these name is left out, and areas that sort after it still become options."""
    ar.async_get(hass).async_create(OPTION_NONE)
    zebra = ar.async_get(hass).async_create("Zebra")

    assert area_options(hass) == {"Zebra": zebra.id}


def test_the_none_choice_keeps_its_description_in_the_criteria() -> None:
    """Area names have no description; none of these carries the fixed one."""
    assert area_criteria({"Kitchen": "kitchen"}) == {"Kitchen": None, OPTION_NONE: AREA_NONE_DESCRIPTION}


async def test_device_text_is_capped_and_disabled_entities_still_count(hass: HomeAssistant) -> None:
    """A long manufacturer and model are cut to the cap, and a disabled entity's domain and class are reported."""
    long = "m" * (DEVICE_TEXT_MAX_CHARS + 20)
    device = register_area_device(
        hass,
        "d",
        manufacturer=long,
        model=long,
        entities=[
            AreaEntitySpec("sensor"),
            AreaEntitySpec("switch", device_class="outlet", disabled_by=er.RegistryEntryDisabler.USER),
        ],
    )

    state_item, _subject = describe(hass, device)

    assert len(state_item["manufacturer"]) <= DEVICE_TEXT_MAX_CHARS
    assert len(state_item["model"]) <= DEVICE_TEXT_MAX_CHARS
    assert state_item["entity_domains"] == ["sensor", "switch"]
    assert state_item["device_classes"] == ["outlet"]


async def test_a_suggestion_that_no_longer_resolves_does_not_hide_the_ones_after_it(hass: HomeAssistant) -> None:
    """An unknown device, then an unknown area, then a good one: only the good one gets a card entry."""
    areas = create_areas(hass, "Kitchen")
    good = register_area_device(hass, "good", name="Good", entities=["sensor"])
    suggested = [
        {"registry_id": "no-such-device", "choice": "Kitchen"},
        {"registry_id": good.id, "choice": "No such area"},
        {"registry_id": good.id, "choice": "Kitchen"},
    ]

    resolved = _resolve(hass, area_options(hass), suggested)

    assert list(resolved) == [f"{AREA_ISSUE_PREFIX}{good.id}"]
    card = resolved[f"{AREA_ISSUE_PREFIX}{good.id}"]
    assert card.data == {"device_id": good.id, "area_id": areas["Kitchen"]}
    assert card.subject_id == good.id


async def test_the_card_names_the_device_by_the_best_field_it_has(hass: HomeAssistant) -> None:
    """User name, name, model, manufacturer, then the device id, each used when the ones before are empty."""
    create_areas(hass, "Kitchen")
    renamed = register_area_device(hass, "renamed", name="Plug", name_by_user="Desk lamp", model="M-1")
    named = register_area_device(hass, "named", name="Plug", model="M-1")
    maker = register_area_device(hass, "maker", name=None, manufacturer="Acme")
    modeled = register_area_device(hass, "modeled", name=None, model="M-1")
    bare = register_area_device(hass, "bare", name=None)
    devices = (renamed, named, maker, modeled, bare)
    suggested = [{"registry_id": device.id, "choice": "Kitchen"} for device in devices]

    resolved = _resolve(hass, area_options(hass), suggested)

    names = [resolved[f"{AREA_ISSUE_PREFIX}{device.id}"].placeholders["device_name"] for device in devices]
    assert names == ["Desk lamp", "Plug", "Acme", "M-1", bare.id]


async def test_a_card_stays_only_for_a_device_that_still_qualifies(hass: HomeAssistant) -> None:
    """A missing device and a device already placed in an area both stop qualifying."""
    areas = create_areas(hass, "Kitchen")
    free = register_area_device(hass, "free", entities=["sensor"])
    placed = register_area_device(hass, "placed", area=areas["Kitchen"], entities=["sensor"])
    safety = SafetyRules(None)

    assert _still_qualifies(hass, safety, f"{AREA_ISSUE_PREFIX}{free.id}") is True
    assert _still_qualifies(hass, safety, f"{AREA_ISSUE_PREFIX}{placed.id}") is False
    assert _still_qualifies(hass, safety, f"{AREA_ISSUE_PREFIX}missing") is False


async def test_the_area_card_carries_the_area_suggestion_text(hass: HomeAssistant) -> None:
    """The issue is raised under the area suggestion translation key."""
    create_areas(hass, "Kitchen")
    device = register_area_device(hass, "d", entities=["sensor"])

    sync_area_cards(hass, SafetyRules(None), [{"registry_id": device.id, "choice": "Kitchen", "confidence": 0.9}])

    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"{AREA_ISSUE_PREFIX}{device.id}")
    assert issue is not None
    assert issue.translation_key == ISSUE_AREA_SUGGESTION


async def test_an_assignment_with_one_malformed_id_writes_nothing(hass: HomeAssistant) -> None:
    """A good area id beside an unhashable device id is refused, not raised on."""
    areas = create_areas(hass, "Kitchen")

    assert assign_area(hass, SafetyRules(None), {"device_id": ["x"], "area_id": areas["Kitchen"]}) is False


async def test_a_result_with_no_suggested_bucket_acts_and_restores_without_error(hass: HomeAssistant) -> None:
    """Acting on and restoring a result that lacks the suggested bucket syncs an empty set."""
    recipe = AreaRecipe(None)
    result = health_result({})

    await recipe.async_act(hass, result)
    await recipe.restore(hass, result)

    assert OPTION_SUGGESTED not in result["items"]
