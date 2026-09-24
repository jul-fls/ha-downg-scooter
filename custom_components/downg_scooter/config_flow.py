"""Config flow for DownG Scooter."""

from __future__ import annotations

from typing import Any

from bleak.exc import BleakError
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_NAME

from .const import CONF_ADDRESS, CONF_MODEL_HINT, DEFAULT_NAME, DOMAIN
from .protocol import (
    DownGScooterClient,
    ScooterConfirmationRequired,
    ScooterProtocolError,
)


class DownGScooterConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for DownG Scooter."""

    VERSION = 1
    _discovered_address: str
    _discovered_name: str

    async def async_step_bluetooth(
        self, discovery_info: bluetooth.BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle bluetooth discovery."""
        address = discovery_info.address.strip().upper()
        await self.async_set_unique_id(address)
        self._abort_if_unique_id_configured()

        self._discovered_address = address
        self._discovered_name = discovery_info.name or DEFAULT_NAME
        self.context["title_placeholders"] = {
            "name": self._discovered_name,
            "address": self._discovered_address,
        }
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm a scooter discovered from its BLE advertisement."""
        if user_input is not None:
            try:
                await self._async_probe_discovered_scooter()
            except ScooterConfirmationRequired:
                return await self.async_step_pairing()
            except (BleakError, ScooterProtocolError):
                return self.async_abort(reason="cannot_connect")
            return self._async_create_discovered_entry()

        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={
                "name": self._discovered_name,
                "address": self._discovered_address,
            },
        )

    async def async_step_pairing(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Wait for optional physical confirmation on the scooter."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await self._async_probe_discovered_scooter()
            except ScooterConfirmationRequired:
                errors["base"] = "confirmation_failed"
            except (BleakError, ScooterProtocolError):
                errors["base"] = "cannot_connect"
            else:
                return self._async_create_discovered_entry()

        self._set_confirm_only()
        return self.async_show_form(
            step_id="pairing",
            description_placeholders={
                "name": self._discovered_name,
                "address": self._discovered_address,
            },
            errors=errors,
        )

    async def _async_probe_discovered_scooter(self) -> None:
        """Test one register read before creating the config entry."""
        device = bluetooth.async_ble_device_from_address(
            self.hass, self._discovered_address, connectable=True
        )
        if device is None:
            raise ScooterProtocolError("Scooter is not reachable over Bluetooth")

        client = DownGScooterClient(device)
        try:
            await client.probe()
        finally:
            await client.disconnect()

    def _async_create_discovered_entry(self) -> ConfigFlowResult:
        """Create an entry after a successful Bluetooth probe."""
        return self.async_create_entry(
            title=self._discovered_name,
            data={
                CONF_NAME: self._discovered_name,
                CONF_ADDRESS: self._discovered_address,
                CONF_MODEL_HINT: "",
            },
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle manual setup."""
        errors: dict[str, str] = {}

        if user_input is not None:
            address = user_input[CONF_ADDRESS].strip().upper()
            await self.async_set_unique_id(address)
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=user_input.get(CONF_NAME) or DEFAULT_NAME,
                data={
                    CONF_NAME: user_input.get(CONF_NAME) or DEFAULT_NAME,
                    CONF_ADDRESS: address,
                    CONF_MODEL_HINT: user_input.get(CONF_MODEL_HINT, ""),
                },
            )

        data_schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
                vol.Required(CONF_ADDRESS): str,
                vol.Optional(CONF_MODEL_HINT, default=""): str,
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
        )
