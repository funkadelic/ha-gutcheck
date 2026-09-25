"""The record of every device class Gut Check sets, and the Configure change-back."""

from __future__ import annotations

import logging
from collections.abc import Collection

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import SelectOptionDict
from homeassistant.helpers.storage import Store

from ..const import CONF_CRITICAL_LABEL, DEVICE_CLASS_APPLIED_STORE_KEY, STORE_VERSION
from .device_class_cards import reject_suggestion
from .device_class_describe import class_names
from .safety import SafetyRules

_LOGGER = logging.getLogger(__name__)


class AppliedClasses:
    """The registry id to device class map Gut Check itself set, persisted across restarts."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Hold the Store; async_load populates the in-memory map."""
        self._store: Store[dict[str, str]] = Store(hass, STORE_VERSION, DEVICE_CLASS_APPLIED_STORE_KEY)
        self._data: dict[str, str] = {}

    async def async_load(self) -> None:
        """Load the persisted map; anything malformed is dropped rather than failing setup."""
        stored = await self._store.async_load()
        if isinstance(stored, dict):
            self._data = {
                registry_id: device_class
                for registry_id, device_class in stored.items()
                if isinstance(registry_id, str) and isinstance(device_class, str)
            }

    def get(self, registry_id: str) -> str | None:
        """The class Gut Check recorded for this registry id, or None."""
        return self._data.get(registry_id)

    @property
    def has_records(self) -> bool:
        """Whether anything is recorded."""
        return bool(self._data)

    def ids(self) -> tuple[str, ...]:
        """Every recorded registry id."""
        return tuple(self._data)

    @callback
    def record(self, registry_id: str, device_class: str) -> None:
        """Record a write and schedule the save; the fix flow's apply step is synchronous."""
        self._data[registry_id] = device_class
        self._store.async_delay_save(lambda: dict(self._data), 0)

    async def async_flush(self) -> None:
        """Await the current map's save, so a reload right after a confirm never loads an older record."""
        await self._store.async_save(dict(self._data))

    async def async_forget(self, registry_ids: Collection[str]) -> None:
        """Drop the given ids and await the save."""
        for registry_id in registry_ids:
            self._data.pop(registry_id, None)
        await self._store.async_save(dict(self._data))


def _display_name(hass: HomeAssistant, entry: er.RegistryEntry) -> str:
    """The state's friendly name when it has a state, else its registry name, original name or entity id."""
    state = hass.states.get(entry.entity_id)
    if state is not None:
        return str(state.name)
    return entry.name or entry.original_name or entry.entity_id


def undo_choices(hass: HomeAssistant, applied: AppliedClasses) -> list[SelectOptionDict]:
    """One choice per recorded sensor still registered, value its registry id, sorted by label."""
    registry = er.async_get(hass)
    choices: list[SelectOptionDict] = []
    for registry_id in applied.ids():
        entry = registry.async_get(registry_id)
        if entry is None:
            continue
        choices.append(SelectOptionDict(value=registry_id, label=_display_name(hass, entry)))
    return sorted(choices, key=lambda choice: choice["label"])


async def async_change_back(hass: HomeAssistant, entry: ConfigEntry, registry_ids: Collection[str]) -> None:
    """Clear a class Gut Check set for each picked sensor still holding it, record it as a rejection, then forget it.

    Also drops every recorded id no longer registered, in the same save.
    Takes the whole config entry, left untyped, since importing the
    integration's own GutCheckData here would close an import cycle through
    the integration's own package module.
    """
    applied = entry.runtime_data.applied
    registry = er.async_get(hass)
    safety = SafetyRules(entry.options.get(CONF_CRITICAL_LABEL))
    names = await class_names(hass)

    to_forget = {registry_id for registry_id in applied.ids() if registry.async_get(registry_id) is None}
    to_forget.update(registry_ids)
    cleared = 0
    left = 0
    for registry_id in registry_ids:
        recorded_class = applied.get(registry_id)
        registry_entry = registry.async_get(registry_id)
        if recorded_class is None or registry_entry is None or registry_entry.device_class != recorded_class:
            left += 1
        else:
            registry.async_update_entity(registry_entry.entity_id, device_class=None)
            cleared += 1
        # Reject any picked, recorded sensor whether or not this call is what
        # cleared it, so a hand-cleared or hand-changed one is not re-suggested.
        if recorded_class is not None:
            reject_suggestion(hass, safety, names, registry_id, recorded_class)
    await applied.async_forget(to_forget)
    _LOGGER.debug("device class change-back cleared=%s left=%s", cleared, left)
