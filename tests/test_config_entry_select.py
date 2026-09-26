"""Selection, ordering, the reauth skip, alignment, failing-time carry, and the gate edges."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import SOURCE_IGNORE, ConfigEntryDisabler, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockModule, mock_integration, mock_platform
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import CONF_CRITICAL_LABEL, DOMAIN, OPTION_NONE, RECIPE_CONFIG_ENTRIES
from custom_components.gutcheck.recipes.config_entries import ConfigEntryRecipe
from custom_components.gutcheck.recipes.config_entry_const import OPTION_DEAD, OPTION_NEEDS_REAUTH, OPTION_TRANSIENT
from custom_components.gutcheck.recipes.gate import classify
from custom_components.gutcheck.recipes.shapes import Batch, RecipeResult

from .conftest import api_response, area_answer, posted_bodies, register_jev_responses, triage_sensor_entity_id

_OPTIONS = (OPTION_TRANSIENT, OPTION_NEEDS_REAUTH, OPTION_DEAD)


def _triage_button_entity_id(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    """The stuck integration check's Run button entity id."""
    entity_id = er.async_get(hass).async_get_entity_id("button", DOMAIN, f"{entry.entry_id}_{RECIPE_CONFIG_ENTRIES}_run")
    assert entity_id is not None
    return entity_id


