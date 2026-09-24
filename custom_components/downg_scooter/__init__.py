"""Home Assistant integration for DownG-compatible scooters."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_PROTOCOL,
    DOMAIN,
    PLATFORMS,
    PROTOCOL_MIAUTH,
)
from .coordinator import DownGScooterCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up DownG Scooter from a config entry."""
    coordinator = DownGScooterCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate experimental 5AA5 entries to the real MiAuth reauth path."""
    if entry.version >= 2:
        return True
    data = dict(entry.data)
    if data.get(CONF_PROTOCOL) == "5aa5":
        data[CONF_PROTOCOL] = PROTOCOL_MIAUTH
    data.pop("model_hint", None)
    hass.config_entries.async_update_entry(entry, data=data, version=2)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: DownGScooterCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()
    return unload_ok
