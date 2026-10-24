# Anker BLE Power Bank for Home Assistant

This custom integration reads the Anker A110A Prime Power Bank over local Bluetooth and the A2345 250W Prime Charger through the Anker cloud MQTT channel. It exposes battery/connection entities for BLE and per-port voltage, current, and power telemetry for the Wi-Fi charger.

## Installation

1. Add `https://github.com/orangeboyChen/ha-anker` as a custom repository in HACS (Integration).
2. Install **Anker BLE Power Bank** and restart Home Assistant.
3. Add the integration from **Settings → Devices & services**. Nearby FF09 devices are discovered automatically; manual Bluetooth-address setup is also available. The cloud path is designed to accept an Anker email/password, reuse cached accounts, refresh sessions, and then let one account add multiple devices.
4. Keep the power bank in Bluetooth range of the Home Assistant host or a Bluetooth proxy.

The BLE path uses the local HA Bluetooth stack. The A2345 cloud path asks for an Anker account during setup; it does not require the mobile app or HAR session data.

## Current support

- A110A BLE: GATT discovery, authenticated FF09 GCM session, battery snapshot and live notification updates.
- Manual connection controls: Connect resumes updates; Disconnect closes GATT and pauses automatic reconnection.
- `raw_fields` preserves received telemetry TLVs without guessing their meaning or scale.
- A2345 cloud MQTT: mTLS credentials are obtained after account login, a read-only status request is published, and six USB port telemetry groups are exposed. Each port has raw status plus voltage, current, and power; total current, total power, and a non-zero status count are also derived from complete port groups. A diagnostic raw-telemetry entity preserves every response field for later protocol mapping. The MQTT session and temporary certificate material remain in memory only.
- Cloud account persistence and multi-device selection are implemented in `cloud_auth.py` and `config_flow.py`. Passwords are never stored; a password entered during the current HA process is reused for token renewal. If the session becomes invalid, the cached account is removed and must be added again.
- Only the observed A2345 status request and telemetry response are implemented. Charging-mode, port-switch, timer, and other write commands are intentionally not exposed until their payloads are independently verified.

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
