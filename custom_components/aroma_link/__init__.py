"""The Aroma-Link Diffuser integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .aroma_link_api import AromaLinkClient
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SWITCH, Platform.SENSOR, Platform.NUMBER, Platform.BINARY_SENSOR]

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

        # Start WebSocket connection after platforms are set up
        for device in devices:
            await client.start_websocket(device.id)

        # Register services
        async def handle_set_schedule_block(call):
            """Handle set_schedule_block service call."""
            device_id = call.data.get("device_id")
            block_number = call.data.get("block_number")
            start_time = call.data.get("start_time")
            end_time = call.data.get("end_time")
            work_duration = call.data.get("work_duration")
            pause_duration = call.data.get("pause_duration")
            days_raw = call.data.get("days", [0, 1, 2, 3, 4, 5, 6])
            enabled = call.data.get("enabled", True)

            # Convert days from strings to integers if needed
            days = [int(d) if isinstance(d, str) else d for d in days_raw]

            # Fetch current schedule
            schedule_blocks = await client.get_schedule(device_id)
            if not schedule_blocks:
                _LOGGER.error("Failed to fetch current schedule for device %s", device_id)
                return

            # Update the specified block
            schedule_blocks[block_number - 1] = {
                "start_time": start_time,
                "end_time": end_time,
                "work_duration": work_duration,
                "pause_duration": pause_duration,
                "days": days,
                "enabled": enabled
            }

            # Send updated schedule
            if await client.set_schedule(device_id, schedule_blocks=schedule_blocks):
                _LOGGER.info("Schedule block %s updated for device %s", block_number, device_id)
            else:
                _LOGGER.error("Failed to update schedule block %s for device %s", block_number, device_id)

        async def handle_clear_schedule_block(call):
            """Handle clear_schedule_block service call."""
            device_id = call.data.get("device_id")
            block_number = call.data.get("block_number")

            # Fetch current schedule
            schedule_blocks = await client.get_schedule(device_id)
            if not schedule_blocks:
                _LOGGER.error("Failed to fetch current schedule for device %s", device_id)
                return

            # Disable the specified block
            schedule_blocks[block_number - 1]["enabled"] = False

            # Send updated schedule
            if await client.set_schedule(device_id, schedule_blocks=schedule_blocks):
                _LOGGER.info("Schedule block %s cleared for device %s", block_number, device_id)
            else:
                _LOGGER.error("Failed to clear schedule block %s for device %s", block_number, device_id)

        async def handle_sync_schedule(call):
            """Handle sync_schedule service call."""
            device_id = call.data.get("device_id")

            schedule_blocks = await client.get_schedule(device_id)
            if schedule_blocks:
                _LOGGER.info("Schedule synced for device %s: %s", device_id, schedule_blocks)
            else:
                _LOGGER.error("Failed to sync schedule for device %s", device_id)

        hass.services.async_register(DOMAIN, "set_schedule_block", handle_set_schedule_block)
        hass.services.async_register(DOMAIN, "clear_schedule_block", handle_clear_schedule_block)
        hass.services.async_register(DOMAIN, "sync_schedule", handle_sync_schedule)

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
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok

async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)