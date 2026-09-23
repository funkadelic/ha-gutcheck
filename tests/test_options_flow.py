"""Tests for the options flow: health toggle, daily budget, and critical label."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import label_registry as lr
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gutcheck.const import (
    CONF_CRITICAL_LABEL,
    CONF_DAILY_BUDGET,
    CONF_HEALTH_ENABLED,
    CONF_UPDATES_ENABLED,
    DEFAULT_DAILY_BUDGET,
    DOMAIN,
    HEALTH_ISSUE_PREFIX,
    OPTION_EXPECTED,
    OPTION_WORTH_FIXING,
    RECIPE_UPDATES,
    UPDATES_ISSUE_PREFIX,
)

from .conftest import (
    api_response,
    choice_answer,
    find_health_sensor,
    find_updates_sensor,
    posted_bodies,
    register_jev_responses,
    register_jev_responses_by_question,
    register_pending_update,
    register_unavailable_entity,
    score_answer,
)


def _updates_button_entity_id(hass: HomeAssistant, entry: MockConfigEntry) -> str | None:
    """The update recipe's Run button entity id, or None if it was not created."""
    return er.async_get(hass).async_get_entity_id("button", DOMAIN, f"{entry.entry_id}_{RECIPE_UPDATES}_run")


def _tokens_sensor_state(hass: HomeAssistant, entry: MockConfigEntry) -> Any:
    """The tokens_today usage sensor's current state."""
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_tokens_today")
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    return state


def _has_update_issue(hass: HomeAssistant) -> bool:
    """Whether any possibly-breaking update Repairs card exists."""
    return any(domain == DOMAIN and issue_id.startswith(UPDATES_ISSUE_PREFIX) for domain, issue_id in ir.async_get(hass).issues)


def _schema_defaults(schema: Any) -> dict[str, Any]:
    """The default value of every field in a voluptuous options-flow schema."""
    return {str(key): key.default() for key in schema.schema if hasattr(key, "default") and callable(key.default)}


async def test_defaults_apply_when_options_never_saved(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """The init form's defaults are health on, the default budget, and no label."""
    register_unavailable_entity(hass)
    register_jev_responses(aioclient_mock, [api_response({"e0": choice_answer(OPTION_EXPECTED, 0.9)})])
    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    defaults = _schema_defaults(result["data_schema"])
    assert defaults[CONF_HEALTH_ENABLED] is True
    assert defaults[CONF_DAILY_BUDGET] == DEFAULT_DAILY_BUDGET
    assert CONF_CRITICAL_LABEL not in defaults

    assert find_health_sensor(hass, mock_config_entry) is not None
    assert _tokens_sensor_state(hass, mock_config_entry).attributes["daily_budget"] == DEFAULT_DAILY_BUDGET


async def test_saving_new_budget_reloads_and_keeps_spent_tokens(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """A new budget saves, reloads the entry, and today's spent tokens survive the reload."""
    register_unavailable_entity(hass)
    register_jev_responses(
        aioclient_mock,
        [api_response({"e0": choice_answer(OPTION_EXPECTED, 0.9)}, input_tokens=1234)],
    )
    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _tokens_sensor_state(hass, mock_config_entry).state == "1234"

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    with patch.object(
        hass.config_entries, "async_schedule_reload", wraps=hass.config_entries.async_schedule_reload
    ) as reload_spy:
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_HEALTH_ENABLED: True, CONF_DAILY_BUDGET: 5000}
        )
        await hass.async_block_till_done(wait_background_tasks=True)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    reload_spy.assert_called_once()

    # The reload restores the run from less than a week ago for free (D-11)
    # instead of sending another request, so only the first run's 1234 tokens
    # are spent.
    tokens_state = _tokens_sensor_state(hass, mock_config_entry)
    assert tokens_state.state == "1234"
    assert tokens_state.attributes["daily_budget"] == 5000
    assert tokens_state.attributes["remaining"] == 5000 - 1234


