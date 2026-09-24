"""Base entities for DownG Scooter."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import DownGScooterCoordinator


class DownGScooterEntity(CoordinatorEntity[DownGScooterCoordinator]):
    """Base DownG scooter entity."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: DownGScooterCoordinator) -> None:
        """Initialize entity."""
        super().__init__(coordinator)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            name=coordinator.name,
            manufacturer="Xiaomi",
        )
