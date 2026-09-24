"""Buttons for DownG Scooter."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import DownGScooterCoordinator
from .entity import DownGScooterEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up scooter buttons."""
    coordinator: DownGScooterCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([DownGScooterFlashTailLightButton(coordinator)])


class DownGScooterFlashTailLightButton(DownGScooterEntity, ButtonEntity):
    """Flash the rear light three times."""

    _attr_translation_key = "flash_tail_light"
    _attr_icon = "mdi:car-light-alert"

    def __init__(self, coordinator: DownGScooterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_flash_tail_light"

    async def async_press(self) -> None:
        await self.coordinator.async_flash_tail_light()
