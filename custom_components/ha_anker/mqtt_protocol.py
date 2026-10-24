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
        payload["log_id"] = f"{device_sn} {timestamp or int(time.time())}"
    head = {
        "version": "1",
        "client_id": client_id,
        "sess_id": "1",
        "msg_seq": 2,
        "cmd": COMMAND_TYPES[command_type],
        "cmd_status": 4,
        "sign_code": 2,
        "seed": "1",
        "timestamp": timestamp or int(time.time()),
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
    if (
        not isinstance(message, dict)
        or not isinstance(message.get("head"), dict)
        or not isinstance(message.get("payload"), dict)
    ):
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
