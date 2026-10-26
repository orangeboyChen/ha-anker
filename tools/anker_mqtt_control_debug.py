"""One-shot MQTT read/no-op control probe for an A2345 charger.

This script is deliberately outside the Home Assistant integration.  It reads
the locally supplied debug export, keeps credentials in memory, and never
prints authentication material.  The control operation is reversible and the
result distinguishes broker publish, explicit ACK, and read-back evidence.
"""

from __future__ import annotations

import argparse
import base64
import json
import queue
import re
import ssl
import sys
import time
from pathlib import Path
from typing import Any

# The script is normally invoked as ``python tools/...`` from the repository
# root.  Keep that invocation working without requiring callers to set
# PYTHONPATH manually.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

from custom_components.ha_anker.mqtt_protocol import (
    decode_mqtt_zx_frame,
    encode_a2345_set_port_priority,
    encode_a2345_set_port_switch,
    encode_envelope,
    encode_status_envelope,
)

DEFAULT_AUTH_PATH = Path.home() / "Downloads" / "anker-auth-debug.json"
DEFAULT_DEVICE_PN = "A2345"
DEFAULT_DEVICE_SN = "ASHUXB1E40500164"


def _endpoint(value: str) -> tuple[str, int]:
    match = re.match(r"^(?:mqtts?://)?([^:/]+)(?::(\d+))?$", value.strip())
    if not match:
        raise ValueError("invalid MQTT endpoint")
    return match.group(1), int(match.group(2) or 8883)


def _read_export(path: Path) -> tuple[str, dict[str, Any], dict[str, Any]]:
    document = json.loads(path.expanduser().read_text(encoding="utf-8"))
    login = document["login_response"]["data"]
    mqtt_info = document["mqtt_session"]
    account_id = str(login.get("user_id") or mqtt_info.get("user_id"))
    if not account_id or account_id == "None":
        raise ValueError("debug export has no user id")
    required = ("endpoint_addr", "certificate_pem", "private_key", "aws_root_ca1_pem")
    if not all(isinstance(mqtt_info.get(key), str) and mqtt_info[key] for key in required):
        raise ValueError("debug export has incomplete MQTT material")
    return account_id, login, mqtt_info


def _decode_envelope(payload: bytes) -> tuple[dict[str, Any], bytes | None]:
    message = json.loads(payload)
    inner = message.get("payload")
    if isinstance(inner, str):
        inner = json.loads(inner)
    if not isinstance(inner, dict):
        return message, None
    encoded = inner.get("data")
    if not isinstance(encoded, str):
        return message, None
    return message, base64.b64decode(encoded, validate=True)


def parse_param_info(frame: bytes) -> dict[str, Any]:
    """Parse the A2345 0A00 response (one-byte field lengths)."""
    _group, command, body = decode_mqtt_zx_frame(frame)
    if command != 0x0A00:
        raise ValueError(f"unexpected telemetry command 0x{command:04x}")
    offset = 1 if body[:1] == b"\x00" else 0
    fields: dict[int, bytes] = {}
    while offset < len(body):
        if offset + 2 > len(body):
            raise ValueError("truncated A2345 field header")
        field_id, length = body[offset], body[offset + 1]
        offset += 2
        if not length or offset + length > len(body):
            raise ValueError("truncated A2345 field")
        fields[field_id] = body[offset : offset + length]
        offset += length

    result: dict[str, Any] = {
        "command": command,
        "body_length": len(body),
        "field_ids": [f"0x{key:02x}" for key in sorted(fields)],
        "raw_fields": {f"0x{key:02x}": value.hex() for key, value in sorted(fields.items())},
    }
    priority = fields.get(0xA3)
    if priority is not None and len(priority) >= 2:
        value = priority[1:]
        decoded = int.from_bytes(value, "little")
        if decoded <= 0x0F:
            result["priority_mask"] = decoded
    ports: list[dict[str, Any]] = []
    for field_id in range(0xA4, 0xAA):
        raw = fields.get(field_id)
        # Each port value is type 0x04 followed by status, mV, mA, and 0.01 W.
        if raw is None or len(raw) < 8 or raw[0] != 0x04:
            continue
        value = raw[1:]
        ports.append(
            {
                "field": f"0x{field_id:02x}",
                "port": field_id - 0xA4,
                "enabled": value[0] != 0,
                "status": value[0],
                "voltage_v": int.from_bytes(value[1:3], "little") / 1000,
                "current_a": int.from_bytes(value[3:5], "little") / 1000,
                "power_w": int.from_bytes(value[5:7], "little") / 100,
            }
        )
    result["ports"] = ports
    return result