async def _press_run(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Press the stuck integration check's Run button and let its background run finish."""
    await hass.services.async_call("button", "press", {"entity_id": _triage_button_entity_id(hass, entry)}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)


def _reauth_flow_id(hass: HomeAssistant, domain: str) -> str:
    """The flow id of domain's single in-progress reauth flow."""
    flows = [
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["handler"] == domain and flow["context"]["source"] == "reauth"
    ]
    assert len(flows) == 1
    return str(flows[0]["flow_id"])


async def test_only_setup_retry_and_setup_error_entries_are_selected(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    triage_entry: MockConfigEntry,
    failing_entry,
) -> None:
    """A loaded, ignored, disabled or migration_error entry never appears in any bucket or unsure."""
    stuck = await failing_entry("gone_service", ConfigEntryError("account closed"), title="Gone", entry_id="gone_entry")
    await failing_entry("healthy_hub", None, title="Healthy", entry_id="healthy_entry")

    ignored = MockConfigEntry(domain="skipped_hub", title="Ignored", entry_id="ignored_entry", source=SOURCE_IGNORE)
    ignored.add_to_hass(hass)
    disabled = MockConfigEntry(
        domain="off_hub", title="Disabled", entry_id="disabled_entry", disabled_by=ConfigEntryDisabler.USER
    )
    disabled.add_to_hass(hass)

    mock_integration(hass, MockModule(domain="no_flow_hub"))
    mock_platform(hass, "no_flow_hub.config_flow", None)
    migration = MockConfigEntry(domain="no_flow_hub", title="No Flow", entry_id="migration_entry", version=2)
    migration.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(migration.entry_id)
    assert migration.state is ConfigEntryState.MIGRATION_ERROR

    register_jev_responses(aioclient_mock, [api_response({"c0": area_answer(OPTION_DEAD, 0.9, _OPTIONS)})])
    triage_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(triage_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    bodies = posted_bodies(aioclient_mock)
    assert len(bodies) == 1
    assert len(bodies[0]["state"]["entries"]) == 1
    assert bodies[0]["state"]["entries"][0]["integration"] == "gone_service"

    state = hass.states.get(triage_sensor_entity_id(hass, triage_entry))
    assert state is not None
    all_items = [item for bucket in state.attributes["items"].values() for item in bucket] + state.attributes["unsure"]
    assert {item["entry_id"] for item in all_items} == {stuck.entry_id}


async def test_critical_labelled_entry_is_still_asked(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    triage_entry: MockConfigEntry,
    failing_entry,
) -> None:
    """An integration owning a critical-labelled device with a lock entity is still checked."""
    triage_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(triage_entry, options={**triage_entry.options, CONF_CRITICAL_LABEL: "critical"})

    entry = await failing_entry("locked_hub", ConfigEntryError("device offline"), title="Locked Hub", entry_id="locked_entry")
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("locked_hub", "lock1")}, name="Front Door"
    )
    device_registry.async_update_device(device.id, labels={"critical"})
    entity_registry = er.async_get(hass)
    entity_registry.async_get_or_create("lock", "locked_hub", "lock1", device_id=device.id)

    register_jev_responses(aioclient_mock, [api_response({"c0": area_answer(OPTION_TRANSIENT, 0.9, _OPTIONS)})])
    assert await hass.config_entries.async_setup(triage_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    bodies = posted_bodies(aioclient_mock)
    assert len(bodies) == 1
    assert bodies[0]["state"]["entries"][0]["integration"] == "locked_hub"


async def test_entries_asked_in_entry_id_order_and_stable_across_runs(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    triage_entry: MockConfigEntry,
    failing_entry,
) -> None:
    """Two stuck entries are asked in entry_id order, and unanswered runs post the same body twice."""
    await failing_entry("zeta_hub", ConfigEntryError("offline"), title="Zeta", entry_id="zzz_entry")
    await failing_entry("alpha_hub", ConfigEntryError("offline"), title="Alpha", entry_id="aaa_entry")

    register_jev_responses(
        aioclient_mock,
        [
            api_response(
                {
                    "c0": area_answer(OPTION_NONE, 0.9, _OPTIONS),
                    "c1": area_answer(OPTION_NONE, 0.9, _OPTIONS),
                }
            )
        ],
    )
    triage_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(triage_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    bodies = posted_bodies(aioclient_mock)
    assert [item["integration"] for item in bodies[0]["state"]["entries"]] == ["alpha_hub", "zeta_hub"]


async def test_reauth_active_entry_is_skipped_and_counted_with_no_gutcheck_card(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    triage_entry: MockConfigEntry,
    failing_entry,
) -> None:
    """A reauth-active entry gets no question and no Gut Check card, but is counted in needs_reauth."""
    entry = await failing_entry(
        "cloud_hub", ConfigEntryAuthFailed("invalid credentials"), title="Cloud Hub", entry_id="cloud_entry", reauth=True
    )

    registry = ir.async_get(hass)
    reauth_issue_id = f"config_entry_reauth_{entry.domain}_{entry.entry_id}"
    assert registry.async_get_issue("homeassistant", reauth_issue_id) is not None

    triage_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(triage_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert posted_bodies(aioclient_mock) == []
    state = hass.states.get(triage_sensor_entity_id(hass, triage_entry))
    assert state is not None
    assert state.state == "0"
    assert state.attributes["counts"][OPTION_NEEDS_REAUTH] == 1
    item = state.attributes["items"][OPTION_NEEDS_REAUTH][0]
    assert item["reauth_in_progress"] is True
    assert item["confidence"] == 1.0
    assert registry.async_get_issue(DOMAIN, f"config_entry_{entry.entry_id}") is None


async def test_entry_is_asked_and_carded_after_its_reauth_flow_is_aborted(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    triage_entry: MockConfigEntry,
    failing_entry,
) -> None:
    """Once HA's reauth flow ends, the next run asks about the entry and a confident answer cards it."""
    entry = await failing_entry(
        "cloud_hub2", ConfigEntryAuthFailed("invalid credentials"), title="Cloud Hub", entry_id="cloud_entry2", reauth=True
    )
    triage_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(triage_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    flow_id = _reauth_flow_id(hass, "cloud_hub2")
    hass.config_entries.flow.async_abort(flow_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    register_jev_responses(aioclient_mock, [api_response({"c0": area_answer(OPTION_NEEDS_REAUTH, 0.9, _OPTIONS)})])
    await _press_run(hass, triage_entry)

    assert len(posted_bodies(aioclient_mock)) == 1
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, f"config_entry_{entry.entry_id}") is not None


async def test_auth_failure_with_no_reauth_step_is_asked_like_any_other_entry(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    triage_entry: MockConfigEntry,
    failing_entry,
) -> None:
    """An integration with no reauth step is asked about, never carried straight into needs_reauth."""
    await failing_entry(
        "no_reauth_hub", ConfigEntryAuthFailed("invalid credentials"), title="No Reauth", entry_id="no_reauth_entry"
    )

    register_jev_responses(aioclient_mock, [api_response({"c0": area_answer(OPTION_NEEDS_REAUTH, 0.9, _OPTIONS)})])
    triage_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(triage_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(posted_bodies(aioclient_mock)) == 1


async def test_reauth_skipped_entry_does_not_shift_question_alignment(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    triage_entry: MockConfigEntry,
    failing_entry,
) -> None:
    """A reauth-skipped entry sorting between two asked ones leaves c0/c1 describing the asked entries only."""
    await failing_entry("aaa_hub", ConfigEntryError("offline"), title="A", entry_id="aaa_entry")
    await failing_entry("bbb_hub", ConfigEntryAuthFailed("invalid credentials"), title="B", entry_id="bbb_entry", reauth=True)
    await failing_entry("ccc_hub", ConfigEntryError("offline"), title="C", entry_id="ccc_entry")

    register_jev_responses(
        aioclient_mock,
        [
            api_response(
                {
                    "c0": area_answer(OPTION_NONE, 0.9, _OPTIONS),
                    "c1": area_answer(OPTION_NONE, 0.9, _OPTIONS),
                }
            )
        ],
    )
    triage_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(triage_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    body = posted_bodies(aioclient_mock)[0]
    assert set(body["questions"]) == {"c0", "c1"}
    assert [item["integration"] for item in body["state"]["entries"]] == ["aaa_hub", "ccc_hub"]
    assert body["questions"]["c1"]["instructions"].startswith("`entries[1]`")


async def test_failing_for_carries_first_seen_and_buckets_by_elapsed_days(
    hass: HomeAssistant,
    freezer: Any,
    aioclient_mock: AiohttpClientMocker,
    triage_entry: MockConfigEntry,
    failing_entry,
) -> None:
    """failing_for starts unknown, then buckets from the same first_seen as time passes."""
    freezer.move_to("2026-01-01T00:00:00-08:00")
    await failing_entry("slow_hub", ConfigEntryError("timed out"), title="Slow Hub", entry_id="slow_entry")

    register_jev_responses(aioclient_mock, [api_response({"c0": area_answer(OPTION_NONE, 0.9, _OPTIONS)})])
    triage_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(triage_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    first_body = posted_bodies(aioclient_mock)[0]
    assert first_body["state"]["entries"][0]["failing_for"] == "unknown"

    freezer.move_to("2026-01-09T00:00:01-08:00")  # +8 days
    register_jev_responses(aioclient_mock, [api_response({"c0": area_answer(OPTION_NONE, 0.9, _OPTIONS)})])
    await _press_run(hass, triage_entry)
    second_body = posted_bodies(aioclient_mock)[1]
    assert second_body["state"]["entries"][0]["failing_for"] == "longer than 1 week"

    freezer.move_to("2026-01-30T00:00:01-08:00")  # +29 days from first sight
    register_jev_responses(aioclient_mock, [api_response({"c0": area_answer(OPTION_NONE, 0.9, _OPTIONS)})])
    await _press_run(hass, triage_entry)
    third_body = posted_bodies(aioclient_mock)[2]
    assert third_body["state"]["entries"][0]["failing_for"] == "longer than 4 weeks"


async def test_restart_within_the_interval_restores_and_resyncs_cards_without_posting(
    hass: HomeAssistant,
    freezer: Any,
    aioclient_mock: AiohttpClientMocker,
    triage_entry: MockConfigEntry,
    failing_entry,
) -> None:
    """A restart within the weekly interval restores the stored result and re-syncs its card with no POST."""
    freezer.move_to("2026-01-01T00:00:00-08:00")
    entry = await failing_entry("restore_hub", ConfigEntryError("device offline"), title="Restore Hub", entry_id="restore_entry")
    register_jev_responses(aioclient_mock, [api_response({"c0": area_answer(OPTION_DEAD, 0.9, _OPTIONS)})])
    triage_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(triage_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(posted_bodies(aioclient_mock)) == 1

    freezer.move_to("2026-01-03T00:00:00-08:00")
    assert await hass.config_entries.async_unload(triage_entry.entry_id)
    assert await hass.config_entries.async_setup(triage_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(posted_bodies(aioclient_mock)) == 1
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, f"config_entry_{entry.entry_id}") is not None


def test_first_seen_reads_a_missing_or_unparseable_value_as_now() -> None:
    """A prior item with no first_seen, a non-string one, or an unparseable string reads as first seen now."""
    from custom_components.gutcheck.recipes.config_entry_describe import first_seen

    now = dt_util.utcnow()
    for bad_value in (None, 42, "not-a-date"):
        previous: RecipeResult = {
            "last_run": "",
            "counts": {},
            "items": {OPTION_TRANSIENT: [{"entry_id": "e1", "first_seen": bad_value}]},
            "unsure": [],
            "last_payload": None,
        }
        assert first_seen(previous, "e1", now) == now

    absent_previous: RecipeResult = {
        "last_run": "",
        "counts": {},
        "items": {OPTION_TRANSIENT: [{"entry_id": "some_other_entry", "first_seen": now.isoformat()}]},
        "unsure": [],
        "last_payload": None,
    }
    assert first_seen(absent_previous, "e1", now) == now


def test_gate_edges_for_threshold_none_of_these_and_off_criteria() -> None:
    """Exactly-at-threshold is accepted; just below, a confident none, and an off-criteria choice are unsure."""
    recipe = ConfigEntryRecipe()
    batch = Batch(
        state={"entries": [{}]},
        questions={"c0": {"type": "choice", "instructions": "x", "criteria": {**dict.fromkeys(_OPTIONS), OPTION_NONE: None}}},
        subjects={"c0": {"entry_id": "e1"}},
    )

    def _classify(answer: dict[str, Any]) -> RecipeResult:
        """Classify one c0 answer against the shared batch."""
        return classify(batch, api_response({"c0": answer}), _OPTIONS, None, recipe.gate)

    at_threshold = _classify(area_answer(OPTION_NEEDS_REAUTH, 0.5, _OPTIONS))
    assert at_threshold["counts"][OPTION_NEEDS_REAUTH] == 1

    below_threshold = _classify(area_answer(OPTION_NEEDS_REAUTH, 0.49, _OPTIONS))
    assert sum(below_threshold["counts"].values()) == 0
    assert len(below_threshold["unsure"]) == 1

    confident_none = _classify(area_answer(OPTION_NONE, 0.9, _OPTIONS))
    assert sum(confident_none["counts"].values()) == 0
    assert len(confident_none["unsure"]) == 1

    off_criteria = _classify({"type": "choice", "choice": "not_a_real_option", "probabilities": {}, "confidence": 0.9})
    assert sum(off_criteria["counts"].values()) == 0
    assert "choice" not in off_criteria["unsure"][0]
