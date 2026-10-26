"""Offline codec for the verified Anker Wi-Fi charger MQTT envelope."""

from __future__ import annotations

import base64
import binascii
import json
import time
from typing import Any

COMMAND_TYPES = {
    "normalCommand": 34,
    "otaCommand": 12,
    "a17y0OtaCommand": 12,
    "uploadLogCommand": 10,
    "remoteScanWifi": 50,
    "remoteSwitchWifi": 52,
    "unbindDevice": 24,
    "getDeviceBssid": 56,
}


def command_topic(device_pn: str, device_sn: str) -> str:
    """Return the Anker command topic recovered from the APK."""
    return f"cmd/anker_power/{device_pn}/{device_sn}/req"


def response_topic(device_pn: str, device_sn: str) -> str:
    """Return the Anker response topic recovered from the APK."""
    return f"cmd/anker_power/{device_pn}/{device_sn}/app/res"


def encode_envelope(
    *,
    device_pn: str,
    device_sn: str,
    account_id: str,
    client_id: str,
    data: bytes | None = None,
    command_type: str = "normalCommand",
    timestamp: int | None = None,
) -> bytes:
    """Encode the confirmed outer JSON schema, without inventing model payload bytes."""
    if command_type not in COMMAND_TYPES:
        raise ValueError(f"unknown command type: {command_type}")
    if command_type == "normalCommand" and data is None:
        raise ValueError("normalCommand requires observed data bytes")
    if command_type != "normalCommand" and data is not None:
        raise ValueError(f"{command_type} does not carry data")
    payload: dict[str, Any] = {"device_sn": device_sn, "account_id": account_id}
    if data is not None:
        payload["data"] = base64.b64encode(data).decode()
    if command_type == "a17y0OtaCommand":
        payload["ota_type"] = "A17Y0"
    if command_type == "uploadLogCommand":
        payload["log_id"] = (
            f"{device_sn} {timestamp if timestamp is not None else int(time.time())}"
        )
    head = {
        # Match the four-component protocol version observed in the app's
        # status and command envelopes.  A shorter ``1`` is accepted by our
        # JSON decoder but is not the app-observed wire value.
        "version": "1.0.0.1",
        "client_id": client_id,
        "sess_id": "1",
        "msg_seq": 2,
        "cmd": COMMAND_TYPES[command_type],
        "cmd_status": 4,
        "sign_code": 2,
        "seed": "1",
        "timestamp": timestamp if timestamp is not None else int(time.time()),
        "device_pn": device_pn,
        "device_sn": device_sn,
    }
    # The APK's MqttCommandTransformer calls jsonEncode on the common payload
    # before placing it under the outer ``payload`` key.  Keep that wire shape
    # for both read and write commands; an embedded object is silently ignored
    # by the charger even though it is valid JSON.
    return json.dumps(
        {"head": head, "payload": json.dumps(payload, separators=(",", ":"))},
        separators=(",", ":"),
    ).encode()


def decode_envelope(value: bytes | str) -> dict[str, Any]:
    """Decode and validate an Anker MQTT JSON envelope."""
    try:
        message = json.loads(value)
    except (TypeError, json.JSONDecodeError) as err:
        raise ValueError("invalid MQTT JSON") from err
    if not isinstance(message, dict):
        raise ValueError("MQTT message must be an object")
    payload = message.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as err:
            raise ValueError("MQTT payload is not valid JSON") from err
        message["payload"] = payload
    if not isinstance(message.get("head"), dict) or not isinstance(message.get("payload"), dict):
        raise ValueError("MQTT message must contain head and payload objects")
    data = message["payload"].get("data")
    if data is not None:
        if not isinstance(data, str):
            raise ValueError("payload.data must be base64 text")
        try:
            message["data"] = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError) as err:
            raise ValueError("payload.data is not valid base64") from err
    return message


def encode_mqtt_zx_frame(
    *, group: int, command: int, body: bytes = b"", encrypted: bool = False
) -> bytes:
    """Encode the unencrypted ZX frame used by the A2345 MQTT status request."""
    frame = bytearray(10 + len(body))
    frame[:2] = b"\xff\x09"
    frame[2:4] = len(frame).to_bytes(2, "little")
    frame[4:7] = bytes((3, 0, group))
    frame[7:9] = (command | (0x4000 if encrypted else 0)).to_bytes(2, "big")
    frame[9:-1] = body
    for byte in frame[:-1]:
        frame[-1] ^= byte
    return bytes(frame)


def encode_mqtt_tlvs(items: list[tuple[int, bytes]]) -> bytes:
    """Encode the two-byte-length TLVs used by the Wi-Fi command body."""
    result = bytearray()
    for index, value in items:
        if not 0 <= index <= 255 or len(value) > 255:
            raise ValueError("MQTT TLV index or value length is out of range")
        result.extend((index, len(value)))
        result.extend(value)
    return bytes(result)


def encode_a2345_status_request(*, timestamp: int | None = None) -> bytes:
    """Build the verified read-only A2345 status request."""
    now = timestamp if timestamp is not None else int(time.time())
    body = encode_mqtt_tlvs([(0xA1, b"\x22"), (0xFE, b"\x03" + now.to_bytes(4, "little"))])
    return encode_mqtt_zx_frame(group=0x0F, command=0x0200, body=body)


