"""Coordinator for DownG Scooter."""

from __future__ import annotations

from datetime import timedelta
import logging

from bleak.exc import BleakError

from homeassistant.components import bluetooth, persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_ADDRESS,
    CONF_PROTOCOL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    PROTOCOL_ENCRYPTED,
    PROTOCOL_PLAIN,
)
from .protocol import (
    DownGScooterClient,
    ScooterConfirmationRequired,
    ScooterData,
    ScooterProtocolError,
)

_LOGGER = logging.getLogger(__name__)


class DownGScooterCoordinator(DataUpdateCoordinator[ScooterData]):
    """Fetch scooter telemetry and expose command helpers."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize coordinator."""
        self.entry = entry
        self.name = entry.data[CONF_NAME]
        self.address = entry.data[CONF_ADDRESS]
        self.protocol = entry.data.get(CONF_PROTOCOL, PROTOCOL_PLAIN)
        self.client = DownGScooterClient(
            self.address,
            scooter_name=self.name,
            encrypted=self.protocol == PROTOCOL_ENCRYPTED,
        )
        self._confirmation_notification_id = (
            f"{DOMAIN}_{entry.entry_id}_confirmation_required"
        )

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
            data = await self.client.read_telemetry()
            persistent_notification.async_dismiss(
                self.hass, self._confirmation_notification_id
            )
            return data
        except ScooterConfirmationRequired as err:
            self._notify_confirmation_required()
            raise UpdateFailed(str(err)) from err
        except (BleakError, ScooterProtocolError) as err:
            raise UpdateFailed(str(err)) from err
        finally:
            await self.client.disconnect()

    async def async_set_locked(self, locked: bool) -> None:
        """Set scooter software lock state."""
        self._resolve_ble_device()
        try:
            await self.client.set_locked(locked)
        except ScooterConfirmationRequired:
            self._notify_confirmation_required()
            await self.client.disconnect()
            raise
        except (BleakError, ScooterProtocolError):
            await self.client.disconnect()
            raise
        # Reuse the authenticated connection for the immediate state refresh.
        await self.async_request_refresh()

    def _resolve_ble_device(self) -> None:
        """Select the nearest connectable HA Bluetooth adapter or proxy."""
        device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if device is None:
            raise ScooterProtocolError("Scooter is not reachable over Bluetooth")
        self.client.set_device(device)

    def _notify_confirmation_required(self) -> None:
        """Ask an administrator to confirm the connection on the scooter."""
        persistent_notification.async_create(
            self.hass,
            (
                f"La connexion Bluetooth a {self.name} doit etre confirmee. "
                "Rechargez l'integration, puis appuyez une fois sur le bouton "
                "d'alimentation lorsque la trottinette emet son bip "
                "d'authentification."
            ),
            title="Confirmation requise pour la trottinette",
            notification_id=self._confirmation_notification_id,
        )

    async def async_shutdown(self) -> None:
        """Close BLE resources."""
        persistent_notification.async_dismiss(
            self.hass, self._confirmation_notification_id
        )
        await self.client.disconnect()
