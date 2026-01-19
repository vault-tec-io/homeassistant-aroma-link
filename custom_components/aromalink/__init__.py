"""The Aroma-Link Diffuser integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .aromalink_api import AromaLinkClient
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SWITCH, Platform.SENSOR, Platform.NUMBER]

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Aroma-Link component."""
    hass.data.setdefault(DOMAIN, {})
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Aroma-Link from a config entry."""
    try:
        client = AromaLinkClient(
            username=entry.data["username"],
            access_token=entry.data.get("access_token")
        )

        devices = await client.get_devices()
        if not devices:
            raise ConfigEntryNotReady("No devices found")

        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][entry.entry_id] = {
            "client": client,
            "devices": devices
        }

        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        
        # Start WebSocket connection after platforms are set up
        for device in devices:
            await client.start_websocket(device.id)

        return True

    except Exception as exc:
        _LOGGER.error("Error setting up Aroma-Link integration: %s", exc)
        raise ConfigEntryNotReady from exc

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok

async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)