def encode_a2345_setting_frame(
    *,
    items: list[tuple[int, bytes]],
    timestamp: int | None = None,
    is_ble: bool = False,
    is_more_length: bool = False,
) -> bytes:
    """Encode the A2345 setting payload recovered from ZXCommandTransformer.

    ``A2345DeviceController.sendCommandWithParam`` defaults ``isMoreLength``
    to ``false``.  The decompiled transformer consequently calls
    ``CmdUtil.intToList2`` for the trailing ``0x1fc`` length field (the
    resulting ``Uint8List`` exposes its low byte as ``0xfc``).  The one-byte
    form is retained for callers that explicitly pass ``isMoreLength=true``.
    """
    if not items:
        raise ValueError("at least one A2345 setting is required")
    if len({key for key, _value in items}) != len(items):
        raise ValueError("A2345 setting keys must be unique")
    if any(not 0 <= key <= 0xFFFF for key, _value in items):
        raise ValueError("A2345 setting key is out of range")
    if any(not value or len(value) > 120 for _key, value in items):
        raise ValueError("A2345 setting values must contain 1 to 120 bytes")

    # AOT emits the key's low byte, then value length + 1 and a type derived
    # from the length. The setting key itself is not present on the wire.
    payload = bytearray((0x42, 0x02, 0x42 if is_ble else 0x44))
    for key, value in sorted(items):
        value_type = {1: 1, 2: 2, 4: 3}.get(len(value), 4)
        payload.extend((key & 0xFF, len(value) + 1, value_type))
        payload.extend(value)

    now = timestamp if timestamp is not None else int(time.time())
    timestamp_bytes = now.to_bytes(4, "little", signed=False)
    payload_length = len(payload) + len(timestamp_bytes)
    length_width = 1 if is_more_length else 2
    if payload_length >= 1 << (8 * length_width):
        raise ValueError("A2345 setting payload exceeds the selected length encoding")
    payload.append(0xFC)
    payload.extend(payload_length.to_bytes(length_width, "little"))
    payload.extend(timestamp_bytes)
    return bytes(payload)


def encode_a2345_setting_envelope(
    *,
    device_pn: str,
    device_sn: str,
    account_id: str,
    client_id: str,
    items: list[tuple[int, bytes]],
    timestamp: int | None = None,
) -> bytes:
    """Wrap a setting byte sequence in the APK's normal MQTT command envelope."""
    now = timestamp if timestamp is not None else int(time.time())
    command = encode_a2345_setting_frame(items=items, timestamp=now)
    return encode_envelope(
        device_pn=device_pn,
        device_sn=device_sn,
        account_id=account_id,
        client_id=client_id,
        data=command,
        timestamp=now,
    )


def encode_a2345_set_port_switch(
    *, port: int, enabled: bool, timestamp: int | None = None
) -> bytes:
    """Build the APK's DC relay setting map for one zero-based port index."""
    if not 0 <= port <= 255:
        raise ValueError("A2345 port index must fit in one byte")
    return encode_a2345_setting_frame(
        items=[(0x0144, bytes((port,))), (0x0146, bytes((int(enabled),)))],
        timestamp=timestamp,
    )


def encode_a2345_set_charging_mode(*, mode: int, timestamp: int | None = None) -> bytes:
    """Build a non-custom A2345 charging-mode setting map."""
    if not 0 <= mode <= 255 or mode == 5:
        raise ValueError("mode must be a one-byte non-custom A2345 mode")
    return encode_a2345_setting_frame(items=[(0x0144, bytes((mode,)))], timestamp=timestamp)


def encode_a2345_set_port_priority(*, mask: int, timestamp: int | None = None) -> bytes:
    """Build the four-port A2345 priority bitmask setting map."""
    if not 0 <= mask <= 0x0F:
        raise ValueError("A2345 priority mask must use only its low four bits")
    return encode_a2345_setting_frame(items=[(0x0144, bytes((mask,)))], timestamp=timestamp)


def encode_a2345_set_ufcs(*, enabled: bool, timestamp: int | None = None) -> bytes:
    """Build the A2345 UFCS setting map."""
    return encode_a2345_setting_frame(items=[(0x0144, bytes((int(enabled),)))], timestamp=timestamp)


def encode_status_envelope(
    *,
    device_pn: str,
    device_sn: str,
    account_id: str,
    client_id: str,
    timestamp: int | None = None,
) -> bytes:
    """Encode the app-compatible read-only MQTT command envelope."""
    now = timestamp if timestamp is not None else int(time.time())
    frame = encode_a2345_status_request(timestamp=now)
    payload = {
        "device_sn": device_sn,
        "account_id": account_id,
        "data": base64.b64encode(frame).decode(),
    }
    head = {
        "version": "1.0.0.1",
        "client_id": client_id,
        "sess_id": "1234-5678",
        "msg_seq": 1,
        "seed": 1,
        "timestamp": now,
        "cmd_status": 2,
        "cmd": 17,
        "sign_code": 1,
        "device_pn": device_pn,
        "device_sn": device_sn,
    }
    return json.dumps(
        {"head": head, "payload": json.dumps(payload, separators=(",", ":"))},
        separators=(",", ":"),
    ).encode()


