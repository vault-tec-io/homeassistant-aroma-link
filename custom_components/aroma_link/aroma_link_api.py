import asyncio
import hashlib
import json
import logging
import time
from typing import List, Optional

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


class _SessionContext:
    """Context manager wrapper for shared session that doesn't close on exit."""
    def __init__(self, session: aiohttp.ClientSession):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # Don't close the shared session
        pass


class AromaLinkClient:
    """API Client for Aroma-Link devices."""
    def __init__(self, username: str, password: str = None, access_token: str = None, session: aiohttp.ClientSession = None):
        self.username = username
        self.password = password
        self.hashed_password = hashlib.md5(password.encode()).hexdigest() if password else None
        self.access_token = access_token
        self.refresh_token = None
        self.user_id = None
        self.devices: List[AromaLinkDevice] = []
        self._callbacks = []
        self.ws_tasks = {}  # device_id -> task
        self._ws_connections = {}  # device_id -> websocket
        self._ws_connected = {}  # device_id -> bool
        self._session = session  # Optional shared aiohttp session
        # Per-device state
        self._device_state = {}  # device_id -> {current_phase, work_remain_time, pause_remain_time, work_time, pause_time, waiting_for_response}

    def set_session(self, session: aiohttp.ClientSession):
        """Set the aiohttp session (called after HA setup)."""
        self._session = session

    def _get_session_context(self):
        """Get session context manager - reuse shared session or create new one."""
        if self._session:
            # Return a context manager that doesn't close the shared session
            return _SessionContext(self._session)
        return aiohttp.ClientSession()

    async def login(self) -> bool:
        """Login and get access token."""
        try:
            async with self._get_session_context() as session:
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

    async def refresh_access_token(self) -> bool:
        """Refresh the access token using the refresh token."""
        if not self.refresh_token:
            _LOGGER.warning("No refresh token available, cannot refresh")
            return False

        try:
            async with self._get_session_context() as session:
                data = {"refreshToken": self.refresh_token}
                async with session.post(f"{BASE_URL}/v2/app/token/refresh", data=data) as resp:
                    if resp.status != 200:
                        _LOGGER.warning("Token refresh failed with status %s", resp.status)
                        return False
                    result = await resp.json()
                    if result.get("code") == 200 and result.get("data"):
                        self.access_token = result["data"].get("accessToken", self.access_token)
                        self.refresh_token = result["data"].get("refreshToken", self.refresh_token)
                        _LOGGER.debug("Token refreshed successfully")
                        return True
                    _LOGGER.warning("Token refresh response invalid: %s", result)
                    return False
        except Exception:
            _LOGGER.exception("Token refresh failed")
            return False

    async def get_devices(self) -> List[AromaLinkDevice]:
        """Get list of devices."""
        try:
            async with self._get_session_context() as session:
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
            async with self._get_session_context() as session:
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
            async with self._get_session_context() as session:
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
        schedule_blocks: list = None,
        work_duration: int = None,
        pause_duration: int = None,
    ) -> bool:
        """Set device schedule.

        Args:
            device_id: Device ID
            schedule_blocks: List of schedule block dicts (up to 5 blocks)
                Each block: {
                    "start_time": "07:30",
                    "end_time": "21:30",
                    "work_duration": 10,
                    "pause_duration": 300,
                    "enabled": True,
                    "days": [0,1,2,3,4,5,6]  # Sunday=0
                }
            work_duration: Legacy parameter for simple schedule (deprecated)
            pause_duration: Legacy parameter for simple schedule (deprecated)
        """
        try:
            # Legacy mode: simple schedule with work/pause duration only
            if schedule_blocks is None and (work_duration is not None or pause_duration is not None):
                schedule_blocks = [{
                    "start_time": "00:00",
                    "end_time": "23:59",
                    "work_duration": work_duration or 10,
                    "pause_duration": pause_duration or 120,
                    "enabled": True,
                    "days": [0,1,2,3,4,5,6]
                }]

            if not schedule_blocks:
                _LOGGER.error("No schedule blocks provided")
                return False

            # Build workTimeList - always send exactly 5 blocks
            work_time_list = []
            active_days = set()

            for i in range(5):
                if i < len(schedule_blocks) and schedule_blocks[i].get("enabled", False):
                    block = schedule_blocks[i]
                    work_time_list.append({
                        "startTime": block.get("start_time", "00:00"),
                        "endTime": block.get("end_time", "00:00"),
                        "workDuration": str(block.get("work_duration", 10)),
                        "pauseDuration": str(block.get("pause_duration", 120)),
                        "enabled": 1,
                        "consistenceLevel": 1
                    })
                    # Collect all days from enabled blocks
                    active_days.update(block.get("days", [0,1,2,3,4,5,6]))
                else:
                    # Disabled block with default values
                    work_time_list.append({
                        "startTime": "00:00",
                        "endTime": "00:00",
                        "workDuration": "10",
                        "pauseDuration": "120",
                        "enabled": 0,
                        "consistenceLevel": 1
                    })

            # Use all days that have at least one enabled block
            week_array = sorted(list(active_days)) if active_days else [0,1,2,3,4,5,6]

            data = {
                "deviceId": str(device_id),
                "userId": self.user_id,
                "workTimeList": work_time_list,
                "week": week_array
            }

            async with self._get_session_context() as session:
                headers = {"access_token": self.access_token}
                async with session.post(
                    f"{BASE_URL}/v1/app/data/workSetApp",
                    json=data,
                    headers=headers
                ) as resp:
                    if resp.status == 200:
                        _LOGGER.debug("Schedule updated successfully for device %s", device_id)
                        return True
                    else:
                        _LOGGER.error("Failed to set schedule: HTTP %s", resp.status)
                        return False
        except Exception as e:
            _LOGGER.error("Failed to set schedule: %s", e)
            return False

    def _init_device_state(self, device_id):
        """Initialize state tracking for a device."""
        device_id = str(device_id)  # Ensure consistent string type
        if device_id not in self._device_state:
            self._device_state[device_id] = {
                "current_phase": None,
                "work_remain_time": 0,
                "pause_remain_time": 0,
                "work_time": 0,
                "pause_time": 0,
                "waiting_for_response": False,
                "last_update_time": time.time(),  # Timestamp for drift correction
                "schedule_blocks": [  # Initialize with 5 disabled blocks
                    {
                        "start_time": "00:00",
                        "end_time": "00:00",
                        "work_duration": 10,
                        "pause_duration": 120,
                        "enabled": False,
                        "days": []
                    } for _ in range(5)
                ]
            }

    async def start_websocket(self, device_id):
        """Start WebSocket connection for a device."""
        device_id = str(device_id)  # Ensure consistent string type
        if device_id in self.ws_tasks:
            return
        self._init_device_state(device_id)
        self._ws_connected[device_id] = False
        self.ws_tasks[device_id] = asyncio.create_task(self._websocket_handler(device_id))

    async def _websocket_handler(self, device_id: str):
        """Handle WebSocket connection and messages."""
        backoff = 5
        while True:
            try:
                # Trigger newWork page before WebSocket connection
                async with self._get_session_context() as session:
                    headers = {
                        "access_token": self.access_token,
                        "User-Agent": "KeRuiMa/1.1.3",
                        "Accept": "*/*",
                        "version": "1"
                    }
                    url = f"{BASE_URL}/v1/app/device/newWork/{device_id}?isOpenPage=0&userId={self.user_id}"
                    await session.get(url, headers=headers)

                async with websockets.connect(WS_URL) as websocket:
                    self._ws_connections[device_id] = websocket
                    self._ws_connected[device_id] = True

                    # Start monitoring tasks
                    heartbeat_task = asyncio.create_task(self._heartbeat(device_id))
                    supercommand_task = asyncio.create_task(self._supercommand_monitor(device_id))
                    countdown_task = asyncio.create_task(self._countdown_monitor(device_id))

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
                _LOGGER.error("WebSocket error for device %s: %s", device_id, e)
                self._ws_connected[device_id] = False
                self._ws_connections.pop(device_id, None)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 300)  # Cap at 5 minutes

    async def _heartbeat(self, device_id: str):
        """Send heartbeat messages."""
        while self._ws_connected.get(device_id, False):
            try:
                ws = self._ws_connections.get(device_id)
                if not ws:
                    break
                message = {
                    "type": "HEARTBEAT",
                    "data": "{}",
                    "deviceId": device_id
                }
                await ws.send(json.dumps(message))
                await asyncio.sleep(10)
            except Exception as e:
                _LOGGER.error("Heartbeat error for device %s: %s", device_id, e)
                break

    async def _delayed_supercommand(self, device_id: str, delay: float):
        """Send SUPERCOMMAND after a delay (for phase transitions)."""
        await asyncio.sleep(delay)
        await self._send_supercommand(device_id)

    async def _send_supercommand(self, device_id: str):
        """Send SUPERCOMMAND message with trigger."""
        try:
            ws = self._ws_connections.get(device_id)
            if not ws:
                _LOGGER.error("No WebSocket connection for device %s", device_id)
                return

            # Always trigger newWork before SUPERCOMMAND
            async with self._get_session_context() as session:
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
            await ws.send(json.dumps(message))
            self._device_state[device_id]["waiting_for_response"] = True
            _LOGGER.debug("Sent SUPERCOMMAND for device %s", device_id)
        except Exception as e:
            _LOGGER.error("Failed to send SUPERCOMMAND for device %s: %s", device_id, e)

    async def _handle_message(self, message: str, device_id: str):
        """Handle incoming WebSocket messages."""
        try:
            if message == "连接成功":
                _LOGGER.debug("WebSocket connection successful for device %s", device_id)
                return

            _LOGGER.debug("Received WebSocket message for device %s: %s", device_id, message)

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

            # Parse nested JSON strings in data field
            if "data" in data and isinstance(data["data"], str):
                try:
                    data["data"] = json.loads(data["data"])
                except Exception:
                    _LOGGER.error("Failed to decode data field: %s", data["data"])
                    return

            msg_type = data.get("type")

            if msg_type == "WORK_TIME_FREQUENCY":
                # Schedule data received
                schedule_data = data.get("data")
                _LOGGER.debug("WORK_TIME_FREQUENCY data type: %s, is_list: %s",
                            type(schedule_data).__name__, isinstance(schedule_data, list))

                if isinstance(schedule_data, list):
                    # Parse schedule blocks
                    schedule_blocks = []
                    for block in schedule_data:
                        parsed_block = {
                            "start_time": block.get("startHour", "00:00"),
                            "end_time": block.get("endHour", "00:00"),
                            "work_duration": block.get("workSec", 10),
                            "pause_duration": block.get("pauseSec", 120),
                            "enabled": block.get("enabled", 0) == 1,
                            "consistency_level": block.get("consistenceLevel", 1),
                            "week_day": block.get("weekDay", 0),
                        }
                        schedule_blocks.append(parsed_block)
                        _LOGGER.debug("Parsed schedule block: %s", parsed_block)

                    # Store in device state
                    state = self._device_state.get(device_id, {})
                    state["schedule_blocks"] = schedule_blocks
                    state["schedule_fetched"] = True
                    _LOGGER.debug("Stored %d schedule blocks in device state for device %s",
                                len(schedule_blocks), device_id)
                else:
                    _LOGGER.warning("WORK_TIME_FREQUENCY data is not a list: %s", schedule_data)

            elif msg_type == "SUPERCOMMAND":
                device_data = data.get("data", {})

                if str(device_data.get("deviceId")) == str(device_id):
                    state = self._device_state[device_id]

                    # Get raw countdown values from server
                    work_remain_raw = device_data.get("workRemainTime", 0)
                    pause_remain_raw = device_data.get("pauseRemainTime", 0)

                    # Calculate elapsed time using both updateTime and sendTime
                    receive_time_ms = time.time() * 1000  # Current local time in ms
                    send_time_ms = data.get("sendTime")  # When server sent the message
                    update_time_ms = device_data.get("updateTime")  # When device state was updated

                    # Apply time adjustment if timestamps are valid
                    if send_time_ms and update_time_ms:
                        # How old was the state when the server sent it
                        state_age_ms = send_time_ms - update_time_ms
                        # Network transmission delay
                        network_delay_ms = receive_time_ms - send_time_ms
                        # Total elapsed time
                        total_elapsed_ms = state_age_ms + network_delay_ms

                        # Safety checks for clock desync
                        if network_delay_ms < 0:
                            _LOGGER.warning(
                                "Clock desync detected (negative network delay: %.3fs), using raw values",
                                network_delay_ms / 1000.0
                            )
                            total_elapsed_sec = 0
                        elif network_delay_ms > 5000:
                            _LOGGER.warning(
                                "Excessive network delay detected (%.3fs), possible clock desync, using raw values",
                                network_delay_ms / 1000.0
                            )
                            total_elapsed_sec = 0
                        else:
                            total_elapsed_sec = total_elapsed_ms / 1000.0
                            _LOGGER.debug(
                                "Time adjustment for device %s: state_age=%.3fs, network_delay=%.3fs, total=%.3fs",
                                device_id,
                                state_age_ms / 1000.0,
                                network_delay_ms / 1000.0,
                                total_elapsed_sec
                            )
                    else:
                        _LOGGER.debug("Missing timestamps, using raw countdown values")
                        total_elapsed_sec = 0

                    # Update timestamp when we receive server data
                    state["last_update_time"] = time.time()
                    state["work_time"] = device_data.get("workTime", 0)
                    state["pause_time"] = device_data.get("pauseTime", 0)

                    # Adjust countdown values for elapsed time
                    state["work_remain_time"] = max(0, work_remain_raw - total_elapsed_sec)
                    state["pause_remain_time"] = max(0, pause_remain_raw - total_elapsed_sec)

                    state["current_phase"] = "work" if device_data.get("workStatus") == 1 else "pause"
                    state["waiting_for_response"] = False

                    _LOGGER.debug(
                        "Updated state for device %s: phase=%s, work_remain=%s (raw=%s), pause_remain=%s (raw=%s)",
                        device_id,
                        state["current_phase"],
                        state["work_remain_time"],
                        work_remain_raw,
                        state["pause_remain_time"],
                        pause_remain_raw,
                    )

            # Notify all callbacks
            for callback in self._callbacks:
                if isinstance(data, dict):
                    await callback(data)
                else:
                    _LOGGER.error("Callback data is not a dict: %s", data)
        except Exception as e:
            _LOGGER.exception("Failed to handle message for device %s: %s", device_id, e)

    async def _supercommand_monitor(self, device_id: str):
        """Monitor and send SUPERCOMMAND at appropriate intervals."""
        sent_before_pause_ends = False
        sent_before_work_ends = False
        sent_after_pause_starts = False

        while self._ws_connected.get(device_id, False):
            try:
                state = self._device_state.get(device_id, {})
                if not state.get("waiting_for_response", False):
                    current_phase = state.get("current_phase")
                    pause_remain = state.get("pause_remain_time", 0)
                    work_remain = state.get("work_remain_time", 0)
                    pause_time = state.get("pause_time", 0)
                    work_time = state.get("work_time", 0)

                    if current_phase == "pause":
                        # Send SUPERCOMMAND 1 second before pause ends
                        if pause_remain == 1 and not sent_before_pause_ends:
                            await self._send_supercommand(device_id)
                            sent_before_pause_ends = True

                        # Send SUPERCOMMAND 1 second after pause starts
                        if pause_remain == pause_time - 1 and not sent_after_pause_starts:
                            await self._send_supercommand(device_id)
                            sent_after_pause_starts = True

                    elif current_phase == "work":
                        # Send SUPERCOMMAND 1 second before work ends
                        if work_remain == 1 and not sent_before_work_ends:
                            await self._send_supercommand(device_id)
                            sent_before_work_ends = True

                    # Reset flags when transitioning between phases
                    if current_phase == "pause" and pause_remain > 1 and pause_remain < pause_time - 1:
                        sent_before_pause_ends = False
                        sent_after_pause_starts = False

                    if current_phase == "work" and work_remain > 1 and work_remain < work_time - 1:
                        sent_before_work_ends = False

                await asyncio.sleep(1)
            except Exception as e:
                _LOGGER.error("Error in SUPERCOMMAND monitor for device %s: %s", device_id, e)
                await asyncio.sleep(1)

    async def _countdown_monitor(self, device_id: str):
        """Monitor and update countdown timers for a specific device using timestamp-based calculations."""
        last_countdown_value = None  # Track when countdown hits 0

        while self._ws_connected.get(device_id, False):
            try:
                state = self._device_state.get(device_id)
                if not state:
                    await asyncio.sleep(0.5)
                    continue

                current_phase = state.get("current_phase")
                if not current_phase:
                    await asyncio.sleep(0.5)
                    continue

                # Calculate elapsed time since last server update
                elapsed = time.time() - state.get("last_update_time", time.time())

                # Get base values from server
                work_remain_base = state.get("work_remain_time", 0)
                pause_remain_base = state.get("pause_remain_time", 0)
                work_time = state.get("work_time", 0)
                pause_time = state.get("pause_time", 0)

                # Only countdown the ACTIVE phase, inactive phase shows configured duration
                if current_phase == "work":
                    # Work is counting down, pause shows full duration
                    work_countdown = max(0, int(work_remain_base - elapsed))
                    pause_countdown = pause_time
                    active_countdown = work_countdown
                else:  # pause
                    # Pause is counting down, work shows full duration
                    work_countdown = work_time
                    pause_countdown = max(0, int(pause_remain_base - elapsed))
                    active_countdown = pause_countdown

                # Request fresh state when countdown hits 0 to get phase transition
                # Schedule request asynchronously without blocking countdown updates
                if active_countdown == 0 and last_countdown_value != 0:
                    _LOGGER.debug("Countdown hit 0 for device %s, scheduling state refresh in 2s", device_id)
                    asyncio.create_task(self._delayed_supercommand(device_id, 2))

                last_countdown_value = active_countdown

                # Notify callbacks with calculated countdown values
                callback_data = {
                    "type": "COUNTDOWN",
                    "data": {
                        "deviceId": device_id,
                        "workStatus": 1 if current_phase == "work" else 0,
                        "workRemainTime": work_countdown,
                        "pauseRemainTime": pause_countdown,
                    }
                }

                for callback in self._callbacks:
                    await callback(callback_data)

                await asyncio.sleep(1)  # Update every second
            except Exception as e:
                _LOGGER.error("Error in countdown monitor for device %s: %s", device_id, e)
                await asyncio.sleep(1)

    async def stop_all_websockets(self):
        """Stop all WebSocket connections and cancel tasks."""
        for device_id in list(self.ws_tasks.keys()):
            await self.stop_websocket(device_id)

    async def stop_websocket(self, device_id):
        """Stop WebSocket connection for a specific device."""
        device_id = str(device_id)  # Ensure consistent string type
        # Mark as disconnected to stop loops
        self._ws_connected[device_id] = False

        # Cancel the task
        task = self.ws_tasks.pop(device_id, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Close the WebSocket connection
        ws = self._ws_connections.pop(device_id, None)
        if ws:
            try:
                await ws.close()
            except Exception:
                pass

        # Clean up device state
        self._device_state.pop(device_id, None)
        _LOGGER.debug("Stopped WebSocket for device %s", device_id)

    def is_device_available(self, device_id: str) -> bool:
        """Check if a device's WebSocket connection is active."""
        return self._ws_connected.get(str(device_id), False)

    def add_callback(self, callback):
        """Add callback for WebSocket messages."""
        self._callbacks.append(callback)

    def remove_callback(self, callback):
        """Remove callback for WebSocket messages."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    async def get_schedule(self, device_id: str, day_of_week: int = None) -> Optional[list]:
        """Get all schedule blocks for a device for a specific day.

        Args:
            device_id: Device ID
            day_of_week: Day of week (0=Sunday, 6=Saturday). If None, uses current day.

        Returns list of schedule blocks for the day or None if failed.
        """
        device_id = str(device_id)

        # Use current day if not specified
        if day_of_week is None:
            day_of_week = int(time.strftime("%w"))  # Sunday=0

        try:
            # Clear previous schedule data
            state = self._device_state.get(device_id, {})
            state["schedule_fetched"] = False
            state["schedule_blocks"] = []

            # Trigger REST API - response comes via WebSocket
            url = f"{BASE_URL}/v1/app/device/newWorkTime/{device_id}?userId={self.user_id}&week={day_of_week}"
            headers = {
                "access_token": self.access_token,
                "User-Agent": "KeRuiMa/1.1.3",
                "Accept": "*/*",
                "version": "1"
            }

            async with self._get_session_context() as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        _LOGGER.error("Failed to trigger schedule fetch: %s", resp.status)
                        return None

                    # Response is just {code: 200, msg: "OK"}
                    # Actual data comes via WebSocket WORK_TIME_FREQUENCY message

            # Wait for WebSocket response (max 5 seconds)
            _LOGGER.debug("Waiting for WebSocket WORK_TIME_FREQUENCY response...")
            for i in range(50):  # 50 * 0.1s = 5 seconds
                await asyncio.sleep(0.1)
                if state.get("schedule_fetched", False):
                    schedule_blocks = state.get("schedule_blocks", [])
                    _LOGGER.debug("WebSocket response received with %d blocks", len(schedule_blocks))

                    # Ensure we have exactly 5 blocks
                    while len(schedule_blocks) < 5:
                        schedule_blocks.append({
                            "start_time": "00:00",
                            "end_time": "00:00",
                            "work_duration": 10,
                            "pause_duration": 120,
                            "enabled": False,
                            "consistency_level": 1,
                            "days": []
                        })

                    # Add day information to each block
                    for block in schedule_blocks[:5]:
                        if "days" not in block:
                            block["days"] = [day_of_week] if block.get("enabled") else []

                    _LOGGER.debug("Schedule retrieved for device %s, day %s: %s blocks",
                                device_id, day_of_week, len(schedule_blocks))
                    return schedule_blocks[:5]

            _LOGGER.error("Timeout waiting for schedule data from WebSocket for device %s", device_id)
            return None

        except Exception as e:
            _LOGGER.error("Error fetching schedule: %s", e)
            return None

    async def get_schedule_for_day(self, device_id: str, day_of_week: int) -> Optional[dict]:
        """Get schedule for a specific day (Sunday=0). DEPRECATED - use get_schedule() instead."""
        try:
            url = f"{BASE_URL}/v1/app/device/newWorkTime/{device_id}?userId={self.user_id}&week={day_of_week}"
            headers = {
                "access_token": self.access_token,
                "User-Agent": "KeRuiMa/1.1.3",
                "Accept": "*/*",
                "version": "1"
            }
            async with self._get_session_context() as session:
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
            ws = self._ws_connections.get(device_id)
            if not ws:
                _LOGGER.error("No WebSocket connection for device %s", device_id)
                return
            message = {
                "type": "WORK_TIME_FREQUENCY",
                "data": "{}",
                "deviceId": device_id
            }
            await ws.send(json.dumps(message))
            _LOGGER.debug("Sent WORK_TIME_FREQUENCY for device %s", device_id)
        except Exception as e:
            _LOGGER.error("Failed to send WORK_TIME_FREQUENCY for device %s: %s", device_id, e)