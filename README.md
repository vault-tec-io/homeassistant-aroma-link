# Aroma-Link Diffuser Integration for Home Assistant

[![GitHub Release](https://img.shields.io/github/v/release/vault-tec-io/homeassistant-aroma-link?style=flat-square)](https://github.com/vault-tec-io/homeassistant-aroma-link/releases)
[![License](https://img.shields.io/github/license/vault-tec-io/homeassistant-aroma-link?style=flat-square)](LICENSE)

A custom Home Assistant integration for Aroma-Link WiFi diffusers, providing control and monitoring through the Aroma-Link cloud platform.

## Features

- **Cloud Connectivity**: Connects to Aroma-Link cloud services for device control
- **Real-time Updates**: WebSocket connection provides instant state changes
- **Power Control**: Turn diffusers on/off remotely
- **Fan Control**: Toggle fan mode on supported devices
- **Phase Monitoring**: Track work/pause cycles with countdown timers
- **Scheduling**: Configure work and pause durations
- **Multi-device Support**: Manage multiple diffusers from a single account

## Supported Devices

### Aroma-Link WiFi Diffusers

The integration monitors and controls the following attributes:

- Power state (on/off)
- Fan state (on/off, if supported)
- Current phase (work/pause)
- Work countdown timer (seconds remaining)
- Pause countdown timer (seconds remaining)
- Work duration setting (5-60 seconds)
- Pause duration setting (60-300 seconds)
- Daily schedules (Sunday through Saturday)

## Installation

### HACS (Recommended)

1. Open HACS in your Home Assistant instance
2. Click on "Integrations"
3. Click the three dots menu in the top right corner
4. Select "Custom repositories"
5. Add `https://github.com/vault-tec-io/homeassistant-aroma-link` as a custom repository with category "Integration"
6. Click "Add"
7. Search for "Aroma-Link" and install
8. Restart Home Assistant

### Manual Installation

1. Download the latest release from the [releases page](https://github.com/vault-tec-io/homeassistant-aroma-link/releases)
2. Extract the `aroma_link` folder to your `custom_components` directory:
   ```
   config/custom_components/aroma_link/
   ```
3. Restart Home Assistant
4. Add the integration through the UI

## Configuration

1. Go to **Settings** → **Devices & Services**
2. Click **Add Integration**
3. Search for "Aroma-Link Diffuser"
4. Enter your Aroma-Link account credentials (same as the mobile app)
5. The integration will automatically discover all diffusers on your account

## Entities

### Switches

| Entity | Description |
|--------|-------------|
| Power | Turn the diffuser on or off |
| Fan | Toggle fan mode (only on supported devices) |

### Sensors

| Entity | Description |
|--------|-------------|
| Current Phase | Displays the current operating phase (work or pause) |
| Work Countdown | Seconds remaining in the current work phase |
| Pause Countdown | Seconds remaining in the current pause phase |

### Numbers

| Entity | Range | Description |
|--------|-------|-------------|
| Work Time | 5-60 seconds | Duration of the work (diffusing) phase |
| Pause Time | 60-300 seconds | Duration of the pause phase between diffusing |

### Schedule

Seven schedule entities are created per device, one for each day of the week (Sunday through Saturday). Each entity shows the number of enabled schedule blocks and provides the full schedule in its attributes.

## Usage Examples

### Automation: Turn Off When Leaving Home

```yaml
automation:
  - alias: "Turn off diffuser when leaving"
    trigger:
      - platform: state
        entity_id: person.your_name
        from: "home"
    action:
      - service: switch.turn_off
        target:
          entity_id: switch.living_room_diffuser_power
```

### Automation: Turn On During Evening Hours

```yaml
automation:
  - alias: "Evening diffuser schedule"
    trigger:
      - platform: time
        at: "18:00:00"
    condition:
      - condition: state
        entity_id: person.your_name
        state: "home"
    action:
      - service: switch.turn_on
        target:
          entity_id: switch.living_room_diffuser_power
```

### Dashboard Card: Diffuser Status

```yaml
type: entities
title: Living Room Diffuser
entities:
  - entity: switch.living_room_diffuser_power
  - entity: switch.living_room_diffuser_fan
  - entity: sensor.living_room_diffuser_current_phase
  - entity: sensor.living_room_diffuser_work_countdown
  - entity: sensor.living_room_diffuser_pause_countdown
  - entity: number.living_room_diffuser_work_time
  - entity: number.living_room_diffuser_pause_time
```

## Troubleshooting

### Invalid Credentials

If you receive an authentication error:
1. Verify your username and password work in the Aroma-Link mobile app
2. Check for any special characters in your password that may need escaping
3. Try logging out and back into the mobile app, then retry the integration

### Device Not Appearing

If your diffuser doesn't show up after setup:
1. Ensure the device is online in the Aroma-Link mobile app
2. Check that the device is connected to WiFi
3. Remove and re-add the integration
4. Check Home Assistant logs for error messages

### Connection Issues

If the integration shows as unavailable:
1. Check your internet connection
2. Verify the Aroma-Link cloud service is operational
3. Restart Home Assistant
4. The integration will automatically reconnect with exponential backoff (5s to 300s)

### Countdown Timers Not Updating

The countdown timers are emulated locally based on WebSocket messages. If they seem stuck:
1. Toggle the diffuser power off and on
2. Check the WebSocket connection in Home Assistant logs
3. Restart the integration

## Technical Details

### Architecture

- **API Communication**: REST API for authentication and device control
- **Real-time Updates**: WebSocket connection for live state changes
- **IoT Class**: `cloud_push` - The integration receives push updates from the cloud

### Limitations

- Requires internet connectivity (no local control)
- Depends on Aroma-Link cloud service availability
- Countdown timers are emulated client-side

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## Support

If you encounter issues:

1. Check the [Troubleshooting](#troubleshooting) section
2. Search [existing issues](https://github.com/vault-tec-io/homeassistant-aroma-link/issues)
3. Enable debug logging and collect logs:
   ```yaml
   logger:
     default: info
     logs:
       custom_components.aroma_link: debug
   ```
4. Open a [new issue](https://github.com/vault-tec-io/homeassistant-aroma-link/issues/new) with logs and device details

## Disclaimer

This integration is not affiliated with, endorsed by, or connected to Aroma-Link or its parent company. Use at your own risk.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- Home Assistant community for integration patterns and documentation
- Aroma-Link for creating controllable WiFi diffusers
