"""Binary sensor platform for Aroma-Link schedule blocks."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .aroma_link_api import AromaLinkDevice

_LOGGER = logging.getLogger(__name__)

# Day names for display
DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Aroma-Link schedule block binary sensors."""
    data = hass.data[DOMAIN][entry.entry_id]
    client = data["client"]
    devices = data["devices"]

    entities = []
    for device in devices:
        # Create 5 schedule block entities per device
        for block_num in range(1, 6):
            entities.append(AromaLinkScheduleBlock(client, device, block_num))

    async_add_entities(entities)


class AromaLinkScheduleBlock(BinarySensorEntity):
    """Representation of an Aroma-Link schedule block."""

    def __init__(self, client, device: AromaLinkDevice, block_number: int):
        """Initialize the schedule block."""
        self._client = client
        self._device = device
        self._block_number = block_number
        self._attr_unique_id = f"{device.id}_schedule_block_{block_number}"
        self._attr_name = f"{device.name} Schedule Block {block_number}"
        self._attr_icon = "mdi:calendar-clock"
        self._attr_is_on = False
        self._schedule_fetched = False

        # Initialize attributes
        self._attr_extra_state_attributes = {
            "start_time": "00:00",
            "end_time": "00:00",
            "work_duration": 10,
            "pause_duration": 120,
            "days": [],
            "days_display": "",
            "block_number": block_number
        }

        # Register callback for updates
        self._client.add_callback(self._handle_ws_message)

    async def async_added_to_hass(self) -> None:
        """Fetch schedule when entity is added to hass."""
        await self._fetch_schedule()

    async def _fetch_schedule(self):
        """Fetch current schedule from device."""
        try:
            _LOGGER.debug("Fetching schedule for device %s block %s", self._device.id, self._block_number)
            # Get schedule for today (WebSocket-based retrieval)
            schedule_blocks = await self._client.get_schedule(self._device.id)
            _LOGGER.debug("Received %s schedule blocks for device %s block %s",
                        len(schedule_blocks) if schedule_blocks else 0,
                        self._device.id, self._block_number)

            if schedule_blocks and len(schedule_blocks) >= self._block_number:
                block = schedule_blocks[self._block_number - 1]
                _LOGGER.debug("Block %s data: %s", self._block_number, block)
                self._update_from_block(block)
                self._schedule_fetched = True
                _LOGGER.debug("Updated block %s: enabled=%s, start=%s, end=%s, work=%s, pause=%s",
                            self._block_number, self._attr_is_on,
                            block.get("start_time"), block.get("end_time"),
                            block.get("work_duration"), block.get("pause_duration"))
                self.async_write_ha_state()
            else:
                _LOGGER.warning("No schedule blocks returned for device %s block %s (got %s blocks)",
                              self._device.id, self._block_number,
                              len(schedule_blocks) if schedule_blocks else 0)
        except Exception as e:
            _LOGGER.error("Failed to fetch schedule for block %s: %s", self._block_number, e)

    def _update_from_block(self, block: dict):
        """Update entity from schedule block data."""
        self._attr_is_on = block.get("enabled", False)

        days = block.get("days", [])
        days_display = ", ".join([DAY_NAMES[d] for d in sorted(days)]) if days else "None"

        self._attr_extra_state_attributes = {
            "start_time": block.get("start_time", "00:00"),
            "end_time": block.get("end_time", "00:00"),
            "work_duration": block.get("work_duration", 10),
            "pause_duration": block.get("pause_duration", 120),
            "days": days,
            "days_display": days_display,
            "block_number": self._block_number
        }

    async def _handle_ws_message(self, message: dict) -> None:
        """Handle WebSocket state updates."""
        # Schedule updates might come through WebSocket in future
        # For now, we rely on manual fetches after updates
        pass

    async def async_will_remove_from_hass(self) -> None:
        """Cleanup on entity removal."""
        self._client.remove_callback(self._handle_ws_message)

    @property
    def is_on(self) -> bool:
        """Return true if schedule block is enabled."""
        return self._attr_is_on

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._client.is_device_available(self._device.id)

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._device.id)},
            name=self._device.name,
            manufacturer="Aroma-Link",
            model="Diffuser",
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the schedule block (must use service to set times/durations)."""
        _LOGGER.info("Schedule block can only be configured via aroma_link.set_schedule_block service")

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the schedule block."""
        # Get current schedule
        schedule_blocks = await self._client.get_schedule(self._device.id)
        if not schedule_blocks:
            _LOGGER.error("Failed to fetch current schedule")
            return

        # Disable this block
        schedule_blocks[self._block_number - 1]["enabled"] = False

        # Update schedule
        if await self._client.set_schedule(self._device.id, schedule_blocks=schedule_blocks):
            self._attr_is_on = False
            self.async_write_ha_state()
            # Refresh to confirm
            await self._fetch_schedule()
