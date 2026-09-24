"""Coordinator for DownG Scooter."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta
import logging

from bleak.exc import BleakError

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_ADDRESS,
    CONF_PROTOCOL,
    CONF_TOKEN,
    CONNECTED_POLL_INTERVAL,
    DISCONNECTED_RETRY_INTERVAL,
    DOMAIN,
    PROTOCOL_PLAIN,
)
from .protocol import (
    DownGScooterClient,
    ScooterAuthenticationError,
    ScooterData,
    ScooterProtocolError,
)

_LOGGER = logging.getLogger(__name__)


class DownGScooterCoordinator(DataUpdateCoordinator[ScooterData]):
    """Fetch scooter telemetry and serialize write commands."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        self.name = entry.data[CONF_NAME]
        self.address = entry.data[CONF_ADDRESS]
        self.protocol = entry.data.get(CONF_PROTOCOL, PROTOCOL_PLAIN)
        token_hex = entry.data.get(CONF_TOKEN)
        token = bytes.fromhex(token_hex) if token_hex else None
        self.client = DownGScooterClient(
            self.address,
            scooter_name=self.name,
            protocol=self.protocol,
            token=token,
        )
        self._operation_lock = asyncio.Lock()

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=CONNECTED_POLL_INTERVAL),
        )

    async def _async_update_data(self) -> ScooterData:
        """Poll the scooter through the closest HA adapter or proxy."""
        async with self._operation_lock:
            try:
                if not self.client.is_connected:
                    self._resolve_ble_device()
                data = await self.client.read_telemetry()
                self.update_interval = timedelta(seconds=CONNECTED_POLL_INTERVAL)
                return data
            except ScooterAuthenticationError as err:
                await self.client.disconnect()
                raise ConfigEntryAuthFailed(str(err)) from err
            except (BleakError, ScooterProtocolError) as err:
                self.update_interval = timedelta(
                    seconds=DISCONNECTED_RETRY_INTERVAL
                )
                await self.client.disconnect()
                raise UpdateFailed(str(err)) from err

    async def async_set_locked(self, locked: bool) -> None:
        """Set the software lock."""
        await self._async_command(lambda: self.client.set_locked(locked))

    async def async_set_cruise(self, enabled: bool) -> None:
        """Set cruise control."""
        await self._async_command(lambda: self.client.set_cruise(enabled))

    async def async_set_tail_light(self, mode: str) -> None:
        """Set rear-light behavior."""
        await self._async_command(lambda: self.client.set_tail_light(mode))

    async def async_set_kers(self, mode: str) -> None:
        """Set regenerative braking strength."""
        await self._async_command(lambda: self.client.set_kers(mode))

    async def async_flash_tail_light(self) -> None:
        """Flash the rear light three times and restore its mode."""
        mode = self.data.tail_light_mode
        if mode is None:
            raise HomeAssistantError("The current tail-light mode is unavailable")
        await self._async_command(lambda: self.client.flash_tail_light(mode))

    async def _async_command(self, command: Callable[[], Awaitable[None]]) -> None:
        async with self._operation_lock:
            try:
                if not self.client.is_connected:
                    self._resolve_ble_device()
                await command()
                await asyncio.sleep(0.2)
            except ScooterAuthenticationError as err:
                await self.client.disconnect()
                raise ConfigEntryAuthFailed(str(err)) from err
            except (BleakError, ScooterProtocolError) as err:
                self.update_interval = timedelta(
                    seconds=DISCONNECTED_RETRY_INTERVAL
                )
                await self.client.disconnect()
                raise HomeAssistantError(str(err)) from err
        await self.async_request_refresh()

    def _resolve_ble_device(self) -> None:
        device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if device is None:
            raise ScooterProtocolError("Scooter is not reachable over Bluetooth")
        self.client.set_device(device)

    async def async_shutdown(self) -> None:
        """Close BLE resources."""
        await self.client.disconnect()
