"""Cloud MQTT coordinator for the A2345 Wi-Fi charger."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import ssl
import tempfile
from typing import Any

import paho.mqtt.client as mqtt
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from paho.mqtt.enums import CallbackAPIVersion

from .cloud_auth import (
    AnkerCloudTransport,
    CloudAccount,
    CloudAccountStore,
    CloudAuthenticationError,
    CloudAuthError,
)
from .const import CONF_ACCOUNT_ID, CONF_SELECTED_DEVICES, DEFAULT_CLOUD_SCAN_INTERVAL, DOMAIN
from .mqtt_protocol import (
    decode_a2345_telemetry,
    decode_envelope,
    encode_a2345_setting_envelope,
    encode_status_envelope,
)

_LOGGER = logging.getLogger(__name__)


def _endpoint(value: str) -> tuple[str, int]:
    """Split the broker endpoint returned by the account API."""
    match = re.match(r"^(?:mqtts?://)?([^:/]+)(?::(\d+))?$", value.strip())
    if not match:
        raise ValueError("invalid Anker MQTT endpoint")
    return match.group(1), int(match.group(2) or 8883)


def _first_text(data: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return None


class _MqttSession:
    """Small paho bridge that keeps credentials in memory only."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.client: mqtt.Client | None = None
        self._connected: asyncio.Future[None] | None = None
        self._messages: asyncio.Queue[tuple[str, bytes]] = asyncio.Queue()

    @property
    def connected(self) -> bool:
        return bool(self.client and self.client.is_connected())

    async def async_connect(self, info: dict[str, Any]) -> None:
        if self.connected:
            return
        if self.client is not None:
            await self.async_close()
        endpoint = _first_text(info, "endpoint_addr", "endpoint")
        certificate = _first_text(info, "certificate_pem", "certificate")
        private_key = _first_text(info, "private_key", "private_key_pem")
        root_ca = _first_text(info, "aws_root_ca1_pem", "root_ca", "ca_pem")
        if not endpoint or not certificate or not private_key or not root_ca:
            raise UpdateFailed("Anker MQTT credentials are incomplete")
        host, port = _endpoint(endpoint)
        app_name = _first_text(info, "app_name") or "anker_power"
        user_id = _first_text(info, "user_id") or "unknown"
        certificate_id = _first_text(info, "certificate_id") or ""
        client_id = _first_text(info, "client_id", "thing_name") or (
            f"android-{app_name}-{user_id}-{certificate_id}"
        )
        context = ssl.create_default_context()
        context.load_verify_locations(cadata=root_ca)
        with (
            tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as cert_file,
            tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as key_file,
        ):
            cert_file.write(certificate)
            cert_file.flush()
            key_file.write(private_key)
            key_file.flush()
            context.load_cert_chain(cert_file.name, key_file.name)
        client = mqtt.Client(CallbackAPIVersion.VERSION2, client_id=client_id)
        client.tls_set_context(context)
        loop = asyncio.get_running_loop()
        connected = loop.create_future()
        self._connected = connected

        def on_connect(_client, _userdata, _flags, reason_code, _properties) -> None:
            if reason_code.is_failure:
                if not connected.done():
                    loop.call_soon_threadsafe(
                        connected.set_exception,
                        ConnectionError(f"Anker MQTT connection failed: {reason_code}"),
                    )
            elif not connected.done():
                loop.call_soon_threadsafe(connected.set_result, None)

        def on_message(_client, _userdata, message) -> None:
            loop.call_soon_threadsafe(
                self._messages.put_nowait, (str(message.topic), bytes(message.payload))
            )

        client.on_connect = on_connect
        client.on_message = on_message
        try:
            await self.hass.async_add_executor_job(client.connect, host, port, 60)
            client.loop_start()
            await asyncio.wait_for(connected, 15)
        except Exception:
            client.loop_stop()
            client.disconnect()
            raise
        self.client = client

    async def async_request(self, *, pn: str, sn: str, envelope: bytes) -> dict[str, object]:
        if self.client is None or not self.connected:
            raise UpdateFailed("Anker MQTT is not connected")
        prefix = f"dt/anker_power/{pn}/{sn}/#"
        response = f"cmd/anker_power/{pn}/{sn}/#"
        result = self.client.subscribe([(prefix, 0), (response, 0)])
        if result[0] != mqtt.MQTT_ERR_SUCCESS:
            raise UpdateFailed(f"Anker MQTT subscribe failed: {result[0]}")
        request_topic = f"cmd/anker_power/{pn}/{sn}/req"
        published = self.client.publish(request_topic, envelope, qos=1)
        if published.rc != mqtt.MQTT_ERR_SUCCESS:
            raise UpdateFailed(f"Anker MQTT publish failed: {published.rc}")
        deadline = asyncio.get_running_loop().time() + 10
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError(f"no A2345 status response for {sn}")
            topic, payload = await asyncio.wait_for(self._messages.get(), remaining)
            if f"/{pn}/{sn}/" not in topic:
                continue
            try:
                message = decode_envelope(payload)
                raw = message.get("data")
                if not isinstance(raw, bytes):
                    continue
                return decode_a2345_telemetry(raw)
            except TypeError, ValueError:
                _LOGGER.debug("Ignoring malformed Anker MQTT response on %s", topic)

    async def async_publish(self, *, pn: str, sn: str, envelope: bytes) -> None:
        """Publish a candidate command without assuming a device ACK topic."""
        if self.client is None or not self.connected:
            raise UpdateFailed("Anker MQTT is not connected")
        prefix = f"dt/anker_power/{pn}/{sn}/#"
        response = f"cmd/anker_power/{pn}/{sn}/#"
        result = self.client.subscribe([(prefix, 0), (response, 0)])
        if result[0] != mqtt.MQTT_ERR_SUCCESS:
            raise UpdateFailed(f"Anker MQTT subscribe failed: {result[0]}")
        published = self.client.publish(f"cmd/anker_power/{pn}/{sn}/req", envelope, qos=1)
        await self.hass.async_add_executor_job(published.wait_for_publish, 10)
        if published.rc != mqtt.MQTT_ERR_SUCCESS:
            raise UpdateFailed(f"Anker MQTT publish failed: {published.rc}")

    async def async_close(self) -> None:
        if self.client is not None:
            client, self.client = self.client, None
            await self.hass.async_add_executor_job(client.disconnect)
            await self.hass.async_add_executor_job(client.loop_stop)


