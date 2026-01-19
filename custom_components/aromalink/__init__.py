"""The Aroma-Link Diffuser integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .aromalink_api import AromaLinkClient
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SWITCH, Platform.SENSOR, Platform.NUMBER]
CUSTOM_PLATFORMS: list[str] = ["schedule"]

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Aroma-Link component."""
    hass.data.setdefault(DOMAIN, {})
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Aroma-Link from a config entry."""
    try:
        session = async_get_clientsession(hass)
        client = AromaLinkClient(
            username=entry.data["username"],
            password=entry.data.get("password"),
            access_token=entry.data.get("access_token"),
            session=session
        )
        client.refresh_token = entry.data.get("refresh_token")
        client.user_id = entry.data.get("user_id")

        # Try to get devices with existing token
        devices = await client.get_devices()

        # If no devices, try to refresh token or re-login
        if not devices:
            _LOGGER.info("Failed to get devices, attempting token refresh")
            if await client.refresh_access_token():
                devices = await client.get_devices()

        if not devices and client.password:
            _LOGGER.info("Token refresh failed, attempting re-login")
            if await client.login():
                devices = await client.get_devices()
                # Update config entry with new tokens
                hass.config_entries.async_update_entry(
                    entry,
                    data={
                        **entry.data,
                        "access_token": client.access_token,
                        "refresh_token": client.refresh_token,
                        "user_id": client.user_id,
                    }
                )

        if not devices:
            raise ConfigEntryNotReady("No devices found - authentication may have failed")

        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][entry.entry_id] = {
            "client": client,
            "devices": devices
        }

        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        await hass.config_entries.async_forward_entry_setups(entry, CUSTOM_PLATFORMS)
        
        # Start WebSocket connection after platforms are set up
        for device in devices:
            await client.start_websocket(device.id)

        return True

    except Exception as exc:
        _LOGGER.error("Error setting up Aroma-Link integration: %s", exc)
        raise ConfigEntryNotReady from exc

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Stop WebSocket connections first
    data = hass.data[DOMAIN].get(entry.entry_id)
    if data and "client" in data:
        await data["client"].stop_all_websockets()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    unload_ok = unload_ok and await hass.config_entries.async_unload_platforms(entry, CUSTOM_PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok

async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)