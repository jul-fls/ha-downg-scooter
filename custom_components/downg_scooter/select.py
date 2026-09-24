"""Select entities for DownG Scooter."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import DownGScooterCoordinator
from .entity import DownGScooterEntity
from .protocol import KERS_MODES, ScooterData, TAIL_LIGHT_MODES


@dataclass(frozen=True, kw_only=True)
class DownGSelectDescription(SelectEntityDescription):
    """Describe a scooter setting."""

    value_fn: Callable[[ScooterData], str | None]
    command_fn: Callable[[DownGScooterCoordinator, str], Coroutine[Any, Any, None]]


SELECTS = (
    DownGSelectDescription(
        key="tail_light_mode",
        translation_key="tail_light_mode",
        options=list(TAIL_LIGHT_MODES),
        value_fn=lambda data: data.tail_light_mode,
        command_fn=lambda coordinator, option: coordinator.async_set_tail_light(option),
    ),
    DownGSelectDescription(
        key="kers_mode",
        translation_key="kers_mode",
        options=list(KERS_MODES),
        value_fn=lambda data: data.kers_mode,
        command_fn=lambda coordinator, option: coordinator.async_set_kers(option),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up scooter selects."""
    coordinator: DownGScooterCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(DownGScooterSelect(coordinator, description) for description in SELECTS)


class DownGScooterSelect(DownGScooterEntity, SelectEntity):
    """One writable scooter setting."""

    entity_description: DownGSelectDescription

    def __init__(
        self, coordinator: DownGScooterCoordinator, description: DownGSelectDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.address}_{description.key}"
        self._update_value()

    def _update_value(self) -> None:
        self._attr_current_option = self.entity_description.value_fn(self.coordinator.data)

    def _handle_coordinator_update(self) -> None:
        self._update_value()
        super()._handle_coordinator_update()

    async def async_select_option(self, option: str) -> None:
        await self.entity_description.command_fn(self.coordinator, option)