def assess_port_write(
    *, before_enabled: bool, after_enabled: bool | None, requested_enabled: bool, ack: bool
) -> dict[str, Any]:
    """Classify a write without treating a matching no-op as confirmation.

    A broker publish only proves that MQTT accepted the packet.  A state
    transition observed by a subsequent param_info read is useful evidence,
    but it is still not an explicit device ACK.  If the state was already the
    requested value, the read-back cannot distinguish an accepted no-op from
    an ignored packet, so that result remains explicitly unconfirmed.
    """
    readback_matches = after_enabled is not None and after_enabled == requested_enabled
    state_changed = before_enabled != requested_enabled and readback_matches
    if ack:
        verification = "acknowledged"
    elif state_changed:
        verification = "state_changed_on_readback"
    elif readback_matches:
        verification = "unconfirmed_no_change"
    else:
        verification = "rejected_or_not_observed"
    return {
        "readback_matches_request": readback_matches,
        "state_changed_to_request": state_changed,
        # Only an independent response can be called confirmation.  A
        # state transition is useful evidence but remains separate.
        "write_confirmed": bool(ack),
        "verification": verification,
    }


def _build_tls_context(mqtt_info: dict[str, Any]) -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.load_verify_locations(cadata=mqtt_info["aws_root_ca1_pem"])
    # paho needs filesystem paths for the client certificate.  Temporary files
    # are created by the caller and removed before this function returns.
    return context


