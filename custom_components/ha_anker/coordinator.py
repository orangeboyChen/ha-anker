"""A110A BLE session and telemetry coordinator."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress

from bleak import BleakClient
from bleak_retry_connector import establish_connection
from cryptography.hazmat.primitives.asymmetric import ec
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN, NOTIFY_UUID, SERVICE_UUID, WRITE_UUID
from .protocol import (
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

_LOGGER = logging.getLogger(__name__)
type Telemetry = dict[str, int | str | None]


def _timestamp() -> bytes:
    return int(time.time()).to_bytes(4, "little")


def _public_key(payload: bytes) -> bytes:
    for index, value in decode_tlvs(payload).items():
        if index == 0xA1 and len(value) == 64:
            return value
    raise ValueError("A110A did not return a P-256 public key")


class AnkerCoordinator(DataUpdateCoordinator[Telemetry]):
    """Keep the BLE session alive and publish A110A snapshot/notify fields."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{entry.data[CONF_ADDRESS]}",
            update_interval=DEFAULT_SCAN_INTERVAL,
            always_update=False,
        )
        self.address: str = entry.data[CONF_ADDRESS]
        self._client: BleakClient | None = None
        self._write_char = None
        self._notify_char = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._session_key: bytes | None = None
        self._session_nonce: bytes | None = None
        self._manually_disconnected = False
        self._connect_lock = asyncio.Lock()
        self._initial_refresh_task: asyncio.Task[None] | None = None

    def async_start(self) -> None:
        """Start the initial refresh without blocking config-entry setup."""
        if self._initial_refresh_task is None:
            self._initial_refresh_task = self.hass.async_create_task(
                self.async_refresh(), name=f"{DOMAIN}_{self.address}_initial_refresh"
            )

    @property
    def connected(self) -> bool:
        """Return whether the GATT session is active."""
        return bool(self._client and self._client.is_connected and self._session_key)

    def _on_disconnect(self, _client: BleakClient) -> None:
        self._session_key = None
        self._session_nonce = None
        self.async_set_updated_data(dict(self.data or {}))

    def _on_notify(self, _sender: int, packet: bytearray) -> None:
        try:
            frame = decode_frame(bytes(packet))
            self._queue.put_nowait(frame)
            if frame.command == 0x0300:
                fields = decode_tlvs(frame.body)
                value = decode_battery(fields, 0xA2)
                data = dict(self.data or {})
                if value is not None:
                    data["battery"] = value
                data["raw_fields"] = {f"0x{key:02x}": raw.hex() for key, raw in fields.items()}
                self.async_set_updated_data(data)
        except ValueError, asyncio.QueueFull:
            _LOGGER.debug("Ignoring invalid Anker BLE notification", exc_info=True)

    async def _send(
        self,
        command: int,
        items: list[tuple[int, bytes]],
        *,
        group: int = 1,
        encrypted: bool = True,
    ) -> None:
        if self._write_char is None or self._client is None:
            raise ConnectionError("Anker BLE characteristics are unavailable")
        if encrypted:
            key = self._session_key or INITIAL_KEY
            nonce = self._session_nonce or INITIAL_NONCE
            body = encrypt(key, nonce, encode_tlvs(items))
        else:
            body = encode_tlvs(items)
        await self._client.write_gatt_char(
            self._write_char, encode_frame(group, command, body, encrypted=encrypted), response=True
        )

    async def _receive(self, command: int, timeout: float = 3.0):
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError(f"No response for Anker BLE command 0x{command:04x}")
            frame = await asyncio.wait_for(self._queue.get(), remaining)
            if frame.command == command:
                return frame

    async def _exchange(self, command: int, items: list[tuple[int, bytes]]) -> bytes:
        await self._send(command, items)
        response = await self._receive(command)
        if not response.encrypted:
            raise ValueError(f"Expected encrypted response for 0x{command:04x}")
        key = self._session_key or INITIAL_KEY
        nonce = self._session_nonce or INITIAL_NONCE
        return decrypt(key, nonce, response.body)

    async def _connect(self) -> None:
        from homeassistant.components import bluetooth

        async with self._connect_lock:
            if self.connected:
                return
            device = bluetooth.async_ble_device_from_address(
                self.hass, self.address, connectable=True
            )
            if device is None:
                raise ConnectionError(
                    f"{self.address} is not currently discovered by Home Assistant Bluetooth"
                )
            self._client = await establish_connection(
                BleakClient, device, self.address, disconnected_callback=self._on_disconnect
            )
            services = await self._client.get_services()
            service = services.get_service(SERVICE_UUID)
            if service is None:
                raise ConnectionError("Anker FF09 GATT service was not found")
            self._write_char = service.get_characteristic(WRITE_UUID)
            self._notify_char = service.get_characteristic(NOTIFY_UUID)
            if self._write_char is None or self._notify_char is None:
                raise ConnectionError("Anker FF09 write/notify characteristics were not found")
            await self._client.start_notify(self._notify_char, self._on_notify)
            for command, items in (
                (0x0001, [(0xA1, _timestamp())]),
                (0x0003, [(0xA1, _timestamp()), (0xA3, b"\x20"), (0xA4, b"\x00\xf0")]),
                (0x0029, [(0xA1, _timestamp())]),
                (
                    0x0005,
                    [
                        (0xA1, _timestamp()),
                        (0xA3, b"\x20"),
                        (0xA4, b"\x29\x01"),
                        (0xA5, b"\x44"),
                        (0xA6, b"\x02"),
                    ],
                ),
            ):
                await self._exchange(command, items)
            ephemeral = ec.generate_private_key(ec.SECP256R1())
            public = ephemeral.public_key().public_numbers()
            public_bytes = public.x.to_bytes(32, "big") + public.y.to_bytes(32, "big")
            key_response = await self._exchange(0x0021, [(0xA1, public_bytes)])
            raw_public = _public_key(key_response)
            peer = ec.EllipticCurvePublicNumbers(
                int.from_bytes(raw_public[:32], "big"),
                int.from_bytes(raw_public[32:], "big"),
                ec.SECP256R1(),
            ).public_key()
            shared = ephemeral.exchange(ec.ECDH(), peer)
            self._session_key, self._session_nonce = shared[:16], shared[16:28]
            await self._exchange(
                0x0022, [(0xA1, _timestamp()), (0xA3, bytes.fromhex("808fffff")), (0xA5, b"CST-8")]
            )
            await self._send(0x0200, [(0xA1, b"\x21"), (0xFE, _timestamp())], group=0x0F)
            snapshot = await self._receive(0x0200, 5.0)
            payload = (
                decrypt(self._session_key, self._session_nonce, snapshot.body)
                if snapshot.encrypted
                else snapshot.body
            )
            fields = decode_tlvs(payload)
            battery = decode_battery(fields, 0xA6)
            if battery is None:
                raise ValueError("A110A snapshot did not contain the verified battery field")
            self.async_set_updated_data(
                {
                    "battery": battery,
                    "raw_fields": {f"0x{k:02x}": v.hex() for k, v in fields.items()},
                }
            )

    async def _async_update_data(self) -> Telemetry:
        if self._manually_disconnected:
            return dict(self.data or {})
        try:
            await self._connect()
            return dict(self.data or {})
        except Exception as err:
            await self._drop_connection()
            raise UpdateFailed(str(err)) from err

    async def async_connect(self) -> None:
        """Connect now and resume update management."""
        self._manually_disconnected = False
        await self.async_refresh()

    async def async_disconnect(self) -> None:
        """Disconnect and pause automatic reconnection."""
        self._manually_disconnected = True
        await self._drop_connection()

    async def _drop_connection(self) -> None:
        client, self._client = self._client, None
        self._write_char = self._notify_char = None
        self._session_key = self._session_nonce = None
        if client is not None and client.is_connected:
            await client.disconnect()

    async def async_shutdown(self) -> None:
        """Close the connection on config-entry unload."""
        if self._initial_refresh_task is not None and not self._initial_refresh_task.done():
            self._initial_refresh_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._initial_refresh_task
        await super().async_shutdown()
        await self._drop_connection()
