"""Coordinator for DownG Scooter."""

from __future__ import annotations

from datetime import timedelta
import logging

from bleak.exc import BleakError

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_ADDRESS, DEFAULT_SCAN_INTERVAL, DOMAIN
from .protocol import DownGScooterClient, ScooterData, ScooterProtocolError

_LOGGER = logging.getLogger(__name__)


class DownGScooterCoordinator(DataUpdateCoordinator[ScooterData]):
    """Fetch scooter telemetry and expose command helpers."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize coordinator."""
        self.entry = entry
        self.name = entry.data[CONF_NAME]
        self.address = entry.data[CONF_ADDRESS]
        self.client = DownGScooterClient(self.address)

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )

    async def _async_update_data(self) -> ScooterData:
        """Poll scooter data."""
        try:
            self._resolve_ble_device()
            return await self.client.read_telemetry()
        except (BleakError, ScooterProtocolError) as err:
            raise UpdateFailed(str(err)) from err
        finally:
            await self.client.disconnect()

    async def async_set_locked(self, locked: bool) -> None:
        """Set scooter software lock state."""
        self._resolve_ble_device()
        try:
            await self.client.set_locked(locked)
        finally:
            await self.client.disconnect()
        await self.async_request_refresh()

    def _resolve_ble_device(self) -> None:
        """Select the nearest connectable HA Bluetooth adapter or proxy."""
        device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if device is None:
            raise ScooterProtocolError("Scooter is not reachable over Bluetooth")
        self.client.set_device(device)

    async def async_shutdown(self) -> None:
        """Close BLE resources."""
        await self.client.disconnect()
