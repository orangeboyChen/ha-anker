"""Protocol codec tests use captured A110A frames and never access Bluetooth hardware."""

import json

import pytest
from cryptography.exceptions import InvalidTag

from custom_components.ha_anker.mqtt_protocol import (
    command_topic,
    decode_a2345_telemetry,
    decode_envelope,
    decode_mqtt_zx_frame,
    encode_a2345_control_envelope,
    encode_a2345_set_charging_mode,
    encode_a2345_set_port_priority,
    encode_a2345_set_port_switch,
    encode_a2345_set_ufcs,
    encode_a2345_setting_envelope,
    encode_a2345_setting_frame,
    encode_a2345_status_request,
    encode_envelope,
    encode_mqtt_zx_frame,
    encode_status_envelope,
    response_topic,
)
from custom_components.ha_anker.protocol import (
    INITIAL_KEY,
    INITIAL_NONCE,
    decode_battery,
    decode_frame,
    decode_tlvs,
    decrypt,
    encode_frame,
    encode_tlvs,
    encrypt,
)


def test_a110a_device_info_frame_decodes() -> None:
    frame = encode_frame(
        1,
        0x0029,
        encode_tlvs(
            [
                (0xA1, b"\x03"),
                (0xA2, b"Charging"),
                (0xA3, b"v0.0.5.2"),
                (0xA4, b"BLE-TEST-SN"),
                (0xA5, bytes.fromhex("007f1d0beff235")),
            ]
        ),
        encrypted=False,
    )
    decoded = decode_frame(frame)
    assert decoded.command == 0x0029
    assert not decoded.encrypted
    fields = decode_tlvs(decoded.body)
    assert fields[0xA4] == b"BLE-TEST-SN"


def test_frame_encoder_matches_known_public_read_request() -> None:
    assert encode_frame(1, 0x0029, b"", encrypted=False).hex() == "ff090a000300010029d7"


def test_frame_rejects_invalid_header_length_and_checksum() -> None:
    with pytest.raises(ValueError, match="header"):
        decode_frame(b"bad")
    valid = bytearray(encode_frame(1, 0x0029, b"", encrypted=False))
    valid[2] = 1
    with pytest.raises(ValueError, match="length"):
        decode_frame(bytes(valid))
    valid = bytearray(encode_frame(1, 0x0029, b"", encrypted=False))
    valid[-1] ^= 1
    with pytest.raises(ValueError, match="checksum"):
        decode_frame(bytes(valid))


def test_tlv_round_trip_and_truncation_validation() -> None:
    payload = encode_tlvs([(0xA6, bytes.fromhex("043500")), (0xFE, b"")])
    assert payload.hex() == "a603043500fe00"
    assert decode_tlvs(payload) == {0xA6: bytes.fromhex("043500"), 0xFE: b""}
    assert decode_tlvs(b"\x00" + payload)[0xA6] == bytes.fromhex("043500")
    with pytest.raises(ValueError, match="header"):
        decode_tlvs(b"\x01")
    with pytest.raises(ValueError, match="value"):
        decode_tlvs(b"\x01\x02\x03")


def test_battery_decoder_only_accepts_known_numeric_shape() -> None:
    assert decode_battery({0xA6: bytes.fromhex("043500")}, 0xA6) == 53
    assert decode_battery({0xA2: bytes.fromhex("043500")}, 0xA2) == 53
    assert decode_battery({0xA6: b"\x35"}, 0xA6) == 53
    assert decode_battery({}, 0xA6) is None
    assert decode_battery({0xA6: b"\x04\x01"}, 0xA6) is None


def test_gcm_authentication_and_fixed_handshake_parameters() -> None:
    plain = encode_tlvs([(0xA1, b"\x21")])
    cipher = encrypt(INITIAL_KEY, INITIAL_NONCE, plain)
    assert decrypt(INITIAL_KEY, INITIAL_NONCE, cipher) == plain
    with pytest.raises(InvalidTag):
        decrypt(INITIAL_KEY, INITIAL_NONCE, cipher[:-1] + bytes([cipher[-1] ^ 1]))


