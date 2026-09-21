"""Shared fixtures for Gut Check tests."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.const import CONF_API_KEY, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.gutcheck.const import API_URL, DOMAIN, HEALTH_OPTIONS, OPTION_NONE, RECIPE_HEALTH
from custom_components.gutcheck.recipes.base import Item, RecipeResult

ALL_HEALTH_OPTIONS = (*HEALTH_OPTIONS, OPTION_NONE)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(request: pytest.FixtureRequest) -> None:
    """Allow this custom integration to load during every test.

    Skipped when the test also uses recorder_mock: enable_custom_integrations
    pulls in hass directly, and being autouse it would otherwise win the race
    to create hass before recorder_mock's own chain sets up the recorder's
    database, which pytest-homeassistant-custom-component requires to happen
    first. No recorder test here sets up the integration through a config
    entry, so skipping this for them is safe.
    """
    if "recorder_mock" in request.fixturenames:
        return
    request.getfixturevalue("enable_custom_integrations")


def choice_answer(choice: str, confidence: float) -> dict[str, Any]:
    """Build a documented-shape choice answer with probabilities over every option."""
    others = [option for option in ALL_HEALTH_OPTIONS if option != choice]
    remaining = max(0.0, 1.0 - confidence)
    share = remaining / len(others) if others else 0.0
    probabilities = {option: (confidence if option == choice else share) for option in ALL_HEALTH_OPTIONS}
    return {"type": "choice", "choice": choice, "probabilities": probabilities, "confidence": confidence}


def register_jev_responses(aioclient_mock: AiohttpClientMocker, responses: list[Any]) -> None:
    """Queue a sequence of responses for POSTs to API_URL, in order, one per call.

    Each item is either a JSON body (200) or an (status, body) pair.
    """
    queue = [item if isinstance(item, tuple) else (200, item) for item in responses]
    registered = len(queue)

    async def _side_effect(method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        """Pop the next queued response, or fail loudly once the queue runs out."""
        if not queue:
            # Retries and reschedules can outrun the queue; say so here rather
            # than letting an IndexError surface from inside the mocker.
            raise AssertionError(f"the test registered {registered} API responses but a further call was made")
        status, body = queue.pop(0)
        return AiohttpClientMockResponse(method=method, url=url, status=status, json=body)

    aioclient_mock.post(API_URL, side_effect=_side_effect)


def posted_bodies(aioclient_mock: AiohttpClientMocker) -> list[dict[str, Any]]:
    """Return every request body posted to API_URL, in call order."""
    return [data for _method, url, data, _headers in aioclient_mock.mock_calls if str(url) == API_URL]


def api_response(answers: dict[str, Any], input_tokens: int = 10) -> dict[str, Any]:
    """One API response body wrapping the given answers and reported usage."""
    return {
        "model": "jev-latest",
        "answers": answers,
        "usage": {"input_tokens": input_tokens, "output_tokens": 0},
    }


def health_item(entity_id: str, registry_id: str, unavailable_for: str = "1 to 6 days") -> Item:
    """One classified subject, as a recipe result carries it."""
    return {
        "entity_id": entity_id,
        "registry_id": registry_id,
        "restored": False,
        "confidence": 0.9,
        "unavailable_for": unavailable_for,
    }


def health_result(items: dict[str, list[Item]]) -> RecipeResult:
    """A recipe result holding just the given per-option items."""
    return {"last_run": "", "counts": {}, "items": items, "unsure": [], "last_payload": None}


def register_unavailable_entity(hass: HomeAssistant, unique_id: str = "unique_selectable") -> str:
    """Register one unavailable entity the health recipe can select, and return its id."""
    entry = er.async_get(hass).async_get_or_create("sensor", "test", unique_id)
    hass.states.async_set(entry.entity_id, STATE_UNAVAILABLE)
    return entry.entity_id


def find_health_sensor(hass: HomeAssistant, entry: MockConfigEntry) -> str | None:
    """The health recipe's sensor entity id, or None when the recipe is switched off."""
    return er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{RECIPE_HEALTH}")


def health_sensor_entity_id(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    """The health recipe's sensor entity id, for the tests where it must exist."""
    entity_id = find_health_sensor(hass, entry)
    assert entity_id is not None
    return entity_id


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """A Gut Check config entry with a test API key."""
    return MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: "test-key"})
