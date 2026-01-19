import asyncio
import hashlib
import json
import logging
import threading
import time
from typing import Dict, List, Optional

import aiohttp
import websockets

BASE_URL = "https://www.aroma-link.com"
WS_URL = "ws://www.aroma-link.com/ws/asset"

_LOGGER = logging.getLogger(__name__)

class AromaLinkDevice:
    """Representation of an Aroma-Link device."""
    def __init__(self, device_data: dict):
        self.id = device_data["id"]
        self.name = device_data["text"]
        self.device_no = device_data["deviceNo"]
        self.has_fan = device_data["hasFan"] == 1
        self.online = device_data["onlineStatus"] == 1

class AromaLinkClient:
    """API Client for Aroma-Link devices."""
    def __init__(self, username: str, password: str = None, access_token: str = None):
        self.username = username
        self.password = password
        self.hashed_password = hashlib.md5(password.encode()).hexdigest() if password else None
        self.access_token = access_token
        self.refresh_token = None
        self.user_id = None
        self.devices: List[AromaLinkDevice] = []
        self.ws = None
        self.ws_task = None
        self._ws_connected = False
        self._callbacks = []
        self._waiting_for_response = False
        self._current_phase = None
        self._work_remain_time = 0
        self._pause_remain_time = 0
        self._work_time = 0
        self._pause_time = 0
        self.ws_tasks = {}  # device_id -> task

    async def login(self) -> bool:
        """Login and get access token."""
        try:
            async with aiohttp.ClientSession() as session:
                # Login
                login_data = {"userName": self.username, "password": self.hashed_password}
                async with session.post(f"{BASE_URL}/v1/app/user/newLogin", data=login_data) as resp:
                    if resp.status != 200:
                        return False
                
                # Get token
                async with session.post(f"{BASE_URL}/v2/app/token", data=login_data) as resp:
                    if resp.status != 200:
                        return False
                    data = await resp.json()
                    self.access_token = data["data"]["accessToken"]
                    self.refresh_token = data["data"]["refreshToken"]
                    self.user_id = data["data"]["id"]
                    return True
        except Exception:
            _LOGGER.exception("Login failed")
            return False

    async def get_devices(self) -> List[AromaLinkDevice]:
        """Get list of devices."""
        try:
            async with aiohttp.ClientSession() as session:
                headers = {"access_token": self.access_token}
                async with session.get(f"{BASE_URL}/v1/app/device/listAll/{self.user_id}", headers=headers) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
                    devices = []
                    for group in data["data"]:
                        for device in group["children"]:
                            devices.append(AromaLinkDevice(device))
                    self.devices = devices
                    return devices
        except Exception:
            _LOGGER.exception("Failed to get devices")
            return []

    async def set_power(self, device_id: str, state: bool) -> bool:
        """Set device power state."""
        try:
            async with aiohttp.ClientSession() as session:
                data = {
                    "deviceId": device_id,
                    "onOff": "1" if state else "0",
                    "userId": self.user_id
                }
                headers = {"access_token": self.access_token}
                async with session.post(f"{BASE_URL}/v1/app/data/newSwitch", data=data, headers=headers) as resp:
                    return resp.status == 200
        except Exception as e:
            _LOGGER.error("Failed to set power: %s", e)
            return False

    async def set_fan(self, device_id: str, state: bool) -> bool:
        """Set device fan state."""
        try:
            async with aiohttp.ClientSession() as session:
                data = {
                    "deviceId": device_id,
                    "fan": "1" if state else "0",
                    "userId": self.user_id
                }
                headers = {"access_token": self.access_token}
                async with session.post(f"{BASE_URL}/v1/app/data/switch", data=data, headers=headers) as resp:
                    return resp.status == 200
        except Exception as e:
            _LOGGER.error("Failed to set fan: %s", e)
            return False

    async def set_schedule(
        self,
        device_id: str,
        work_duration: int = None,
        pause_duration: int = None,
    ) -> bool:
        """Set device schedule."""
        try:
            async with aiohttp.ClientSession() as session:
                data = {
                    "deviceId": device_id,
                    "userId": self.user_id,
                    "workTimeList": [{
                        "startTime": "00:00",
                        "endTime": "23:59",
                        "enabled": 1,
                        "workDuration": str(work_duration) if work_duration is not None else None,
                        "pauseDuration": str(pause_duration) if pause_duration is not None else None,
                    }],
                    "week": [1,2,3,4,5,6,7]  # All days of week
                }
                
                # Remove None values
                if data["workTimeList"][0]["workDuration"] is None:
                    del data["workTimeList"][0]["workDuration"]
                if data["workTimeList"][0]["pauseDuration"] is None:
                    del data["workTimeList"][0]["pauseDuration"]

                headers = {"access_token": self.access_token}
                async with session.post(
                    f"{BASE_URL}/v1/app/data/workSetApp",
                    json=data,
                    headers=headers
                ) as resp:
                    return resp.status == 200
        except Exception as e:
            _LOGGER.error("Failed to set schedule: %s", e)
            return False

    async def start_websocket(self, device_id: str):
        """Start WebSocket connection for a device."""
        if device_id in self.ws_tasks:
            return
        self.ws_tasks[device_id] = asyncio.create_task(self._websocket_handler(device_id))

    async def _websocket_handler(self, device_id: str):
        """Handle WebSocket connection and messages."""
        backoff = 5
        while True:
            try:
                # Trigger newWork page before WebSocket connection
                async with aiohttp.ClientSession() as session:
                    headers = {
                        "access_token": self.access_token,
                        "User-Agent": "KeRuiMa/1.1.3",
                        "Accept": "*/*",
                        "version": "1"
                    }
                    url = f"{BASE_URL}/v1/app/device/newWork/{device_id}?isOpenPage=0&userId={self.user_id}"
                    await session.get(url, headers=headers)

                async with websockets.connect(WS_URL) as websocket:
                    self.ws = websocket
                    self._ws_connected = True

                    # Start monitoring tasks
                    heartbeat_task = asyncio.create_task(self._heartbeat(device_id))
                    supercommand_task = asyncio.create_task(self._supercommand_monitor(device_id))
                    countdown_task = asyncio.create_task(self._countdown_monitor())

                    # Send initial SUPERCOMMAND
                    await self._send_supercommand(device_id)
                    # Send WORK_TIME_FREQUENCY after fetching schedule
                    await self.send_work_time_frequency(device_id)

                    try:
                        while True:
                            message = await websocket.recv()
                            await self._handle_message(message, device_id)
                    finally:
                        # Cancel monitoring tasks when connection drops
                        heartbeat_task.cancel()
                        supercommand_task.cancel()
                        countdown_task.cancel()

                backoff = 5  # Reset on success

            except Exception as e:
                _LOGGER.error("WebSocket error: %s", e)
                self._ws_connected = False
                self.ws = None
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 300)  # Cap at 5 minutes

    async def _heartbeat(self, device_id: str):
        """Send heartbeat messages."""
        while self._ws_connected:
            try:
                message = {
                    "type": "HEARTBEAT",
                    "data": "{}",
                    "deviceId": device_id
                }
                await self.ws.send(json.dumps(message))
                await asyncio.sleep(10)
            except Exception as e:
                _LOGGER.error("Heartbeat error: %s", e)
                break

    async def _send_supercommand(self, device_id: str):
        """Send SUPERCOMMAND message with trigger."""
        try:
            # Always trigger newWork before SUPERCOMMAND
            async with aiohttp.ClientSession() as session:
                headers = {
                    "access_token": self.access_token,
                    "User-Agent": "KeRuiMa/1.1.3",
                    "Accept": "*/*",
                    "version": "1"
                }
                url = f"{BASE_URL}/v1/app/device/newWork/{device_id}?isOpenPage=0&userId={self.user_id}"
                await session.get(url, headers=headers)

            # Send SUPERCOMMAND
            message = {
                "type": "SUPERCOMMAND",
                "data": {},
                "deviceId": device_id
            }
            await self.ws.send(json.dumps(message))
            self._waiting_for_response = True
            _LOGGER.debug("Sent SUPERCOMMAND for device %s", device_id)
        except Exception as e:
            _LOGGER.error("Failed to send SUPERCOMMAND: %s", e)

    async def _handle_message(self, message: str, device_id: str):
        """Handle incoming WebSocket messages."""
        try:
            if message == "连接成功":
                _LOGGER.debug("WebSocket connection successful")
                return

            _LOGGER.debug("Received WebSocket message: %s", message)

            # Parse the message as JSON if it's a string
            try:
                data = json.loads(message) if isinstance(message, str) else message
            except (json.JSONDecodeError, TypeError):
                _LOGGER.error("Failed to decode message: %s", message)
                return

            # Ensure the parsed message is a dictionary
            if not isinstance(data, dict):
                _LOGGER.error("Unexpected message format: %s", data)
                return

            if data.get("type") == "SUPERCOMMAND":
                device_data = data.get("data", {})
                if isinstance(device_data, str):
                    try:
                        device_data = json.loads(device_data)
                    except Exception:
                        _LOGGER.error("Failed to decode device_data: %s", device_data)
                        return

                if str(device_data.get("deviceId")) == str(device_id):
                    self._work_time = device_data.get("workTime", 0)
                    self._pause_time = device_data.get("pauseTime", 0)
                    self._work_remain_time = device_data.get("workRemainTime", 0)
                    self._pause_remain_time = device_data.get("pauseRemainTime", 0)
                    self._current_phase = "work" if device_data.get("workStatus") == 1 else "pause"
                    self._waiting_for_response = False
                    _LOGGER.debug(
                        "Updated state: work_time=%s, pause_time=%s, phase=%s",
                        self._work_time,
                        self._pause_time,
                        self._current_phase,
                    )

            # Notify all callbacks
            for callback in self._callbacks:
                if isinstance(data, dict):
                    await callback(data)
                else:
                    _LOGGER.error("Callback data is not a dict: %s", data)
        except Exception as e:
            _LOGGER.exception("Failed to handle message: %s", e)

    async def _supercommand_monitor(self, device_id: str):
        """Monitor and send SUPERCOMMAND at appropriate intervals."""
        sent_before_pause_ends = False
        sent_before_work_ends = False
        sent_after_pause_starts = False

        while self._ws_connected:
            try:
                if not self._waiting_for_response:
                    if self._current_phase == "pause":
                        # Send SUPERCOMMAND 1 second before pause ends
                        if self._pause_remain_time == 1 and not sent_before_pause_ends:
                            await self._send_supercommand(device_id)
                            sent_before_pause_ends = True

                        # Send SUPERCOMMAND 1 second after pause starts
                        if (self._pause_remain_time == self._pause_time - 1 
                            and not sent_after_pause_starts):
                            await self._send_supercommand(device_id)
                            sent_after_pause_starts = True

                    elif self._current_phase == "work":
                        # Send SUPERCOMMAND 1 second before work ends
                        if self._work_remain_time == 1 and not sent_before_work_ends:
                            await self._send_supercommand(device_id)
                            sent_before_work_ends = True

                    # Reset flags when transitioning between phases
                    if (self._current_phase == "pause" and 
                        self._pause_remain_time > 1 and 
                        self._pause_remain_time < self._pause_time - 1):
                        sent_before_pause_ends = False
                        sent_after_pause_starts = False

                    if (self._current_phase == "work" and 
                        self._work_remain_time > 1 and 
                        self._work_remain_time < self._work_time - 1):
                        sent_before_work_ends = False

                await asyncio.sleep(1)
            except Exception as e:
                _LOGGER.error("Error in SUPERCOMMAND monitor: %s", e)
                await asyncio.sleep(1)

    async def _countdown_monitor(self):
        """Monitor and update countdown timers."""
        while self._ws_connected:
            try:
                if self._current_phase == "work" and self._work_remain_time > 0:
                    self._work_remain_time -= 1
                    # Notify callbacks of time change
                    for callback in self._callbacks:
                        await callback({
                            "type": "COUNTDOWN",
                            "data": {
                                "deviceId": self.devices[0].id,  # Assuming single device for now
                                "workRemainTime": self._work_remain_time,
                                "currentPhase": "work"
                            }
                        })
                elif self._current_phase == "pause" and self._pause_remain_time > 0:
                    self._pause_remain_time -= 1
                    # Notify callbacks of time change
                    for callback in self._callbacks:
                        await callback({
                            "type": "COUNTDOWN",
                            "data": {
                                "deviceId": self.devices[0].id,  # Assuming single device for now
                                "pauseRemainTime": self._pause_remain_time,
                                "currentPhase": "pause"
                            }
                        })
                # Handle phase transition
                if self._current_phase == "work" and self._work_remain_time == 0:
                    self._current_phase = "pause"
                    _LOGGER.debug("Transitioned to pause phase")
                elif self._current_phase == "pause" and self._pause_remain_time == 0:
                    self._current_phase = "work"
                    _LOGGER.debug("Transitioned to work phase")
                await asyncio.sleep(1)
            except Exception as e:
                _LOGGER.error("Error in countdown monitor: %s", e)
                await asyncio.sleep(1)

    def add_callback(self, callback):
        """Add callback for WebSocket messages."""
        self._callbacks.append(callback)

    def remove_callback(self, callback):
        """Remove callback for WebSocket messages."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    async def get_schedule_for_day(self, device_id: str, day_of_week: int) -> Optional[dict]:
        """Get schedule for a specific day (Sunday=0)."""
        try:
            url = f"{BASE_URL}/v1/app/device/newWorkTime/{device_id}?userId={self.user_id}&week={day_of_week}"
            headers = {
                "access_token": self.access_token,
                "User-Agent": "KeRuiMa/1.1.3",
                "Accept": "*/*",
                "version": "1"
            }
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        _LOGGER.error("Failed to get schedule: %s", resp.status)
                        return None
                    data = await resp.json()
                    _LOGGER.debug("Schedule for device %s, day %s: %s", device_id, day_of_week, data)
                    return data
        except Exception as e:
            _LOGGER.error("Error fetching schedule: %s", e)
            return None

    async def send_work_time_frequency(self, device_id: str):
        """Send WORK_TIME_FREQUENCY message after fetching today's schedule."""
        # Get today's weekday (Sunday=0)
        day_of_week = int(time.strftime("%w"))
        schedule = await self.get_schedule_for_day(device_id, day_of_week)
        # Optionally, do something with the schedule here (e.g., store or update state)
        try:
            message = {
                "type": "WORK_TIME_FREQUENCY",
                "data": "{}",
                "deviceId": device_id
            }
            await self.ws.send(json.dumps(message))
            _LOGGER.debug("Sent WORK_TIME_FREQUENCY for device %s", device_id)
        except Exception as e:
            _LOGGER.error("Failed to send WORK_TIME_FREQUENCY: %s", e)