def test_mqtt_envelope_and_topics_are_offline_and_deterministic() -> None:
    encoded = encode_envelope(
        device_pn="A1903",
        device_sn="WIFI-TEST",
        account_id="account-1",
        client_id="client-1",
        data=b"observed",
        timestamp=123,
    )
    decoded = decode_envelope(encoded)
    assert decoded["head"]["cmd"] == 34
    assert decoded["head"]["version"] == "1.0.0.1"
    assert decoded["data"] == b"observed"
    raw = json.loads(encoded)
    assert isinstance(raw["payload"], str)
    assert json.loads(raw["payload"])["data"] == "b2JzZXJ2ZWQ="
    assert command_topic("A1903", "WIFI-TEST") == "cmd/anker_power/A1903/WIFI-TEST/req"
    assert response_topic("A1903", "WIFI-TEST") == "cmd/anker_power/A1903/WIFI-TEST/app/res"
    assert command_topic("A1903", "WIFI-TEST", "custom_app") == (
        "cmd/custom_app/A1903/WIFI-TEST/req"
    )
    with pytest.raises(ValueError, match="observed"):
        encode_envelope(device_pn="A1903", device_sn="WIFI-TEST", account_id="a", client_id="c")
    with pytest.raises(ValueError, match="base64"):
        decode_envelope('{"head": {}, "payload": {"data": "bad!"}}')


def test_a2345_control_envelope_matches_reference_client() -> None:
    encoded = encode_a2345_control_envelope(
        device_pn="A2345",
        device_sn="WIFI-TEST",
        account_id="account-1",
        client_id="client-1",
        data=b"control",
        timestamp=123,
    )
    head = decode_envelope(encoded)["head"]
    assert {key: head[key] for key in ("cmd", "cmd_status", "sign_code", "sess_id", "msg_seq")} == {
        "cmd": 17,
        "cmd_status": 2,
        "sign_code": 1,
        "sess_id": "1234-5678",
        "msg_seq": 1,
    }


def test_a2345_status_request_has_verified_read_only_body() -> None:
    frame = encode_a2345_status_request(timestamp=0x01020304)

    group, command, body = decode_mqtt_zx_frame(frame)

    assert (group, command) == (0x0F, 0x0200)
    assert body == bytes.fromhex("a10122fe050304030201")

    zero_timestamp = decode_mqtt_zx_frame(encode_a2345_status_request(timestamp=0))[2]
    assert zero_timestamp == bytes.fromhex("a10122fe050300000000")


def test_a2345_setting_encoders_match_aot_field_layout() -> None:
    assert encode_a2345_set_port_switch(port=2, enabled=True, timestamp=0).hex() == (
        "ff091c0003000f0207a10122a2020102a3020101fe0503000000009b"
    )
    assert encode_a2345_set_charging_mode(mode=3, timestamp=0).hex() == (
        "ff09180003000f0206a10122a2020103fe0503000000003e"
    )
    assert encode_a2345_set_port_priority(mask=0b0101, timestamp=0).hex() == (
        "ff09180003000f020ca10122a2020105fe05030000000032"
    )
    assert encode_a2345_set_ufcs(enabled=False, timestamp=0).hex() == (
        "ff09180003000f0206a10122a2020100fe0503000000003d"
    )

    # The explicit isMoreLength=true branch in ZXCommandTransformer uses one
    # length byte and is kept available for legacy captures.
    assert (
        encode_a2345_setting_frame(
            items=[(0x0144, b"\x03")], timestamp=0, is_more_length=True
        ).hex()
        == "42024444020103fc050300000000"
    )


def test_a2345_setting_encoder_sorts_fields_and_validates_limits() -> None:
    encoded = encode_a2345_setting_frame(items=[(0x0146, b"\x01"), (0x0144, b"\x02")], timestamp=0)
    assert encoded[:13] == bytes.fromhex("42024444020001024602000101")
    assert encoded[-7:-5] == bytes.fromhex("fc05")
    with pytest.raises(ValueError, match="unique"):
        encode_a2345_setting_frame(items=[(0x0144, b"\x01"), (0x0144, b"\x02")])
    with pytest.raises(ValueError, match="custom"):
        encode_a2345_set_charging_mode(mode=5)
    with pytest.raises(ValueError, match="not supported"):
        encode_a2345_set_port_priority(mask=0x10)
    with pytest.raises(ValueError, match="not supported"):
        encode_a2345_set_port_priority(mask=0x07)


def test_a2345_setting_envelope_carries_raw_setting_bytes() -> None:
    encoded = encode_a2345_setting_envelope(
        device_pn="A2345",
        device_sn="WIFI-TEST",
        account_id="account-1",
        client_id="client-1",
        items=[(0x0144, b"\x02"), (0x0146, b"\x01")],
        timestamp=0,
    )
    decoded = decode_envelope(encoded)
    assert decoded["head"]["cmd"] == 34
    assert decoded["head"]["timestamp"] == 0
    assert decoded["data"] == bytes.fromhex("42024444020001024602000101fc050300000000")


