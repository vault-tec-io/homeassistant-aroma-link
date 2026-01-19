"""Switch platform for Aroma-Link Diffuser."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .aromalink_api import AromaLinkDevice

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Aroma-Link switch based on a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    client = data["client"]
    devices = data["devices"]

    entities = []
    for device in devices:
        entities.append(AromaLinkPowerSwitch(client, device))
        if device.has_fan:
            entities.append(AromaLinkFanSwitch(client, device))

    async_add_entities(entities)

class AromaLinkPowerSwitch(SwitchEntity):
    """Representation of an Aroma-Link power switch."""

    def __init__(self, client, device: AromaLinkDevice):
        self._client = client
        self._device = device
        self._attr_unique_id = f"{device.id}_power"
        self._attr_name = f"{device.name} Power"
        self._is_on = device.online  # Use last known state
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
                new_state = device_data.get("onOff") == 1
                if new_state != self._is_on:
                    self._is_on = new_state
                    self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Cleanup on entity removal."""
        self._client.remove_callback(self._handle_ws_message)

    @property
    def is_on(self) -> bool:
        """Return true if device is on."""
        return self._is_on

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._client.is_device_available(self._device.id)

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers = {(DOMAIN, self._device.id)},
            name = self._device.name,
            manufacturer = "Aroma-Link",
            model = "Diffuser",
        )
    
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the device on."""
        if await self._client.set_power(self._device.id, True):
            self._is_on = True
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the device off."""
        if await self._client.set_power(self._device.id, False):
            self._is_on = False
            self.async_write_ha_state()

class AromaLinkFanSwitch(SwitchEntity):
    """Representation of an Aroma-Link fan switch."""

    def __init__(self, client, device: AromaLinkDevice):
        """Initialize the switch."""
        self._client = client
        self._device = device
        self._attr_unique_id = f"{device.id}_fan"
        self._attr_name = f"{device.name} Fan"
        self._is_on = device.online  # Use last known state
        self._attr_entity_category = EntityCategory.CONFIG
        
        # Register callback for WebSocket updates
        self._client.add_callback(self._handle_ws_message)

    async def _handle_ws_message(self, message: dict) -> None:
        """Handle WebSocket state updates."""
        if not isinstance(message, dict):
            _LOGGER.error("WebSocket message is not a dict: %s", message)
            return
        if (
            message.get("type") == "SUPERCOMMAND" 
            and message.get("data", {}).get("deviceId") == self._device.id
        ):
            new_state = message["data"].get("fan") == 1
            if new_state != self._is_on:
                self._is_on = new_state
                self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Cleanup on entity removal."""
        self._client.remove_callback(self._handle_ws_message)
        
    @property
    def is_on(self) -> bool:
        """Return true if fan is on."""
        return self._is_on

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._client.is_device_available(self._device.id)

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers = {(DOMAIN, self._device.id)},
            name = self._device.name,
            manufacturer = "Aroma-Link",
            model = "Diffuser",
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the fan on."""
        if await self._client.set_fan(self._device.id, True):
            self._is_on = True
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the fan off."""
        if await self._client.set_fan(self._device.id, False):
            self._is_on = False
            self.async_write_ha_state()