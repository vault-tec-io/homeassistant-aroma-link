# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Home Assistant custom integration for Aroma-Link WiFi diffuser devices. It enables control and monitoring of diffusers through Home Assistant's UI using REST API calls and WebSocket connections for real-time updates.

## Development Setup

This is a Home Assistant custom component - there are no build/test/lint commands defined. To develop:

1. Copy the `custom_components/aromalink` folder to your Home Assistant's `custom_components/` directory
2. Restart Home Assistant to load the integration
3. Add the integration through Home Assistant's UI (Settings → Devices & Services → Add Integration)

Dependencies (installed by Home Assistant):
- `websockets>=10.0` - WebSocket client for real-time updates
- `aiohttp>=3.8.0` - Async HTTP client for REST API

## Architecture

### Component Structure

```
custom_components/aromalink/
├── __init__.py          # Integration setup, platform registration
├── aromalink_api.py     # Core API client (REST + WebSocket)
├── config_flow.py       # Credential collection UI flow
├── const.py             # Constants (API URLs, defaults)
├── switch.py            # Power & Fan toggle entities
├── sensor.py            # Phase status & countdown entities
├── number.py            # Work/Pause duration entities
└── schedule.py          # Schedule management entities
```

### Data Flow

1. **config_flow.py** collects credentials and validates via API login
2. **__init__.py** sets up the integration and forwards to entity platforms
3. **aromalink_api.py** (`AromaLinkClient`) manages all device communication:
   - REST API for login, device discovery, and control commands
   - WebSocket for real-time state updates and heartbeat
4. Entity platforms (switch, sensor, number, schedule) register callbacks with the API client to receive updates

### Key Class: AromaLinkClient

The central component managing device state and communication:
- Authenticates and retrieves device list via REST
- Maintains persistent WebSocket connection with reconnection logic (exponential backoff: 5s → 300s)
- Emulates countdown timers client-side (decrements each second)
- Callback system allows entities to subscribe to state changes
- Sends SUPERCOMMAND queries at phase transitions for state sync

### Entity Types

| Platform | Entities | Purpose |
|----------|----------|---------|
| switch | Power, Fan | Toggle device on/off, fan control |
| sensor | Phase, Work Countdown, Pause Countdown | Current state and timer displays |
| number | Work Duration (5-60s), Pause Duration (60-300s) | Adjustable timing settings |
| schedule | Per-day schedules (7 entities) | Work schedule management |

### Authentication

- Uses Aroma-Link cloud API (`https://api.aromaticnet.com`)
- MD5 password hashing for authentication
- Session token stored and refreshed via WebSocket connection