def test_a2345_mode_is_decoded_only_from_mode_response() -> None:
    mode_frame = encode_mqtt_zx_frame(
        group=0x0F,
        command=0x0A00,
        body=b"\x00\xb1\x02\x01\x03",
    )
    telemetry_frame = encode_mqtt_zx_frame(
        group=0x0F,
        command=0x0303,
        body=b"\x00\xb1\x02\x01\x03",
    )
    assert decode_a2345_telemetry(mode_frame)["usage_mode"] == 3
    assert "usage_mode" not in decode_a2345_telemetry(telemetry_frame)


def test_a2345_param_info_decodes_mode_switches_and_priority_records() -> None:
    def relay_record(*, enabled: int, priority: int) -> bytes:
        record = bytearray(19)
        record[0] = enabled
        record[18] = priority
        return bytes(record)

    body = bytearray(b"\x00\xb1\x02\x01\x03")
    for index, (enabled, priority) in enumerate(((1, 2), (0, 1), (0, 1), (0, 1), (1, 1))):
        body.extend((0xAA + index, 0x14, 0x01))
        body.extend(relay_record(enabled=enabled, priority=priority))

    decoded = decode_a2345_telemetry(
        encode_mqtt_zx_frame(group=0x0F, command=0x0A00, body=bytes(body))
    )

    assert decoded["usage_mode"] == 3
    assert decoded["usbc_1_switch"] == 1
    assert decoded["usba_switch"] == 1
    assert decoded["port_priority_mask"] == 1


def test_a2345_telemetry_decodes_port_measurements() -> None:
    def port(status: int, voltage_mv: int, current_ma: int, power_tenth_w: int) -> bytes:
        return (
            bytes((status,))
            + voltage_mv.to_bytes(2, "little")
            + current_ma.to_bytes(2, "little")
            + power_tenth_w.to_bytes(2, "little")
        )

    body = b"\x00"
    body += b"\xa2\x08\x00\x01" + port(1, 4984, 20, 10)
    body += b"\xa3\x08\x00\x01" + port(1, 19816, 549, 990)
    frame = encode_mqtt_zx_frame(group=0x0F, command=0x0303, body=body)

    decoded = decode_a2345_telemetry(frame)

    assert decoded["usbc_1_voltage"] == 4.984
    assert decoded["usbc_1_current"] == 0.02
    assert decoded["usbc_1_power"] == 0.1
    assert decoded["usbc_2_voltage"] == 19.816
    assert decoded["usbc_2_current"] == 0.549
    assert decoded["usbc_2_power"] == 9.9
    assert decoded["usbc_1_status"] == 1
    assert decoded["total_current"] == 0.569
    assert decoded["total_power"] == 10.0
    assert decoded["nonzero_status_count"] == 2


def test_a2345_current_param_info_prefers_a4_port_fields() -> None:
    def port(status: int, voltage_mv: int, current_ma: int, power_tenth_w: int) -> bytes:
        return (
            bytes((status,))
            + voltage_mv.to_bytes(2, "little")
            + current_ma.to_bytes(2, "little")
            + power_tenth_w.to_bytes(2, "little")
        )

    body = b"\x00\xa2\x03\x02\x44\x08\xa4\x08\x04" + port(1, 5000, 20, 10)
    frame = encode_mqtt_zx_frame(group=0x0F, command=0x0A00, body=body)

    decoded = decode_a2345_telemetry(frame)

    assert decoded["usbc_1_voltage"] == 5.0
    assert decoded["usbc_1_current"] == 0.02
    assert decoded["usbc_1_power"] == 0.1
    assert decoded["usbc_1_status"] == 1


def test_a2345_current_param_info_decodes_priority_mask_when_unambiguous() -> None:
    frame = encode_mqtt_zx_frame(
        group=0x0F,
        command=0x030F,
        body=b"\xa3\x02\x01\x00",
    )

    decoded = decode_a2345_telemetry(frame)

    assert decoded["port_priority_mask"] == 0


def test_status_envelope_embeds_the_same_timestamp_and_frame() -> None:
    encoded = encode_status_envelope(
        device_pn="A2345",
        device_sn="WIFI-TEST",
        account_id="account-1",
        client_id="client-1",
        timestamp=123,
    )

    decoded = decode_envelope(encoded)
    assert decoded["head"]["cmd"] == 17
    assert decoded["head"]["timestamp"] == 123
    assert decode_mqtt_zx_frame(decoded["data"])[1] == 0x0200
