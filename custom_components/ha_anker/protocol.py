"""Verified FF09 GCM framing and A110A battery snapshot decoding."""

from __future__ import annotations

from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

HEADER = b"\xff\x09"
AAD = bytes.fromhex("3322110077665544bbaa9988ffeeddcc")
INITIAL_KEY = bytes.fromhex("b8ff7422955d4eb6d554a2c470280559")
INITIAL_NONCE = bytes.fromhex("6ba3e3f2f3a60f2971ce5d1f")


@dataclass(frozen=True)
class Frame:
    group: int
    command: int
    encrypted: bool
    body: bytes


def encode_tlvs(items: list[tuple[int, bytes]]) -> bytes:
    """Encode the byte-indexed TLVs used by the verified A110A requests."""
    result = bytearray()
    for index, value in items:
        if not 0 <= index <= 255 or len(value) > 255:
            raise ValueError("TLV index and value length must fit in one byte")
        result.extend((index, len(value)))
        result.extend(value)
    return bytes(result)


def decode_tlvs(payload: bytes) -> dict[int, bytes]:
    """Decode TLVs, tolerating the leading zero status byte in response payloads."""
    offset = 1 if payload[:1] == b"\x00" else 0
    fields: dict[int, bytes] = {}
    while offset < len(payload):
        if offset + 2 > len(payload):
            raise ValueError("truncated TLV header")
        index, length = payload[offset : offset + 2]
        offset += 2
        if offset + length > len(payload):
            raise ValueError("truncated TLV value")
        fields[index] = payload[offset : offset + length]
        offset += length
    return fields


def encode_frame(group: int, command: int, body: bytes, *, encrypted: bool) -> bytes:
    """Build an FF09 frame with the protocol XOR checksum."""
    wire_command = command | (0x4000 if encrypted else 0)
    frame = bytearray(10 + len(body))
    frame[:2] = HEADER
    frame[2:4] = len(frame).to_bytes(2, "little")
    frame[4:7] = bytes((3, 0, group))
    frame[7:9] = wire_command.to_bytes(2, "big")
    frame[9:-1] = body
    for byte in frame[:-1]:
        frame[-1] ^= byte
    return bytes(frame)


def decode_frame(value: bytes) -> Frame:
    """Validate and decode one complete FF09 frame."""
    if len(value) < 10 or value[:2] != HEADER:
        raise ValueError("invalid FF09 frame header")
    if int.from_bytes(value[2:4], "little") != len(value):
        raise ValueError("FF09 frame length mismatch")
    checksum = 0
    for byte in value:
        checksum ^= byte
    if checksum:
        raise ValueError("FF09 checksum mismatch")
    wire_command = int.from_bytes(value[7:9], "big")
    return Frame(value[6], wire_command & 0xB7FF, bool(wire_command & 0x4000), value[9:-1])


def encrypt(key: bytes, nonce: bytes, payload: bytes) -> bytes:
    """Encrypt a packet using the app-verified AES-GCM parameters."""
    return AESGCM(key).encrypt(nonce, payload, AAD)


def decrypt(key: bytes, nonce: bytes, payload: bytes) -> bytes:
    """Authenticate and decrypt one GCM payload."""
    return AESGCM(key).decrypt(nonce, payload, AAD)


def decode_battery(fields: dict[int, bytes], field_id: int) -> int | None:
    """Decode the only A110A telemetry field whose scale has been verified."""
    value = fields.get(field_id)
    if value is None:
        return None
    if len(value) >= 3 and value[0] == 0x04:
        return int.from_bytes(value[1:3], "little")
    if len(value) == 1:
        return value[0]
    return None
