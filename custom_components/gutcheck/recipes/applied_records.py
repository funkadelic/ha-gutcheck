"""The record of registry writes Gut Check made, and the change-back that undoes them."""

from __future__ import annotations

from collections.abc import Callable, Collection

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import SelectOptionDict
from homeassistant.helpers.storage import Store

from ..const import STORE_VERSION


class AppliedRecords:
    """The registry id to recorded value map for one kind of registry write Gut Check made, persisted under its own Store key."""

    def __init__(self, hass: HomeAssistant, key: str) -> None:
        """Hold the Store; async_load populates the in-memory map."""
        self._store: Store[dict[str, str]] = Store(hass, STORE_VERSION, key)
        self._data: dict[str, str] = {}

    async def async_load(self) -> None:
        """Load the persisted map; anything malformed is dropped rather than failing setup."""
        stored = await self._store.async_load()
        if isinstance(stored, dict):
            self._data = {
                registry_id: value
                for registry_id, value in stored.items()
                if isinstance(registry_id, str) and isinstance(value, str)
            }

    def get(self, registry_id: str) -> str | None:
        """The class Gut Check recorded for this registry id, or None."""
        return self._data.get(registry_id)

    def ids(self) -> tuple[str, ...]:
        """Every recorded registry id."""
        return tuple(self._data)

    @callback
    def record(self, registry_id: str, value: str) -> None:
        """Record a write and schedule the save; the fix flow's apply step is synchronous."""
        self._data[registry_id] = value
        self._store.async_delay_save(lambda: dict(self._data), 0)

    @callback
    def discard(self, registry_id: str) -> None:
        """Drop one id and schedule the save, from a synchronous listener."""
        self._data.pop(registry_id, None)
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


def undo_choices(hass: HomeAssistant, applied: AppliedRecords) -> list[SelectOptionDict]:
    """One choice per recorded sensor still registered, value its registry id, sorted by label."""
    registry = er.async_get(hass)
    choices: list[SelectOptionDict] = []
    for registry_id in applied.ids():
        entry = registry.async_get(registry_id)
        if entry is None:
            continue
        choices.append(SelectOptionDict(value=registry_id, label=_display_name(hass, entry)))
    return sorted(choices, key=lambda choice: choice["label"])


async def async_undo_records(
    applied: AppliedRecords,
    registry: er.EntityRegistry,
    registry_ids: Collection[str],
    *,
    still_ours: Callable[[er.RegistryEntry, str], bool],
    clear: Callable[[er.RegistryEntry], object],
    reject: Callable[[str, str], None],
) -> tuple[int, int]:
    """Clear each picked, recorded sensor still ours, reject it either way, then forget it; returns (cleared, left).

    Also drops every recorded id no longer registered, in the same save.
    """
    to_forget = {registry_id for registry_id in applied.ids() if registry.async_get(registry_id) is None}
    to_forget.update(registry_ids)
    cleared = 0
    left = 0
    for registry_id in registry_ids:
        recorded = applied.get(registry_id)
        registry_entry = registry.async_get(registry_id)
        if recorded is None or registry_entry is None or not still_ours(registry_entry, recorded):
            left += 1
        else:
            clear(registry_entry)
            cleared += 1
        # Reject any picked, recorded sensor whether or not this call is what
        # cleared it, so a hand-changed one is not re-suggested.
        if recorded is not None:
            reject(registry_id, recorded)
    await applied.async_forget(to_forget)
    return cleared, left
