# Anker BLE Power Bank for Home Assistant

This custom integration reads the Anker A110A Prime Power Bank over local Bluetooth and the A2345 250W Prime Charger through the Anker cloud MQTT channel. It exposes battery/connection entities for BLE and per-port voltage, current, and power telemetry for the Wi-Fi charger.

## Installation

1. Add `https://github.com/orangeboyChen/ha-anker` as a custom repository in HACS (Integration).
2. Install **Anker BLE Power Bank** and restart Home Assistant.
3. Add the integration from **Settings → Devices & services**, sign in with an Anker account, then choose a supported device from the account's device list. A110A entries use their Bluetooth address supplied by Anker; cloud chargers use MQTT. Nearby FF09 discovery remains available as a direct add flow.
4. Keep the power bank in Bluetooth range of the Home Assistant host or a Bluetooth proxy.

The BLE path uses the local HA Bluetooth stack. The A2345 cloud path asks for an Anker account during setup; it does not require the mobile app or HAR session data.

## Current support

- A110A BLE: GATT discovery, authenticated FF09 GCM session, battery snapshot and live notification updates. The APK-defined application metrics (total input/output power, C1/C2/USB-A voltage/current/power, health, cycles, and battery/shell temperature) also have stable HA entity IDs, disabled by default, and stay unavailable until the corresponding A110A TLV and conversion are verified.
- Manual connection controls: Connect resumes updates; Disconnect closes GATT and pauses automatic reconnection.
- `raw_fields` preserves received telemetry TLVs without guessing their meaning or scale.
- A2345 cloud MQTT: mTLS credentials are obtained after account login, status is read, and six USB port telemetry groups (C1–C4 plus A1/A2) are exposed. Each group has raw status plus voltage, current, and power. The reference command map provides five relay selectors (C1–C4 plus the shared USB-A rail), the four-port priority mask, and four non-custom charging modes (AI Power, Connection Priority, Dual Laptop, and Low Power); each write is followed by a fresh read-back and fails if the requested state is not observed. A matching read-back is evidence only, not an independent device acknowledgement. Total current, total power, and a non-zero status count are also derived from complete port groups. A diagnostic raw-telemetry entity preserves every response field for later protocol mapping. The MQTT session and temporary certificate material remain in memory only.
- Cloud account persistence and multi-device selection are implemented in `cloud_auth.py` and `config_flow.py`. Passwords are never stored; a password entered during the current HA process is reused for token renewal. If the session becomes invalid, the cached account is removed and must be added again.
- A2345 MQTT connection, status reads, relay/priority/mode command encoding, and same-value read-back are implemented from the reference A2345 map. The custom charging profile, timers, screen/theme settings, reset, OTA, Wi-Fi switching, and unbind remain intentionally unavailable; they require additional model-specific fields or are explicitly destructive/network-management actions.

The A110A power/current/voltage/temperature/health/cycle entities are intentionally unavailable until their wire-field IDs and conversion rules are verified. The raw fields remain available on the battery sensor for correlation, and no unknown TLV is interpreted speculatively.

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
