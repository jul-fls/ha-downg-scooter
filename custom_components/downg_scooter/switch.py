"""Lock switch for DownG Scooter."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import DownGScooterCoordinator
from .entity import DownGScooterEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up scooter switches."""
    coordinator: DownGScooterCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([DownGScooterLockSwitch(coordinator)])


class DownGScooterLockSwitch(DownGScooterEntity, SwitchEntity):
    """Software lock switch."""

    _attr_name = "Software lock"
    _attr_translation_key = "software_lock"

    def __init__(self, coordinator: DownGScooterCoordinator) -> None:
        """Initialize switch."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_software_lock"
        self._attr_is_on = coordinator.data.locked

    def _handle_coordinator_update(self) -> None:
        """Refresh the cached lock state from coordinator data."""
        self._attr_is_on = self.coordinator.data.locked
        super()._handle_coordinator_update()

    async def async_turn_on(self, **kwargs: object) -> None:
        """Lock the scooter."""
        await self.coordinator.async_set_locked(True)

    async def async_turn_off(self, **kwargs: object) -> None:
        """Unlock the scooter."""
        await self.coordinator.async_set_locked(False)
