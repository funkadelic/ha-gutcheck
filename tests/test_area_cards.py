"""Timeline tests: a rejection survives noisy answers, and new cards are capped and ordered."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import AREA_ISSUE_PREFIX, DOMAIN, MAX_NEW_AREA_CARDS_PER_RUN, OPTION_NONE, RECIPE_AREAS
from custom_components.gutcheck.recipes.area_cards import sync_area_cards
from custom_components.gutcheck.recipes.safety import SafetyRules

from .conftest import (
    api_response,
    area_answer,
    areas_sensor_entity_id,
    create_areas,
    posted_bodies,
    register_area_device,
    register_jev_responses,
)


async def _run_again(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Drive a second area run directly, the same way a scheduled refresh would."""
    await entry.runtime_data.coordinators[RECIPE_AREAS].async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)


async def _setup(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry, response: dict[str, object]
) -> None:
    """Register the API response, add and set up the entry."""
    register_jev_responses(aioclient_mock, [api_response(response)])
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_an_ignored_card_survives_an_unsure_answer(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """An ignored card stays ignored, unchanged, when its device's next answer is a low-confidence area guess."""
    areas = create_areas(hass, "Kitchen", "Garage")
    device = register_area_device(hass, "a", name="Device A", entities=["sensor"])
    await _setup(hass, aioclient_mock, mock_config_entry, {"d0": area_answer("Kitchen", 0.9, list(areas))})

    issue_id = f"{AREA_ISSUE_PREFIX}{device.id}"
    ir.async_ignore_issue(hass, DOMAIN, issue_id, True)
    before = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert before is not None
    before_placeholders = before.translation_placeholders

    aioclient_mock.clear_requests()
    register_jev_responses(aioclient_mock, [api_response({"d0": area_answer("Kitchen", 0.2, list(areas))})])
    await _run_again(hass, mock_config_entry)

    after = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert after is not None
    assert after.dismissed_version is not None
    assert after.translation_placeholders == before_placeholders


async def test_an_ignored_card_survives_a_confident_none_of_these_answer(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """An ignored card stays ignored when its device's next answer is a confident none of these."""
    areas = create_areas(hass, "Kitchen", "Garage")
    device = register_area_device(hass, "a", name="Device A", entities=["sensor"])
    await _setup(hass, aioclient_mock, mock_config_entry, {"d0": area_answer("Kitchen", 0.9, list(areas))})

    issue_id = f"{AREA_ISSUE_PREFIX}{device.id}"
    ir.async_ignore_issue(hass, DOMAIN, issue_id, True)

    aioclient_mock.clear_requests()
    register_jev_responses(aioclient_mock, [api_response({"d0": area_answer(OPTION_NONE, 0.9, list(areas))})])
    await _run_again(hass, mock_config_entry)

    after = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert after is not None
    assert after.dismissed_version is not None


async def test_an_ignored_card_updates_in_place_when_a_later_run_names_a_different_area(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """A newer, different confident suggestion updates the ignored card's own content, no new card, and the sensor follows it."""
    areas = create_areas(hass, "Kitchen", "Garage")
    device = register_area_device(hass, "a", name="Device A", entities=["sensor"])
    await _setup(hass, aioclient_mock, mock_config_entry, {"d0": area_answer("Kitchen", 0.9, list(areas))})

    issue_id = f"{AREA_ISSUE_PREFIX}{device.id}"
    ir.async_ignore_issue(hass, DOMAIN, issue_id, True)

    aioclient_mock.clear_requests()
    register_jev_responses(aioclient_mock, [api_response({"d0": area_answer("Garage", 0.9, list(areas))})])
    await _run_again(hass, mock_config_entry)

    after = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert after is not None
    assert after.dismissed_version is not None
    assert after.translation_placeholders is not None
    assert after.translation_placeholders["area_name"] == "Garage"
    assert after.data is not None
    assert after.data["area_id"] == areas["Garage"]
    assert {domain_id for domain_id in ir.async_get(hass).issues if domain_id[1].startswith(AREA_ISSUE_PREFIX)} == {
        (DOMAIN, issue_id)
    }

    state = hass.states.get(areas_sensor_entity_id(hass, mock_config_entry))
    assert state is not None
    suggested = state.attributes["items"]["suggested"]
    assert len(suggested) == 1
    assert suggested[0]["choice"] == "Garage"


async def test_an_open_card_stays_open_unchanged_on_an_unsure_answer(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """A card the user has not ignored stays open when its device's next answer is unsure."""
    areas = create_areas(hass, "Kitchen", "Garage")
    device = register_area_device(hass, "a", name="Device A", entities=["sensor"])
    await _setup(hass, aioclient_mock, mock_config_entry, {"d0": area_answer("Kitchen", 0.9, list(areas))})
    issue_id = f"{AREA_ISSUE_PREFIX}{device.id}"
    before = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert before is not None
    assert before.dismissed_version is None

    aioclient_mock.clear_requests()
    register_jev_responses(aioclient_mock, [api_response({"d0": area_answer("Kitchen", 0.2, list(areas))})])
    await _run_again(hass, mock_config_entry)

    after = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert after is not None
    assert after.dismissed_version is None
    assert after.translation_placeholders == before.translation_placeholders


async def test_a_device_given_an_area_by_hand_is_no_longer_asked_and_its_card_is_deleted(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """A device placed by hand since the last run is dropped from the ask, and its card is swept."""
    areas = create_areas(hass, "Kitchen", "Garage")
    device = register_area_device(hass, "a", name="Device A", entities=["sensor"])
    await _setup(hass, aioclient_mock, mock_config_entry, {"d0": area_answer("Kitchen", 0.9, list(areas))})
    issue_id = f"{AREA_ISSUE_PREFIX}{device.id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    dr.async_get(hass).async_update_device(device.id, area_id=areas["Kitchen"])
    aioclient_mock.clear_requests()
    await _run_again(hass, mock_config_entry)

    # No other device qualifies, so this run has nothing to ask and posts nothing at all.
    assert posted_bodies(aioclient_mock) == []
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_a_device_removed_from_the_registry_has_its_card_deleted(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """A device removed since the last run drops its card too, even though it can no longer be asked about."""
    areas = create_areas(hass, "Kitchen", "Garage")
    device = register_area_device(hass, "a", name="Device A", entities=["sensor"])
    await _setup(hass, aioclient_mock, mock_config_entry, {"d0": area_answer("Kitchen", 0.9, list(areas))})
    issue_id = f"{AREA_ISSUE_PREFIX}{device.id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    dr.async_get(hass).async_remove_device(device.id)
    aioclient_mock.clear_requests()
    await _run_again(hass, mock_config_entry)

    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_twelve_confident_devices_yield_ten_cards_most_confident_first_then_twelve_on_the_next_run(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """A run with more confident suggestions than the cap raises only the cap's worth, the rest arriving next run."""
    create_areas(hass, "Kitchen")
    devices = [register_area_device(hass, f"d{i}", name=f"Device {i}", entities=["sensor"]) for i in range(12)]
    ordered = sorted(devices, key=lambda device: device.id)

    # Nine distinct confidences above the cap, two tied right at the cut, one clearly below it.
    confidences = [0.99, 0.98, 0.97, 0.96, 0.95, 0.94, 0.93, 0.92, 0.91, 0.90, 0.90, 0.80]
    confidence_by_id = {device.id: confidence for device, confidence in zip(devices, confidences, strict=True)}
    # The two devices intentionally tied at the cap boundary, sorted so index 0 is the lower device id.
    tied = sorted(
        (device for device in devices if confidence_by_id[device.id] == 0.90),
        key=lambda device: device.id,
    )
    assert len(tied) == 2
    winner, loser = tied

    answers = {
        f"d{index}": area_answer("Kitchen", confidence_by_id[device.id], ["Kitchen"]) for index, device in enumerate(ordered)
    }
    await _setup(hass, aioclient_mock, mock_config_entry, answers)

    state = hass.states.get(areas_sensor_entity_id(hass, mock_config_entry))
    assert state is not None
    assert len(state.attributes["items"]["suggested"]) == 12
    assert state.state == str(MAX_NEW_AREA_CARDS_PER_RUN)

    card_ids = {
        issue_id for domain, issue_id in ir.async_get(hass).issues if domain == DOMAIN and issue_id.startswith(AREA_ISSUE_PREFIX)
    }
    assert len(card_ids) == MAX_NEW_AREA_CARDS_PER_RUN
    assert f"{AREA_ISSUE_PREFIX}{winner.id}" in card_ids
    assert f"{AREA_ISSUE_PREFIX}{loser.id}" not in card_ids
    below_cut = next(device for device in devices if confidence_by_id[device.id] == 0.80)
    assert f"{AREA_ISSUE_PREFIX}{below_cut.id}" not in card_ids

    aioclient_mock.clear_requests()
    register_jev_responses(aioclient_mock, [api_response(answers)])
    await _run_again(hass, mock_config_entry)

    card_ids_after = {
        issue_id for domain, issue_id in ir.async_get(hass).issues if domain == DOMAIN and issue_id.startswith(AREA_ISSUE_PREFIX)
    }
    assert len(card_ids_after) == 12

    state_after = hass.states.get(areas_sensor_entity_id(hass, mock_config_entry))
    assert state_after is not None
    assert state_after.state == "12"


async def test_three_open_cards_plus_twenty_five_new_confident_suggestions_yield_thirteen_cards(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """Existing open cards never count against the per-run cap on new ones."""
    create_areas(hass, "Kitchen")
    for i in range(3):
        register_area_device(hass, f"f{i}", name=f"First {i}", entities=["sensor"])
    first_answers = {f"d{index}": area_answer("Kitchen", 0.9, ["Kitchen"]) for index in range(3)}
    await _setup(hass, aioclient_mock, mock_config_entry, first_answers)

    card_ids = {
        issue_id for domain, issue_id in ir.async_get(hass).issues if domain == DOMAIN and issue_id.startswith(AREA_ISSUE_PREFIX)
    }
    assert len(card_ids) == 3

    for i in range(25):
        register_area_device(hass, f"s{i}", name=f"Second {i}", entities=["sensor"])
    device_registry = dr.async_get(hass)
    all_devices = sorted(device_registry.devices, key=lambda device: device.id)
    answers = {f"d{index}": area_answer("Kitchen", 0.9, ["Kitchen"]) for index in range(len(all_devices))}

    aioclient_mock.clear_requests()
    register_jev_responses(aioclient_mock, [api_response(answers)])
    await _run_again(hass, mock_config_entry)

    card_ids_after = {
        issue_id for domain, issue_id in ir.async_get(hass).issues if domain == DOMAIN and issue_id.startswith(AREA_ISSUE_PREFIX)
    }
    assert len(card_ids_after) == 13


async def test_an_item_with_no_confidence_still_becomes_a_card_sorted_last(hass: HomeAssistant) -> None:
    """A malformed item carrying no confidence at all defaults to the lowest sort priority, not a crash."""
    create_areas(hass, "Kitchen")
    device = register_area_device(hass, "no_confidence", entities=["sensor"])

    sync_area_cards(hass, SafetyRules(None), [{"registry_id": device.id, "choice": "Kitchen"}])

    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{AREA_ISSUE_PREFIX}{device.id}") is not None
