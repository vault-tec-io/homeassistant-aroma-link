"""Sensor platform for Aroma-Link Diffuser."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    ATTR_WORK_TIME,
    ATTR_PAUSE_TIME,
    ATTR_WORK_REMAIN,
    ATTR_PAUSE_REMAIN,
    ATTR_CURRENT_PHASE,
)

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Aroma-Link sensors based on a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    client = data["client"]
    devices = data["devices"]

    entities = []
    for device in devices:
        entities.extend([
            AromaLinkPhaseSensor(client, device),
            AromaLinkWorkCountdownSensor(client, device),
            AromaLinkPauseCountdownSensor(client, device),
        ])

    async_add_entities(entities)

class AromaLinkBaseSensor(SensorEntity):
    """Base class for Aroma-Link sensors with common availability logic."""

    def __init__(self, client, device):
        self._client = client
        self._device = device

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


class AromaLinkPhaseSensor(AromaLinkBaseSensor):
    """Sensor for the current phase (work or pause)."""

    def __init__(self, client, device):
        super().__init__(client, device)
        self._attr_unique_id = f"{device.id}_current_phase"
        self._attr_name = f"{device.name} Current Phase"
        self._attr_native_value = None
        self._client.add_callback(self._handle_ws_message)

    async def _handle_ws_message(self, message: dict) -> None:
        """Handle WebSocket state updates."""
        if not isinstance(message, dict):
            return
        if message.get("type") in ["SUPERCOMMAND", "COUNTDOWN"]:
            device_data = message.get("data", {})
            if str(device_data.get("deviceId")) == str(self._device.id):
                # workStatus: 1 = work, 0 = pause
                if "workStatus" in device_data:
                    self._attr_native_value = "work" if device_data.get("workStatus") == 1 else "pause"
                elif "currentPhase" in device_data:
                    self._attr_native_value = device_data.get("currentPhase", "unknown")
                self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Cleanup on entity removal."""
        self._client.remove_callback(self._handle_ws_message)


class AromaLinkWorkCountdownSensor(AromaLinkBaseSensor):
    """Sensor for work countdown time."""

    def __init__(self, client, device):
        super().__init__(client, device)
        self._attr_unique_id = f"{device.id}_work_countdown"
        self._attr_name = f"{device.name} Work Countdown"
        self._attr_native_value = None
        self._attr_extra_state_attributes = {}
        self._client.add_callback(self._handle_ws_message)

    async def _handle_ws_message(self, message: dict) -> None:
        """Handle WebSocket state updates."""
        if not isinstance(message, dict):
            return

        if message.get("type") in ["SUPERCOMMAND", "COUNTDOWN"]:
            device_data = message.get("data", {})
            if str(device_data.get("deviceId")) == str(self._device.id):
                if "workRemainTime" in device_data:
                    self._attr_native_value = device_data["workRemainTime"]
                    # Determine phase from workStatus or currentPhase
                    phase = "unknown"
                    if "workStatus" in device_data:
                        phase = "work" if device_data.get("workStatus") == 1 else "pause"
                    elif "currentPhase" in device_data:
                        phase = device_data.get("currentPhase", "unknown")
                    self._attr_extra_state_attributes = {
                        "current_phase": phase,
                    }
                    self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Cleanup on entity removal."""
        self._client.remove_callback(self._handle_ws_message)


class AromaLinkPauseCountdownSensor(AromaLinkBaseSensor):
    """Sensor for pause countdown time."""

    def __init__(self, client, device):
        super().__init__(client, device)
        self._attr_unique_id = f"{device.id}_pause_countdown"
        self._attr_name = f"{device.name} Pause Countdown"
        self._attr_native_value = None
        self._attr_extra_state_attributes = {}
        self._client.add_callback(self._handle_ws_message)

    async def _handle_ws_message(self, message: dict) -> None:
        """Handle WebSocket state updates."""
        if not isinstance(message, dict):
            return

        if message.get("type") in ["SUPERCOMMAND", "COUNTDOWN"]:
            device_data = message.get("data", {})
            if str(device_data.get("deviceId")) == str(self._device.id):
                if "pauseRemainTime" in device_data:
                    self._attr_native_value = device_data["pauseRemainTime"]
                    # Determine phase from workStatus or currentPhase
                    phase = "unknown"
                    if "workStatus" in device_data:
                        phase = "work" if device_data.get("workStatus") == 1 else "pause"
                    elif "currentPhase" in device_data:
                        phase = device_data.get("currentPhase", "unknown")
                    self._attr_extra_state_attributes = {
                        "current_phase": phase,
                    }
                    self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Cleanup on entity removal."""
        self._client.remove_callback(self._handle_ws_message)