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
        "version": "1",
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
    return json.dumps({"head": head, "payload": payload}, separators=(",", ":")).encode()


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
    fields: dict[int, bytes] = {}
    offset = 1 if body[:1] == b"\x00" else 0
    while offset < len(body):
        if offset + 3 > len(body):
            raise ValueError("truncated A2345 field header")
        field_id = body[offset]
        length = int.from_bytes(body[offset + 1 : offset + 3], "little")
        offset += 3
        if length < 1 or offset + length > len(body):
            raise ValueError("truncated A2345 field")
        # A2345 fields carry a one-byte field type before their value.
        fields[field_id] = body[offset + 1 : offset + length]
        offset += length
    result: dict[str, object] = {
        "command": command,
        "raw_fields": {f"0x{key:02x}": raw.hex() for key, raw in fields.items()},
    }
    ports = ("usbc_1", "usbc_2", "usbc_3", "usbc_4", "usba_1", "usba_2")
    parsed_ports = 0
    total_current = 0.0
    total_power = 0.0
    for field_id, port in zip(range(0xA2, 0xA8), ports, strict=False):
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
