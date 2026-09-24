"""Shared fixtures for Gut Check tests."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from homeassistant.components.update import DATA_COMPONENT, UpdateEntityFeature
from homeassistant.const import CONF_API_KEY, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.gutcheck.const import (
    API_URL,
    DOMAIN,
    HEALTH_OPTIONS,
    OPTION_NONE,
    RECIPE_AREAS,
    RECIPE_HEALTH,
    RECIPE_UPDATES,
    UPDATE_CRITERIA,
    UPDATE_OPTIONS,
)
from custom_components.gutcheck.recipes.shapes import Item, RecipeResult

ALL_HEALTH_OPTIONS = (*HEALTH_OPTIONS, OPTION_NONE)

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(subdirectory: str, name: str) -> Any:
    """The parsed JSON fixture at fixtures/<subdirectory>/<name>."""
    return json.loads((FIXTURES / subdirectory / name).read_text(encoding="utf-8"))


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


def score_answer(level: int, confidence: float) -> dict[str, Any]:
    """Build a documented-shape score answer with a legend and probabilities over every level."""
    levels = range(len(UPDATE_OPTIONS))
    others = [other for other in levels if other != level]
    remaining = max(0.0, 1.0 - confidence)
    share = remaining / len(others) if others else 0.0
    probabilities = {str(lvl): (confidence if lvl == level else share) for lvl in levels}
    legend = {str(lvl): UPDATE_CRITERIA[lvl] for lvl in levels}
    return {"type": "score", "score": float(level), "legend": legend, "probabilities": probabilities, "confidence": confidence}


def area_answer(choice: str, confidence: float, options: Sequence[str]) -> dict[str, Any]:
    """Build a documented-shape choice answer over the given area options, plus none of these."""
    all_options = (*options, OPTION_NONE)
    others = [option for option in all_options if option != choice]
    remaining = max(0.0, 1.0 - confidence)
    share = remaining / len(others) if others else 0.0
    probabilities = {option: (confidence if option == choice else share) for option in all_options}
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


def register_jev_responses_by_question(aioclient_mock: AiohttpClientMocker, responses: dict[str, Any]) -> None:
    """Queue one response per request, routed by the first question id in its own payload.

    Two recipes' coordinators can each fire their first refresh concurrently,
    so the order their POSTs land in is not guaranteed; keying by question id
    removes that race instead of trusting call order. Each key is used at
    most once: an unknown or already-used question id fails loudly.
    Each value is either a JSON body (200) or an (status, body) pair.
    """
    remaining = {key: (value if isinstance(value, tuple) else (200, value)) for key, value in responses.items()}

    async def _side_effect(method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        """Pop the response queued for this request's first question id, or fail loudly."""
        question_id = next(iter(data["questions"]))
        if question_id not in remaining:
            raise AssertionError(f"no queued response for question {question_id!r} (unknown or already used)")
        status, body = remaining.pop(question_id)
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


def update_item(
    entity_id: str,
    registry_id: str,
    *,
    latest_version: str = "2.0.0",
    release_url: str | None = "https://example.com/release",
) -> Item:
    """One classified update subject, as a recipe result carries it."""
    return {
        "entity_id": entity_id,
        "registry_id": registry_id,
        "latest_version": latest_version,
        "release_url": release_url,
        "confidence": 0.9,
    }


def update_result(items: dict[str, list[Item]]) -> RecipeResult:
    """A recipe result holding just the given per-option items."""
    return {"last_run": "", "counts": {}, "items": items, "unsure": [], "last_payload": None}


def register_unavailable_entity(hass: HomeAssistant, unique_id: str = "unique_selectable") -> str:
    """Register one unavailable entity the health recipe can select, and return its id."""
    entry = er.async_get(hass).async_get_or_create("sensor", "test", unique_id)
    hass.states.async_set(entry.entity_id, STATE_UNAVAILABLE)
    return entry.entity_id


def register_pending_update(
    hass: HomeAssistant,
    unique_id: str = "unique_update",
    *,
    platform: str = "test",
    device_id: str | None = None,
    disabled_by: er.RegistryEntryDisabler | None = None,
    installed_version: str = "1.0.0",
    latest_version: str = "2.0.0",
    release_summary: str | None = "Bug fixes and performance improvements.",
    title: str | None = None,
    release_url: str | None = None,
    skipped_version: str | None = None,
) -> er.RegistryEntry:
    """Register one pending update entity the update recipe can select, with the given attributes."""
    entry = er.async_get(hass).async_get_or_create("update", platform, unique_id, device_id=device_id, disabled_by=disabled_by)
    hass.states.async_set(
        entry.entity_id,
        STATE_ON,
        {
            "installed_version": installed_version,
            "latest_version": latest_version,
            "release_summary": release_summary,
            "title": title,
            "release_url": release_url,
            "skipped_version": skipped_version,
        },
    )
    return entry