async def test_saving_identical_values_does_not_reload(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> None:
    """Resubmitting the same options is a no-op: no reload is scheduled."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    # Establish explicit options first; a bare entry starts with none at all,
    # so the very first save would always look like a change.
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_HEALTH_ENABLED: True, CONF_DAILY_BUDGET: DEFAULT_DAILY_BUDGET}
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    with patch.object(
        hass.config_entries, "async_schedule_reload", wraps=hass.config_entries.async_schedule_reload
    ) as reload_spy:
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_HEALTH_ENABLED: True, CONF_DAILY_BUDGET: DEFAULT_DAILY_BUDGET}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    reload_spy.assert_not_called()


async def test_turning_health_off_removes_entity_and_issues_and_sends_nothing(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """Disabling the health check removes its sensor and issues; usage sensors and budget stay."""
    register_unavailable_entity(hass)
    register_jev_responses(aioclient_mock, [api_response({"e0": choice_answer(OPTION_WORTH_FIXING, 0.9)})])
    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert find_health_sensor(hass, mock_config_entry) is not None
    issue_registry = ir.async_get(hass)
    assert any(domain == DOMAIN and issue_id.startswith("unavailable_") for domain, issue_id in issue_registry.issues)
    posted_before = len(posted_bodies(aioclient_mock))

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_HEALTH_ENABLED: False, CONF_DAILY_BUDGET: DEFAULT_DAILY_BUDGET}
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert find_health_sensor(hass, mock_config_entry) is None
    assert not any(domain == DOMAIN and issue_id.startswith("unavailable_") for domain, issue_id in issue_registry.issues)
    assert len(posted_bodies(aioclient_mock)) == posted_before

    assert _tokens_sensor_state(hass, mock_config_entry) is not None


async def test_turning_health_back_on_restores_sensor_and_runs(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """Re-enabling the health check restores its sensor and runs again once HA has started."""
    register_unavailable_entity(hass)
    register_jev_responses(
        aioclient_mock,
        [
            api_response({"e0": choice_answer(OPTION_EXPECTED, 0.9)}),
            api_response({"e0": choice_answer(OPTION_EXPECTED, 0.9)}),
        ],
    )
    mock_config_entry = MockConfigEntry(domain=DOMAIN, data=mock_config_entry.data, options={CONF_HEALTH_ENABLED: False})
    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert find_health_sensor(hass, mock_config_entry) is None
    assert len(posted_bodies(aioclient_mock)) == 0

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_HEALTH_ENABLED: True, CONF_DAILY_BUDGET: DEFAULT_DAILY_BUDGET}
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert find_health_sensor(hass, mock_config_entry) is not None
    assert len(posted_bodies(aioclient_mock)) == 1


async def test_reenabling_within_the_week_restores_the_worth_fixing_issue(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """Disable then re-enable within the cadence window: the free restore must recreate the Repairs card too."""
    entity_id = register_unavailable_entity(hass)
    registry_id = er.async_get(hass).async_get(entity_id).id
    issue_id = f"{HEALTH_ISSUE_PREFIX}{registry_id}"
    register_jev_responses(aioclient_mock, [api_response({"e0": choice_answer(OPTION_WORTH_FIXING, 0.9)})])
    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_HEALTH_ENABLED: False, CONF_DAILY_BUDGET: DEFAULT_DAILY_BUDGET}
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None

    posted_before = len(posted_bodies(aioclient_mock))
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_HEALTH_ENABLED: True, CONF_DAILY_BUDGET: DEFAULT_DAILY_BUDGET}
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    # Still within the 7-day cadence window: restored for free, no new API call.
    assert len(posted_bodies(aioclient_mock)) == posted_before
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None


async def test_critical_label_excludes_and_clearing_restores(
    hass: HomeAssistant, freezer: Any, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """Picking a critical label excludes the labelled entity; clearing it brings it back.

    Each reload jumps the clock past the weekly cadence window first, so the
    restore-for-free path (D-11) doesn't hide the label's effect behind a
    restored, pre-label result.
    """
    freezer.move_to("2026-01-01T00:00:00-08:00")
    label_registry = lr.async_get(hass)
    label = label_registry.async_create("Critical")

    labelled_entity_id = register_unavailable_entity(hass, "unique_labelled")
    registry = er.async_get(hass)
    registry.async_update_entity(labelled_entity_id, labels={label.label_id})
    register_unavailable_entity(hass, "unique_other")

    register_jev_responses(
        aioclient_mock,
        [
            api_response({"e0": choice_answer(OPTION_EXPECTED, 0.9)}),  # first run: no label yet, both selected
            api_response({"e0": choice_answer(OPTION_EXPECTED, 0.9)}),  # after picking the label: only "other"
            api_response(
                {"e0": choice_answer(OPTION_EXPECTED, 0.9), "e1": choice_answer(OPTION_EXPECTED, 0.9)}
            ),  # after clearing it: both again
        ],
    )
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    freezer.move_to("2026-01-09T00:00:00-08:00")
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_HEALTH_ENABLED: True, CONF_DAILY_BUDGET: DEFAULT_DAILY_BUDGET, CONF_CRITICAL_LABEL: label.label_id},
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    # State never carries entity_id (SAFE-05), so the label's effect on
    # selection shows up as the count of entities in the payload: with the
    # label picked, only the unlabelled entity is sent.
    bodies = posted_bodies(aioclient_mock)
    assert len(bodies) == 2
    assert len(bodies[0]["state"]["entities"]) == 2
    assert len(bodies[1]["state"]["entities"]) == 1
    assert "entity_id" not in bodies[1]["state"]["entities"][0]

    freezer.move_to("2026-01-17T00:00:00-08:00")
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_HEALTH_ENABLED: True, CONF_DAILY_BUDGET: DEFAULT_DAILY_BUDGET}
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    bodies = posted_bodies(aioclient_mock)
    assert len(bodies) == 3
    assert len(bodies[2]["state"]["entities"]) == 2


async def test_budget_of_zero_is_rejected_by_the_form_schema(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> None:
    """The schema rejects a budget of 0 before the flow handler ever sees it."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(result["flow_id"], {CONF_HEALTH_ENABLED: True, CONF_DAILY_BUDGET: 0})


async def test_turning_updates_off_removes_its_sensor_and_button_leaving_health_alone(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """Disabling the update review removes its sensor, button and cards; the health check stays untouched."""
    register_unavailable_entity(hass)
    register_pending_update(hass)
    register_jev_responses_by_question(
        aioclient_mock,
        {"e0": api_response({"e0": choice_answer(OPTION_EXPECTED, 0.9)}), "u0": api_response({"u0": score_answer(2, 0.9)})},
    )
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert find_health_sensor(hass, mock_config_entry) is not None
    assert find_updates_sensor(hass, mock_config_entry) is not None
    assert _updates_button_entity_id(hass, mock_config_entry) is not None
    assert _has_update_issue(hass)

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_HEALTH_ENABLED: True, CONF_UPDATES_ENABLED: False, CONF_DAILY_BUDGET: DEFAULT_DAILY_BUDGET},
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert find_health_sensor(hass, mock_config_entry) is not None
    assert find_updates_sensor(hass, mock_config_entry) is None
    assert _updates_button_entity_id(hass, mock_config_entry) is None
    assert not _has_update_issue(hass)


async def test_turning_updates_back_on_restores_sensor_and_runs(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """Re-enabling the update review restores its sensor, runs again once HA has started, and raises its cards."""
    register_pending_update(hass)
    register_jev_responses(aioclient_mock, [api_response({"u0": score_answer(2, 0.9)})])
    mock_config_entry = MockConfigEntry(
        domain=DOMAIN, data=mock_config_entry.data, options={CONF_HEALTH_ENABLED: False, CONF_UPDATES_ENABLED: False}
    )
    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert find_updates_sensor(hass, mock_config_entry) is None
    assert len(posted_bodies(aioclient_mock)) == 0

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_HEALTH_ENABLED: False, CONF_UPDATES_ENABLED: True, CONF_DAILY_BUDGET: DEFAULT_DAILY_BUDGET},
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert find_updates_sensor(hass, mock_config_entry) is not None
    assert len(posted_bodies(aioclient_mock)) == 1
    assert _has_update_issue(hass)


async def test_saving_identical_updates_value_does_not_reload(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> None:
    """Resubmitting the same updates-enabled value alongside the rest is a no-op: no reload is scheduled."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_HEALTH_ENABLED: True, CONF_UPDATES_ENABLED: True, CONF_DAILY_BUDGET: DEFAULT_DAILY_BUDGET},
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    with patch.object(
        hass.config_entries, "async_schedule_reload", wraps=hass.config_entries.async_schedule_reload
    ) as reload_spy:
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {CONF_HEALTH_ENABLED: True, CONF_UPDATES_ENABLED: True, CONF_DAILY_BUDGET: DEFAULT_DAILY_BUDGET},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    reload_spy.assert_not_called()
