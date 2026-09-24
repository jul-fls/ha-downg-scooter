"""Config flow for DownG Scooter."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_NAME

from .const import CONF_ADDRESS, CONF_MODEL_HINT, DEFAULT_NAME, DOMAIN


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
            return self.async_create_entry(
                title=self._discovered_name,
                data={
                    CONF_NAME: self._discovered_name,
                    CONF_ADDRESS: self._discovered_address,
                    CONF_MODEL_HINT: "",
                },
            )

        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={
                "name": self._discovered_name,
                "address": self._discovered_address,
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
