"""Binary sensors for DownG Scooter."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import DownGScooterCoordinator
from .entity import DownGScooterEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up scooter binary sensors."""
    coordinator: DownGScooterCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([DownGScooterChargingSensor(coordinator)])


class DownGScooterChargingSensor(DownGScooterEntity, BinarySensorEntity):
    """Report the explicit BMS charging flag."""

    _attr_translation_key = "charging"
    _attr_device_class = BinarySensorDeviceClass.BATTERY_CHARGING

    def __init__(self, coordinator: DownGScooterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_charging"
        self._update_value()

    def _update_value(self) -> None:
        self._attr_is_on = self.coordinator.data.battery_charging

    def _handle_coordinator_update(self) -> None:
        self._update_value()
        super()._handle_coordinator_update()