def decode_mqtt_zx_frame(value: bytes) -> tuple[int, int, bytes]:
    """Validate an MQTT ZX frame and return ``(group, command, body)``."""
    if len(value) < 10 or value[:2] != b"\xff\x09":
        raise ValueError("invalid MQTT ZX frame header")
    if int.from_bytes(value[2:4], "little") != len(value):
        raise ValueError("MQTT ZX frame length mismatch")
    checksum = 0
    for byte in value:
        checksum ^= byte
    if checksum:
        raise ValueError("MQTT ZX checksum mismatch")
    command = int.from_bytes(value[7:9], "big") & 0x3FFF
    return value[6], command, value[9:-1]


def decode_a2345_telemetry(value: bytes) -> dict[str, object]:
    """Decode verified A2345 ``0303`` port telemetry without guessing settings."""
    _group, command, body = decode_mqtt_zx_frame(value)
    if command not in (0x0303, 0x0A00):
        raise ValueError(f"unsupported A2345 telemetry command: 0x{command:04x}")

    def parse_fields(length_width: int) -> dict[int, bytes]:
        fields: dict[int, bytes] = {}
        offset = 1 if body[:1] == b"\x00" else 0
        while offset < len(body):
            header_size = 1 + length_width
            if offset + header_size > len(body):
                raise ValueError("truncated A2345 field header")
            field_id = body[offset]
            if length_width == 1:
                length = body[offset + 1]
            else:
                length = int.from_bytes(body[offset + 1 : offset + 3], "little")
            offset += header_size
            if length < 1 or offset + length > len(body):
                raise ValueError("truncated A2345 field")
            # A2345 fields carry a one-byte field type before their value.
            fields[field_id] = body[offset + 1 : offset + length]
            offset += length
        return fields

    try:
        one_byte_fields = parse_fields(1)
    except ValueError:
        one_byte_fields = None
    try:
        two_byte_fields = parse_fields(2)
    except ValueError:
        two_byte_fields = None
    if one_byte_fields is None and two_byte_fields is None:
        raise ValueError("invalid A2345 field payload")
    if one_byte_fields is not None and two_byte_fields is not None:
        # Legacy captures encode a zero high length byte; current param_info
        # frames start with a nonzero type/value byte after the one-byte size.
        first = 1 if body[:1] == b"\x00" else 0
        fields = (
            two_byte_fields if first + 2 < len(body) and body[first + 2] == 0 else one_byte_fields
        )
    else:
        fields = one_byte_fields or two_byte_fields or {}
    result: dict[str, object] = {
        "command": command,
        "raw_fields": {f"0x{key:02x}": raw.hex() for key, raw in fields.items()},
    }
    if command == 0x0A00 and (mode := fields.get(0xA2)) and len(mode) <= 4:
        decoded_mode = int.from_bytes(mode, "little")
        # A2345 all-info responses can carry a two-byte A2 structure (for
        # example 44 08), which is not the 0..3 charging-mode enum.
        if decoded_mode in range(4):
            result["charging_mode"] = decoded_mode
    if (priority := fields.get(0xA3)) and len(priority) <= 2:
        decoded_priority = int.from_bytes(priority, "little")
        if decoded_priority <= 0x0F:
            result["port_priority_mask"] = decoded_priority
    ports = ("usbc_1", "usbc_2", "usbc_3", "usbc_4", "usba_1", "usba_2")
    # Current A2345 param_info responses use A4..A9 for the six physical
    # ports.  Older captures used A2..A7; retain that fallback for fixtures
    # and older firmware while preferring the current wire layout.
    port_field_ids = (
        range(0xA4, 0xAA)
        if any(field_id in fields for field_id in range(0xA4, 0xAA))
        else range(0xA2, 0xA8)
    )
    parsed_ports = 0
    total_current = 0.0
    total_power = 0.0
    for field_id, port in zip(port_field_ids, ports, strict=False):
        raw = fields.get(field_id)
        if raw is None or len(raw) < 7:
            continue
        parsed_ports += 1
        voltage = int.from_bytes(raw[1:3], "little") / 1000
        current = int.from_bytes(raw[3:5], "little") / 1000
        power = int.from_bytes(raw[5:7], "little") / 100
        result[f"{port}_status"] = raw[0]
        result[f"{port}_voltage"] = voltage
        result[f"{port}_current"] = current
        result[f"{port}_power"] = power
        total_current += current
        total_power += power
    if parsed_ports:
        # These are sums of the independently decoded port measurements.  Do
        # not expose a value when no complete port group was received.
        result["total_current"] = round(total_current, 3)
        result["total_power"] = round(total_power, 2)
        result["nonzero_status_count"] = sum(
            1
            for port in ports
            if isinstance(result.get(f"{port}_status"), int) and result[f"{port}_status"] != 0
        )
    return result