class AnkerCloudCoordinator(DataUpdateCoordinator[dict[str, dict[str, object]]]):
    """Poll selected Wi-Fi devices and expose their verified telemetry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_cloud_{entry.entry_id}",
            update_interval=DEFAULT_CLOUD_SCAN_INTERVAL,
            always_update=False,
        )
        self.account_id = entry.data[CONF_ACCOUNT_ID]
        self.devices = tuple(
            item
            for item in entry.data.get(CONF_SELECTED_DEVICES, [])
            if isinstance(item, dict) and item.get("transport") == "cloud"
        )
        self._store = CloudAccountStore(hass)
        self._transport = AnkerCloudTransport()
        self._mqtt = _MqttSession(hass)
        self._start_task: asyncio.Task[None] | None = None

    @property
    def connected(self) -> bool:
        return self._mqtt.connected

    async def async_set_port_switch(self, device_id: str, port: int, enabled: bool) -> None:
        """Publish one A2345 relay setting and observe a fresh read-back.

        The broker currently exposes no independent device ACK.  A state
        transition is therefore reported as read-back evidence only; a
        same-value request is deliberately not described as confirmed.
        """
        if not 0 <= port <= 5:
            raise ValueError("A2345 port index must be between 0 and 5")
        device = next((item for item in self.devices if item.get("device_id") == device_id), None)
        if device is None:
            raise ValueError("A2345 device is not selected")
        accounts = [
            item for item in await self._store.async_load() if item.account_id == self.account_id
        ]
        if not accounts:
            raise UpdateFailed("Anker account cache entry is missing")
        account = accounts[0]
        manager = self._auth_manager()
        try:
            account = await manager.async_ensure_valid(account)
            info = await self._transport.async_get_mqtt_info(account)
            await self._mqtt.async_connect(info)
            pn, sn = device.get("device_pn"), device.get("device_sn")
            if not isinstance(pn, str) or not isinstance(sn, str):
                raise ValueError("A2345 device metadata is incomplete")
            port_names = ("usbc_1", "usbc_2", "usbc_3", "usbc_4", "usba_1", "usba_2")
            before_data = (self.data or {}).get(device_id, {})
            before_value = before_data.get(f"{port_names[port]}_status")
            client_id = _first_text(info, "client_id", "thing_name") or (
                f"android-{info.get('app_name', 'anker_power')}-"
                f"{account.user_id or account.account_id}-{info.get('certificate_id', '')}"
            )
            envelope = encode_a2345_setting_envelope(
                device_pn=pn,
                device_sn=sn,
                account_id=account.user_id or account.account_id,
                client_id=client_id,
                items=[(0x0144, bytes((port,))), (0x0146, bytes((int(enabled),)))],
            )
            await self._mqtt.async_publish(pn=pn, sn=sn, envelope=envelope)
            read_envelope = encode_status_envelope(
                device_pn=pn,
                device_sn=sn,
                account_id=account.user_id or account.account_id,
                client_id=client_id,
            )
            telemetry = await self._mqtt.async_request(pn=pn, sn=sn, envelope=read_envelope)
            observed = telemetry.get(f"{port_names[port]}_status")
            if not isinstance(observed, int) or (observed != 0) != enabled:
                raise UpdateFailed("A2345 port switch state did not match the requested value")
            if before_value == observed:
                _LOGGER.warning(
                    "A2345 relay request for %s already matched the requested state; "
                    "read-back is not an explicit device acknowledgement",
                    device_id,
                )
            else:
                _LOGGER.info(
                    "A2345 relay state transition observed for %s after publish; "
                    "no independent device acknowledgement was received",
                    device_id,
                )
            updated = dict(self.data or {})
            updated[device_id] = telemetry
            self.async_set_updated_data(updated)
        except CloudAuthenticationError as err:
            await manager.async_invalidate(account.account_id)
            raise UpdateFailed("Anker account authentication failed") from err
        except (CloudAuthError, OSError, TimeoutError, ValueError) as err:
            raise UpdateFailed(str(err)) from err

    async def async_set_port_priority(self, device_id: str, mask: int) -> None:
        """Publish the four-port priority mask and observe a fresh read-back."""
        if not 0 <= mask <= 0x0F:
            raise ValueError("A2345 priority mask must be between 0 and 15")
        device = next((item for item in self.devices if item.get("device_id") == device_id), None)
        if device is None:
            raise ValueError("A2345 device is not selected")
        accounts = [
            item for item in await self._store.async_load() if item.account_id == self.account_id
        ]
        if not accounts:
            raise UpdateFailed("Anker account cache entry is missing")
        account = accounts[0]
        manager = self._auth_manager()
        try:
            account = await manager.async_ensure_valid(account)
            info = await self._transport.async_get_mqtt_info(account)
            await self._mqtt.async_connect(info)
            pn, sn = device.get("device_pn"), device.get("device_sn")
            if not isinstance(pn, str) or not isinstance(sn, str):
                raise ValueError("A2345 device metadata is incomplete")
            before_value = (self.data or {}).get(device_id, {}).get("port_priority_mask")
            client_id = _first_text(info, "client_id", "thing_name") or (
                f"android-{info.get('app_name', 'anker_power')}-"
                f"{account.user_id or account.account_id}-{info.get('certificate_id', '')}"
            )
            envelope = encode_a2345_setting_envelope(
                device_pn=pn,
                device_sn=sn,
                account_id=account.user_id or account.account_id,
                client_id=client_id,
                items=[(0x0144, bytes((mask,)))],
            )
            await self._mqtt.async_publish(pn=pn, sn=sn, envelope=envelope)
            read_envelope = encode_status_envelope(
                device_pn=pn,
                device_sn=sn,
                account_id=account.user_id or account.account_id,
                client_id=client_id,
            )
            telemetry = await self._mqtt.async_request(pn=pn, sn=sn, envelope=read_envelope)
            if telemetry.get("port_priority_mask") != mask:
                raise UpdateFailed("A2345 port priority state did not match the requested mask")
            if before_value == mask:
                _LOGGER.warning(
                    "A2345 priority request for %s already matched the requested mask; "
                    "read-back is not an explicit device acknowledgement",
                    device_id,
                )
            else:
                _LOGGER.info(
                    "A2345 priority transition observed for %s after publish; "
                    "no independent device acknowledgement was received",
                    device_id,
                )
            updated = dict(self.data or {})
            updated[device_id] = telemetry
            self.async_set_updated_data(updated)
        except CloudAuthenticationError as err:
            await manager.async_invalidate(account.account_id)
            raise UpdateFailed("Anker account authentication failed") from err
        except (CloudAuthError, OSError, TimeoutError, ValueError) as err:
            raise UpdateFailed(str(err)) from err

    def async_start(self) -> None:
        if self._start_task is None:
            self._start_task = self.hass.async_create_task(self.async_refresh())

    async def _async_update_data(self) -> dict[str, dict[str, object]]:
        accounts = [
            item for item in await self._store.async_load() if item.account_id == self.account_id
        ]
        if not accounts:
            raise UpdateFailed("Anker account cache entry is missing")
        account: CloudAccount = accounts[0]
        manager = self._auth_manager()
        try:
            account = await manager.async_ensure_valid(account)
            info = await self._transport.async_get_mqtt_info(account)
            await self._mqtt.async_connect(info)
            result: dict[str, dict[str, object]] = {}
            client_id = _first_text(info, "client_id", "thing_name") or (
                f"android-{info.get('app_name', 'anker_power')}-"
                f"{account.user_id or account.account_id}-"
                f"{info.get('certificate_id', '')}"
            )
            for device in self.devices:
                pn, sn = device.get("device_pn"), device.get("device_sn")
                if not isinstance(pn, str) or not isinstance(sn, str):
                    continue
                envelope = encode_status_envelope(
                    device_pn=pn,
                    device_sn=sn,
                    account_id=account.user_id or account.account_id,
                    client_id=client_id,
                )
                result[str(device["device_id"])] = await self._mqtt.async_request(
                    pn=pn, sn=sn, envelope=envelope
                )
            if not result and self.devices:
                raise UpdateFailed("No supported Anker Wi-Fi devices selected")
            return result
        except CloudAuthenticationError as err:
            await manager.async_invalidate(account.account_id)
            raise UpdateFailed("Anker account authentication failed") from err
        except CloudAuthError as err:
            raise UpdateFailed(str(err)) from err
        except (OSError, TimeoutError, ValueError) as err:
            raise UpdateFailed(str(err)) from err

    def _auth_manager(self):
        from .cloud_auth import CloudAuthManager

        return CloudAuthManager(self.hass, transport=self._transport, store=self._store)

    async def async_shutdown(self) -> None:
        if self._start_task and not self._start_task.done():
            self._start_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._start_task
        await super().async_shutdown()
        await self._mqtt.async_close()
        await self._transport.async_close()
