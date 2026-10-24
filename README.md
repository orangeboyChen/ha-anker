# Anker BLE Power Bank for Home Assistant

This custom integration reads the battery level of the Anker A110A Prime Power Bank over local Bluetooth. It exposes a battery sensor, connection state, explicit connect/disconnect buttons, and raw unclassified telemetry fields for protocol diagnostics. The setup flow also has a cloud-account/device-selection path for the Wi-Fi charger work.

## Installation

1. Add `https://github.com/orangeboyChen/ha-anker` as a custom repository in HACS (Integration).
2. Install **Anker BLE Power Bank** and restart Home Assistant.
3. Add the integration from **Settings → Devices & services**. Nearby FF09 devices are discovered automatically; manual Bluetooth-address setup is also available. The cloud path is designed to accept an Anker email/password, reuse cached accounts, refresh sessions, and then let one account add multiple devices.
4. Keep the power bank in Bluetooth range of the Home Assistant host or a Bluetooth proxy.

The integration uses the local HA Bluetooth stack. It does not need Anker cloud credentials, the mobile app, or HAR session data.

## Current support

- A110A BLE: GATT discovery, authenticated FF09 GCM session, battery snapshot and live notification updates.
- Manual connection controls: Connect resumes updates; Disconnect closes GATT and pauses automatic reconnection.
- `raw_fields` preserves received telemetry TLVs without guessing their meaning or scale.
- The verified Wi-Fi/MQTT outer envelope codec is included in `mqtt_protocol.py`; live Wi-Fi entities are not enabled yet because the broker credentials and model-specific telemetry/control payload for a real charger were not supplied.
- Cloud account persistence and multi-device selection are implemented in `cloud_auth.py` and `config_flow.py`. Passwords are never stored. `async_refresh_expiring_accounts` is ready for the periodic HA coordinator; an authentication failure removes the cached account, while an unavailable/temporarily failing transport keeps it.
- The default cloud transport deliberately raises a clear unavailable error until the APK's encrypted `/passport/login` and refresh exchange is independently verified. It does not send guessed requests.
- Charging-mode selection is not exposed yet. The APK contains mode UI/native bridge routes, but static evidence found so far does not bind a mode command and encoding to A110A. Other model command implementations must not be reused as if verified for A110A.

Power, current, voltage, temperature, health, and cycle-count TLVs are not yet published as individual entities because their A110A wire-field IDs and conversion rules have not been verified. The raw fields remain available on the battery sensor for correlation.

## Development

```sh
uv sync
uv run ruff format .
uv run ruff check .
uv run pytest
```

Protocol tests are offline and never publish controls or connect to a physical device.

## License

MIT. See [LICENSE](LICENSE).

This project is not affiliated with Anker Innovations.
