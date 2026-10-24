"""Protocol codec tests use captured A110A frames and never access Bluetooth hardware."""

import pytest
from cryptography.exceptions import InvalidTag

from custom_components.ha_anker.mqtt_protocol import (
    command_topic,
    decode_a2345_telemetry,
    decode_envelope,
    decode_mqtt_zx_frame,
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
    assert decoded["data"] == b"observed"
    assert command_topic("A1903", "WIFI-TEST") == "cmd/anker_power/A1903/WIFI-TEST/req"
    assert response_topic("A1903", "WIFI-TEST") == "cmd/anker_power/A1903/WIFI-TEST/app/res"
    with pytest.raises(ValueError, match="observed"):
        encode_envelope(device_pn="A1903", device_sn="WIFI-TEST", account_id="a", client_id="c")
    with pytest.raises(ValueError, match="base64"):
        decode_envelope('{"head": {}, "payload": {"data": "bad!"}}')


def test_a2345_status_request_has_verified_read_only_body() -> None:
    frame = encode_a2345_status_request(timestamp=0x01020304)

    group, command, body = decode_mqtt_zx_frame(frame)

    assert (group, command) == (0x0F, 0x0200)
    assert body == bytes.fromhex("a10122fe050304030201")

    zero_timestamp = decode_mqtt_zx_frame(encode_a2345_status_request(timestamp=0))[2]
    assert zero_timestamp == bytes.fromhex("a10122fe050300000000")


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