@dataclass
class FakeUpdateEntity:
    """A minimal stand-in for the real update platform entity the notes fetch looks up."""

    available: bool = True
    supported_features: UpdateEntityFeature = field(default_factory=lambda: UpdateEntityFeature.RELEASE_NOTES)
    notes: str | None = None
    raises: Exception | None = None
    hangs: bool = False

    async def async_release_notes(self) -> str | None:
        """Return the configured notes, raise the configured exception, or hang forever."""
        if self.hangs:
            await asyncio.Event().wait()
        if self.raises is not None:
            raise self.raises
        return self.notes


def install_update_entities(hass: HomeAssistant, entities: dict[str, FakeUpdateEntity]) -> None:
    """Install fake update platform entities for the release-notes fetch path to find.

    The update recipe looks entities up through hass.data[DATA_COMPONENT], the
    same in-process path HA's own websocket handler uses; standing up a full
    update platform just to exercise that lookup would be a much larger fixture
    for the same behavior.
    """
    hass.data[DATA_COMPONENT] = SimpleNamespace(get_entity=entities.get)


def find_health_sensor(hass: HomeAssistant, entry: MockConfigEntry) -> str | None:
    """The health recipe's sensor entity id, or None when the recipe is switched off."""
    return er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{RECIPE_HEALTH}")


def health_sensor_entity_id(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    """The health recipe's sensor entity id, for the tests where it must exist."""
    entity_id = find_health_sensor(hass, entry)
    assert entity_id is not None
    return entity_id


def find_updates_sensor(hass: HomeAssistant, entry: MockConfigEntry) -> str | None:
    """The update recipe's sensor entity id, or None when the recipe is switched off."""
    return er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{RECIPE_UPDATES}")


def updates_sensor_entity_id(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    """The update recipe's sensor entity id, for the tests where it must exist."""
    entity_id = find_updates_sensor(hass, entry)
    assert entity_id is not None
    return entity_id


def find_areas_sensor(hass: HomeAssistant, entry: MockConfigEntry) -> str | None:
    """The area recipe's sensor entity id, or None when the recipe is switched off."""
    return er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{RECIPE_AREAS}")


def areas_sensor_entity_id(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    """The area recipe's sensor entity id, for the tests where it must exist."""
    entity_id = find_areas_sensor(hass, entry)
    assert entity_id is not None
    return entity_id


def create_areas(hass: HomeAssistant, *names: str) -> dict[str, str]:
    """Create each named area and return its name mapped to its area id."""
    registry = ar.async_get(hass)
    return {name: registry.async_create(name).id for name in names}


@dataclass
class AreaEntitySpec:
    """One entity to attach to a test device, with just the fields the area recipe cares about."""

    domain: str
    device_class: str | None = None
    disabled_by: er.RegistryEntryDisabler | None = None
    area: str | None = None
    labels: frozenset[str] = frozenset()


def register_area_device(
    hass: HomeAssistant,
    unique: str,
    *,
    domain: str = "test",
    name: str | None = "Device",
    manufacturer: str | None = None,
    model: str | None = None,
    name_by_user: str | None = None,
    labels: frozenset[str] = frozenset(),
    area: str | None = None,
    disabled_by: dr.DeviceEntryDisabler | None = None,
    entry_type: dr.DeviceEntryType | None = None,
    entities: Sequence[AreaEntitySpec | str] = (),
) -> dr.DeviceEntry:
    """Register (or reuse) a device with the given fields and entities, for the area recipe to select.

    Each item in entities is either a bare domain string (a plain entity in
    that domain) or an AreaEntitySpec for one with its own device class,
    disabled state, area or labels.
    """
    config_entry = MockConfigEntry(domain=domain, unique_id=f"area_device_{domain}_{unique}")
    config_entry.add_to_hass(hass)
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(domain, unique)},
        name=name,
        manufacturer=manufacturer,
        model=model,
        disabled_by=disabled_by,
        entry_type=entry_type,
    )
    if name_by_user is not None:
        device_registry.async_update_device(device.id, name_by_user=name_by_user)
    if labels:
        device_registry.async_update_device(device.id, labels=set(labels))
    if area is not None:
        device_registry.async_update_device(device.id, area_id=area)

    entity_registry = er.async_get(hass)
    for index, spec in enumerate(entities):
        item = spec if isinstance(spec, AreaEntitySpec) else AreaEntitySpec(domain=spec)
        entry = entity_registry.async_get_or_create(
            item.domain,
            domain,
            f"{unique}_{index}",
            device_id=device.id,
            disabled_by=item.disabled_by,
            original_device_class=item.device_class,
        )
        if item.labels:
            entity_registry.async_update_entity(entry.entity_id, labels=set(item.labels))
        if item.area is not None:
            entity_registry.async_update_entity(entry.entity_id, area_id=item.area)

    resolved = device_registry.async_get(device.id)
    assert resolved is not None
    return resolved


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """A Gut Check config entry with a test API key."""
    return MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: "test-key"})
