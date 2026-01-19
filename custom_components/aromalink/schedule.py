"""Schedule entity for Aroma-Link Diffuser."""
from __future__ import annotations

import json
import logging
from typing import Any

from homeassistant.helpers.entity import Entity, DeviceInfo
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Aroma-Link schedule entities based on a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    client = data["client"]
    devices = data["devices"]

    entities = []
    # Create a schedule entity for each device and each day (0=Sunday, 1=Monday, ...)
    for device in devices:
        for day in range(7):
            entities.append(AromaLinkScheduleEntity(client, device, day))
    async_add_entities(entities)

class AromaLinkScheduleEntity(Entity):
    """Entity representing the schedule for a specific device and day."""

    _attr_icon = "mdi:calendar-clock"

    def __init__(self, client, device, day_of_week: int):
        self._client = client
        self._device = device
        self._day_of_week = day_of_week
        self._attr_unique_id = f"{device.id}_schedule_{day_of_week}"
        self._attr_name = f"{device.name} Schedule {self._day_name(day_of_week)}"
        self._schedule = []
        self._client.add_callback(self._handle_ws_message)

    @staticmethod
    def _day_name(day: int) -> str:
        return ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"][day]

    async def _handle_ws_message(self, message: dict) -> None:
        """Handle WebSocket state updates."""
        if not isinstance(message, dict):
            _LOGGER.error("WebSocket message is not a dict: %s", message)
            return

        if message.get("type") == "WORK_TIME_FREQUENCY":
            schedule_list = message.get("data", [])
            if not isinstance(schedule_list, list):
                _LOGGER.error("WORK_TIME_FREQUENCY data is not a list: %s", schedule_list)
                return

            if schedule_list and str(schedule_list[0].get("deviceId")) == str(self._device.id):
                self._schedule = schedule_list
                self.async_write_ha_state()

    @property
    def state(self):
        """Return the number of enabled schedule blocks."""
        return sum(1 for block in self._schedule if block.get("enabled"))

    @property
    def extra_state_attributes(self):
        """Return the full schedule as attributes."""
        return {
            "schedule": self._schedule
        }

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers = {(DOMAIN, self._device.id)},
            name = self._device.name,
            manufacturer = "Aroma-Link",
            model = "Diffuser",
        )

    async def async_set_schedule(self, new_schedule: list[dict[str, Any]]) -> bool:
        """Set a new schedule for this device and day."""
        payload = {
            "week": [self._day_of_week],
            "deviceId": str(self._device.id),
            "workTimeList": [
                {
                    "startTime": block["startHour"],
                    "endTime": block["endHour"],
                    "enabled": block["enabled"],
                    "workDuration": str(block["workSec"]),
                    "pauseDuration": str(block["pauseSec"]),
                    "consistenceLevel": block.get("consistenceLevel", 1),
                }
                for block in new_schedule
            ],
            "userId": self._client.user_id,
        }
        url = f"https://www.aroma-link.com/v1/app/data/workSetApp"
        headers = {
            "access_token": self._client.access_token,
            "User-Agent": "KeRuiMa/1.1.3",
            "Accept": "*/*",
            "version": "1"
        }
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=payload) as resp:
                    if resp.status == 200:
                        _LOGGER.info("Schedule updated for device %s day %s", self._device.id, self._day_of_week)
                        return True
                    else:
                        _LOGGER.error("Failed to update schedule: %s %s", resp.status, await resp.text())
                        return False
        except Exception as e:
            _LOGGER.error("Error updating schedule: %s", e)
            return False

    async def async_will_remove_from_hass(self) -> None:
        self._client.remove_callback(self._handle_ws_message)