def run_probe(
    *,
    auth_path: Path,
    device_pn: str,
    device_sn: str,
    timeout: float,
    port: int = 0,
    priority_mask: int | None = None,
    desired_enabled: bool | None = None,
    allow_control: bool = True,
) -> dict[str, Any]:
    if not 0 <= port <= 5:
        raise ValueError("port must be between 0 and 5")
    if priority_mask is not None and not 0 <= priority_mask <= 0x0F:
        raise ValueError("priority mask must be between 0 and 15")
    account_id, _login, mqtt_info = _read_export(auth_path)
    host, endpoint_port = _endpoint(mqtt_info["endpoint_addr"])
    client_id = str(mqtt_info.get("thing_name") or mqtt_info.get("user_id") or "anker-debug")

    import tempfile

    context = _build_tls_context(mqtt_info)
    with (
        tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as cert_file,
        tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as key_file,
    ):
        cert_file.write(mqtt_info["certificate_pem"])
        cert_file.flush()
        key_file.write(mqtt_info["private_key"])
        key_file.flush()
        context.load_cert_chain(cert_file.name, key_file.name)

        messages: queue.Queue[tuple[str, bytes]] = queue.Queue()
        seen_messages: list[dict[str, Any]] = []
        connected = queue.Queue()
        client = mqtt.Client(CallbackAPIVersion.VERSION2, client_id=client_id)
        client.tls_set_context(context)
        client.on_connect = lambda _c, _u, _f, reason, _p: connected.put(reason)
        client.on_message = lambda _c, _u, msg: messages.put((str(msg.topic), bytes(msg.payload)))
        client.connect(host, endpoint_port, 60)
        client.loop_start()
        try:
            reason = connected.get(timeout=timeout)
            if reason.is_failure:
                raise ConnectionError(f"MQTT connection failed: {reason}")
            topic_prefix = f"dt/anker_power/{device_pn}/{device_sn}/#"
            ack_prefix = f"cmd/anker_power/{device_pn}/{device_sn}/#"
            client.subscribe([(topic_prefix, 0), (ack_prefix, 0)])

            def request(
                envelope: bytes, *, wait_for_status: bool
            ) -> tuple[dict[str, Any], str, int] | None:
                published = client.publish(
                    f"cmd/anker_power/{device_pn}/{device_sn}/req", envelope, qos=1
                )
                published.wait_for_publish(timeout=timeout)
                publish_rc = published.rc
                deadline = time.monotonic() + (timeout if wait_for_status else min(timeout, 3.0))
                write_ack: tuple[dict[str, Any], str, int] | None = None
                while time.monotonic() < deadline:
                    try:
                        topic, payload = messages.get(
                            timeout=max(0.05, deadline - time.monotonic())
                        )
                    except queue.Empty:
                        break
                    try:
                        raw_message = json.loads(payload)
                        raw_inner = raw_message.get("payload")
                        if isinstance(raw_inner, str):
                            raw_inner = json.loads(raw_inner)
                        raw_head = raw_message.get("head")
                        seen_messages.append(
                            {
                                "topic": topic,
                                "head_cmd": (
                                    raw_head.get("cmd") if isinstance(raw_head, dict) else None
                                ),
                                "head_cmd_status": (
                                    raw_head.get("cmd_status")
                                    if isinstance(raw_head, dict)
                                    else None
                                ),
                                "head_version": (
                                    raw_head.get("version") if isinstance(raw_head, dict) else None
                                ),
                                "payload_data_bytes": (
                                    len(base64.b64decode(raw_inner["data"], validate=True))
                                    if isinstance(raw_inner, dict)
                                    and isinstance(raw_inner.get("data"), str)
                                    else None
                                ),
                            }
                        )
                        envelope_data, raw = _decode_envelope(payload)
                        if raw is None:
                            continue
                        if wait_for_status:
                            return parse_param_info(raw), topic, publish_rc
                        # For a write, retain a compact acknowledgement record;
                        # never include the raw envelope or credentials.
                        _group, command, body = decode_mqtt_zx_frame(raw)
                        if topic.endswith("/app/res"):
                            write_ack = (
                                {
                                    "outer_cmd": envelope_data.get("head", {}).get("cmd"),
                                    "zx_command": command,
                                    "zx_body_length": len(body),
                                },
                                topic,
                                publish_rc,
                            )
                    except TypeError, ValueError, json.JSONDecodeError:
                        continue
                if wait_for_status:
                    return None
                return write_ack or ({"ack": None, "publish_rc": publish_rc}, "", publish_rc)

            read_envelope = encode_status_envelope(
                device_pn=device_pn,
                device_sn=device_sn,
                account_id=account_id,
                client_id=client_id,
            )
            before_result = request(read_envelope, wait_for_status=True)
            if before_result is None:
                raise TimeoutError("no param_info response received")
            before, before_topic, _before_publish_rc = before_result
            ports = before.get("ports", [])
            if not ports:
                raise RuntimeError("param_info contained no complete port status")
            first = next((item for item in ports if item["port"] == port), None)
            if first is None:
                raise RuntimeError(f"param_info contained no port {port} status")
            if not allow_control:
                return {
                    "device_pn": device_pn,
                    "device_sn": device_sn,
                    "mqtt_connected": True,
                    "read_topic": before_topic,
                    "telemetry": before,
                }
            if priority_mask is None:
                target_enabled = (
                    bool(desired_enabled) if desired_enabled is not None else bool(first["enabled"])
                )
                write_frame = encode_a2345_set_port_switch(
                    port=int(first["port"]), enabled=target_enabled
                )
                write_description: dict[str, Any] = {
                    "kind": "port_switch",
                    "port": first["port"],
                    "enabled": target_enabled,
                }
            else:
                write_frame = encode_a2345_set_port_priority(mask=priority_mask)
                write_description = {
                    "kind": "port_priority",
                    "mask": priority_mask,
                }
            # Keep envelope construction in the integration codec so this
            # probe uses exactly the same outer schema as HA.
            write_bytes = encode_envelope(
                device_pn=device_pn,
                device_sn=device_sn,
                account_id=account_id,
                client_id=client_id,
                data=write_frame,
            )
            write_result = request(write_bytes, wait_for_status=False)

            after_result = request(read_envelope, wait_for_status=True)
            if after_result is None:
                raise TimeoutError("no param_info response after control write")
            after, after_topic, _after_publish_rc = after_result
            after_port = next(
                (item for item in after["ports"] if item["port"] == first["port"]), None
            )
            ack_record = write_result[0] if write_result else None
            ack_received = bool(ack_record and ack_record.get("ack"))
            verification = (
                assess_port_write(
                    before_enabled=bool(first["enabled"]),
                    after_enabled=None if after_port is None else bool(after_port["enabled"]),
                    requested_enabled=bool(write_description["enabled"]),
                    ack=ack_received,
                )
                if priority_mask is None
                else {
                    "readback_matches_request": after.get("priority_mask") == priority_mask,
                    "state_changed_to_request": (
                        before.get("priority_mask") != priority_mask
                        and after.get("priority_mask") == priority_mask
                    ),
                    "write_confirmed": False,
                    "verification": (
                        "state_changed_on_readback"
                        if before.get("priority_mask") != priority_mask
                        and after.get("priority_mask") == priority_mask
                        else "unconfirmed_no_change"
                        if after.get("priority_mask") == priority_mask
                        else "rejected_or_not_observed"
                    ),
                }
            )
            return {
                "device_pn": device_pn,
                "device_sn": device_sn,
                "mqtt_connected": True,
                "read_topic": before_topic,
                "before": first,
                "write": {
                    **write_description,
                    "publish_rc": write_result[2] if write_result else None,
                    "ack": write_result[0] if write_result else None,
                    "ack_topic": write_result[1] if write_result else None,
                },
                "after": after_port,
                "after_topic": after_topic,
                "messages_seen": seen_messages,
                # Live voltage/current/power naturally fluctuate.  The
                # control-relevant state is the relay status itself.
                **verification,
                "priority_raw_before": before.get("raw_fields", {}).get("0xa3"),
                "priority_raw_after": after.get("raw_fields", {}).get("0xa3"),
            }
        finally:
            client.disconnect()
            client.loop_stop()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--auth", type=Path, default=DEFAULT_AUTH_PATH)
    parser.add_argument("--device-pn", default=DEFAULT_DEVICE_PN)
    parser.add_argument("--device-sn", default=DEFAULT_DEVICE_SN)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument(
        "--priority-mask",
        type=int,
        help="write the candidate four-port priority mask instead of a port no-op",
    )
    parser.add_argument(
        "--set-state",
        choices=("on", "off"),
        help="set the selected port state; omit for a same-value no-op",
    )
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument(
        "--read-only", action="store_true", help="read param_info without sending a control packet"
    )
    args = parser.parse_args()
    result = run_probe(
        auth_path=args.auth,
        device_pn=args.device_pn,
        device_sn=args.device_sn,
        timeout=args.timeout,
        port=args.port,
        priority_mask=args.priority_mask,
        desired_enabled=None if args.set_state is None else args.set_state == "on",
        allow_control=not args.read_only,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
