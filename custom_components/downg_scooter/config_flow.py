"""Config flow for DownG Scooter."""

from __future__ import annotations

import asyncio
from typing import Any

from bleak.exc import BleakError
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult
from homeassistant.const import CONF_NAME

from .const import (
    CONF_ADDRESS,
    CONF_PROTOCOL,
    CONF_TOKEN,
    DEFAULT_NAME,
    DOMAIN,
    PROTOCOL_MIAUTH,
    PROTOCOL_PLAIN,
)
from .mi_auth import MiAuthRestartRequired
from .protocol import (
    DownGScooterClient,
    PAIRING_CONFIRMATION_WINDOW,
    PAIRING_FIRST_TIMEOUT,
    PAIRING_SECOND_TIMEOUT,
    ScooterProtocolError,
    protocol_mode_from_advertisement,
)


class DownGScooterConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure and pair a Xiaomi scooter."""

    VERSION = 2

    _address: str
    _name: str
    _protocol: str
    _reauth_entry: ConfigEntry | None = None

    async def async_step_bluetooth(
        self, discovery_info: bluetooth.BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a scooter advertisement selected by Home Assistant."""
        self._address = discovery_info.address.strip().upper()
        self._name = discovery_info.name or DEFAULT_NAME
        self._protocol = protocol_mode_from_advertisement(
            discovery_info.manufacturer_data, discovery_info.service_data
        )
        await self.async_set_unique_id(self._address)
        self._abort_if_unique_id_configured()
        self.context["title_placeholders"] = {
            "name": self._name,
            "address": self._address,
        }
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm an automatically discovered scooter."""
        if user_input is not None:
            if self._protocol == PROTOCOL_MIAUTH:
                return await self.async_step_pairing()
            try:
                await self._async_probe_plain()
            except (BleakError, ScooterProtocolError):
                return self.async_abort(reason="cannot_connect")
            return self._create_entry(token=None)

        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={"name": self._name, "address": self._address},
        )

    async def async_step_pairing(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pair MiAuth while the user handles the five-second button window."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                token = await self._async_pair_miauth()
            except (BleakError, ScooterProtocolError):
                errors["base"] = "pairing_failed"
            else:
                if self._reauth_entry is not None:
                    return self.async_update_reload_and_abort(
                        self._reauth_entry,
                        data_updates={
                            CONF_NAME: self._name,
                            CONF_ADDRESS: self._address,
                            CONF_PROTOCOL: PROTOCOL_MIAUTH,
                            CONF_TOKEN: token.hex(),
                        },
                    )
                return self._create_entry(token=token)

        self._set_confirm_only()
        return self.async_show_form(
            step_id="pairing",
            description_placeholders={"name": self._name, "address": self._address},
            errors=errors,
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle manual setup without asking for a model string."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._address = user_input[CONF_ADDRESS].strip().upper()
            self._name = user_input.get(CONF_NAME) or DEFAULT_NAME
            await self.async_set_unique_id(self._address)
            self._abort_if_unique_id_configured()

            info = bluetooth.async_last_service_info(
                self.hass, self._address, connectable=True
            )
            if info is None:
                errors["base"] = "cannot_connect"
            else:
                self._protocol = protocol_mode_from_advertisement(
                    info.manufacturer_data, info.service_data
                )
                if self._protocol == PROTOCOL_MIAUTH:
                    return await self.async_step_pairing()
                try:
                    await self._async_probe_plain()
                except (BleakError, ScooterProtocolError):
                    errors["base"] = "cannot_connect"
                else:
                    return self._create_entry(token=None)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
                    vol.Required(CONF_ADDRESS): str,
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Start replacement of a missing or rejected MiAuth token."""
        entry_id = self.context.get("entry_id")
        entry = (
            self.hass.config_entries.async_get_entry(entry_id)
            if isinstance(entry_id, str)
            else None
        )
        if entry is None:
            return self.async_abort(reason="reauth_failed")
        self._reauth_entry = entry
        self._address = entry_data[CONF_ADDRESS]
        self._name = entry_data[CONF_NAME]
        self._protocol = PROTOCOL_MIAUTH
        # A real token rejection starts a second BLE client for registration.
        # Unload the live coordinator first so both clients cannot fight over
        # the scooter and repeatedly tear down each other's GATT connection.
        await self.hass.config_entries.async_unload(entry.entry_id)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Explain the physical action before launching re-pairing."""
        if user_input is not None:
            return await self.async_step_pairing(user_input)
        self._set_confirm_only()
        return self.async_show_form(
            step_id="reauth_confirm",
            description_placeholders={"name": self._name, "address": self._address},
        )

    async def _async_pair_miauth(self) -> bytes:
        """Run the two-attempt sequence proven by the Windows diagnostic."""
        device = bluetooth.async_ble_device_from_address(
            self.hass, self._address, connectable=True
        )
        if device is None:
            raise ScooterProtocolError("Scooter is not reachable over Bluetooth")
        client = DownGScooterClient(
            device, scooter_name=self._name, protocol=PROTOCOL_MIAUTH
        )
        try:
            try:
                return await client.register_miauth(timeout=PAIRING_FIRST_TIMEOUT)
            except MiAuthRestartRequired:
                await client.disconnect()
                await asyncio.sleep(PAIRING_CONFIRMATION_WINDOW)
                refreshed = bluetooth.async_ble_device_from_address(
                    self.hass, self._address, connectable=True
                )
                if refreshed is None:
                    raise ScooterProtocolError(
                        "Scooter disappeared during physical confirmation"
                    )
                client.set_device(refreshed)
                try:
                    return await client.register_miauth(
                        timeout=PAIRING_SECOND_TIMEOUT
                    )
                except MiAuthRestartRequired as err:
                    raise ScooterProtocolError(
                        "Physical confirmation was not accepted"
                    ) from err
        finally:
            await client.disconnect()

    async def _async_probe_plain(self) -> None:
        device = bluetooth.async_ble_device_from_address(
            self.hass, self._address, connectable=True
        )
        if device is None:
            raise ScooterProtocolError("Scooter is not reachable over Bluetooth")
        client = DownGScooterClient(
            device, scooter_name=self._name, protocol=PROTOCOL_PLAIN
        )
        try:
            await client.probe()
        finally:
            await client.disconnect()

    def _create_entry(self, *, token: bytes | None) -> ConfigFlowResult:
        data: dict[str, Any] = {
            CONF_NAME: self._name,
            CONF_ADDRESS: self._address,
            CONF_PROTOCOL: self._protocol,
        }
        if token is not None:
            data[CONF_TOKEN] = token.hex()
        return self.async_create_entry(title=self._name, data=data)
