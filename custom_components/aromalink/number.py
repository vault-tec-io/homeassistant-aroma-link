"""Number platform for Aroma-Link Diffuser scheduling."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import EntityCategory, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback


from .const import (
    DOMAIN,
    DEFAULT_WORK_TIME,
    DEFAULT_PAUSE_TIME,
)

_LOGGER = logging.getLogger(__name__)

NUMBER_DESCRIPTIONS = [
    NumberEntityDescription(
        key="work_time",
        name="Work Time",
        icon="mdi:timer",
        native_min_value=5,
        native_max_value=60,
        native_step=5,
        native_unit_of_measurement="s",
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    NumberEntityDescription(
        key="pause_time",
        name="Pause Time",
        icon="mdi:timer-pause",
        native_min_value=60,
        native_max_value=300,
        native_step=30,
        native_unit_of_measurement="s",
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
]

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Aroma-Link number entities from config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    client = data["client"]
    devices = data["devices"]

    entities = []
    for device in devices:
        for description in NUMBER_DESCRIPTIONS:
            if description.key == "work_time":
                entities.append(AromaLinkWorkTimeNumber(client, device, description))
            elif description.key == "pause_time":
                entities.append(AromaLinkPauseTimeNumber(client, device, description))

    async_add_entities(entities)

class AromaLinkBaseNumber(NumberEntity):
    """Base class for Aroma-Link number entities."""

    def __init__(self, client, device, description: NumberEntityDescription) -> None:
        """Initialize the number entity."""
        self._client = client
        self._device = device
        self.entity_description = description
        self._attr_unique_id = f"{device.id}_{description.key}"
        self._attr_name = f"{device.name} {description.name}"
        
        # Add device info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.id)},
            name=device.name,
            manufacturer="Aroma-Link",
            model="Diffuser",
        )

        # Register callback for WebSocket updates
        self._client.add_callback(self._handle_ws_message)

    async def _handle_ws_message(self, message: dict) -> None:
        """Handle WebSocket state updates."""
        if not isinstance(message, dict):
            _LOGGER.error("WebSocket message is not a dict: %s", message)
            return

        if message.get("type") == "SUPERCOMMAND":
            device_data = message.get("data", {})
            if not isinstance(device_data, dict):
                _LOGGER.error("SUPERCOMMAND data is not a dict: %s", device_data)
                return

            if str(device_data.get("deviceId")) == str(self._device.id):
                new_value = device_data.get(self.entity_description.key)
                if new_value is not None and new_value != self._attr_native_value:
                    _LOGGER.debug(
                        "Updating %s from %s to %s",
                        self.entity_description.name,
                        self._attr_native_value,
                        new_value,
                    )
                    self._attr_native_value = new_value
                    self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Cleanup on entity removal."""
        self._client.remove_callback(self._handle_ws_message)

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers = {(DOMAIN, self._device.id)},
            name = self._device.name,
            manufacturer = "Aroma-Link",
            model = "Diffuser",
        )

    async def async_set_native_value(self, value: float) -> None:
        """Update the current value."""
        try:
            _LOGGER.debug("Setting %s to %s", self.entity_description.name, value)
            result = await self._client.set_schedule(
                device_id=self._device.id,
                work_duration=int(value) if self.entity_description.key == "work_time" else None,
                pause_duration=int(value) if self.entity_description.key == "pause_time" else None,
            )
            if result:
                self._attr_native_value = value
                self.async_write_ha_state()
        except Exception as e:
            _LOGGER.error("Failed to set %s: %s", self.entity_description.name, e)

class AromaLinkWorkTimeNumber(AromaLinkBaseNumber):
    """Number entity for work time configuration."""

    def __init__(self, client, device, description: NumberEntityDescription) -> None:
        """Initialize the work time number."""
        super().__init__(client, device, description)
        self._attr_native_value = DEFAULT_WORK_TIME

class AromaLinkPauseTimeNumber(AromaLinkBaseNumber):
    """Number entity for pause time configuration."""

    def __init__(self, client, device, description: NumberEntityDescription) -> None:
        """Initialize the pause time number."""
        super().__init__(client, device, description)
        self._attr_native_value = DEFAULT_PAUSE_TIME