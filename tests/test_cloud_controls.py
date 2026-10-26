"""Offline tests for the cloud charger control entities and write path."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_anker.cloud_auth import CloudAccount
from custom_components.ha_anker.cloud_coordinator import AnkerCloudCoordinator
from custom_components.ha_anker.const import (
    CONF_ACCOUNT_ID,
    CONF_SELECTED_DEVICES,
    DOMAIN,
    TRANSPORT_CLOUD,
)
from custom_components.ha_anker.mqtt_protocol import decode_envelope
from custom_components.ha_anker.select import CloudChargingModeSelect, CloudPortPrioritySelect
from custom_components.ha_anker.switch import CloudPortSwitch

DEVICE = {
    "device_id": "device-1",
    "device_sn": "TEST-SN-1",
    "device_pn": "A2345",
    "name": "Test charger",
    "transport": TRANSPORT_CLOUD,
}
ACCOUNT = CloudAccount(
    "account-1",
    "user@example.test",
    "token",
    datetime.now(UTC) + timedelta(hours=1),
    user_id="user-1",
)
MQTT_INFO = {"client_id": "test-client"}


class FakeMqtt:
    """Capture writes and return a supplied fresh read-back."""

    def __init__(self, telemetry: dict[str, object]) -> None:
        self.telemetry = telemetry
        self.published: list[bytes] = []

    async def async_connect(self, _info: dict[str, object]) -> None:
        return None

    async def async_publish(self, *, pn: str, sn: str, envelope: bytes) -> None:
        assert (pn, sn) == ("A2345", "TEST-SN-1")
        assert decode_envelope(envelope)["head"]["cmd"] == 17
        self.published.append(envelope)

    async def async_request(self, *, pn: str, sn: str, envelope: bytes) -> dict[str, object]:
        assert (pn, sn) == ("A2345", "TEST-SN-1")
        assert decode_envelope(envelope)["head"]["cmd"] == 17
        return self.telemetry


class FakeManager:
    async def async_ensure_valid(self, account: CloudAccount) -> CloudAccount:
        return account

    async def async_invalidate(self, _account_id: str) -> None:
        raise AssertionError("the valid test account must not be invalidated")


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Test charger",
        unique_id="cloud:account-1:device-1",
        data={CONF_ACCOUNT_ID: "account-1", CONF_SELECTED_DEVICES: [DEVICE]},
    )


def _telemetry(*, enabled: bool, mask: int = 0) -> dict[str, object]:
    return {
        "usbc_1_status": int(enabled),
        "port_priority_mask": mask,
        "usage_mode": 1,
        "command": 0x0A00,
    }


def _prepare(
    hass, monkeypatch, *, telemetry: dict[str, object], initial: dict[str, dict[str, object]]
) -> tuple[AnkerCloudCoordinator, FakeMqtt]:
    coordinator = AnkerCloudCoordinator(hass, _entry())
    mqtt = FakeMqtt(telemetry)
    coordinator.data = initial
    coordinator._store.async_load = AsyncMock(return_value=[ACCOUNT])
    coordinator._auth_manager = lambda: FakeManager()
    coordinator._transport.async_get_mqtt_info = AsyncMock(return_value=MQTT_INFO)
    coordinator._mqtt = mqtt
    return coordinator, mqtt


@pytest.mark.asyncio
async def test_port_switch_entity_delegates_to_coordinator(hass, monkeypatch) -> None:
    coordinator, _mqtt = _prepare(
        hass,
        monkeypatch,
        telemetry=_telemetry(enabled=False),
        initial={"device-1": _telemetry(enabled=True)},
    )
    coordinator.async_set_port_switch = AsyncMock()
    entity = CloudPortSwitch(coordinator, DEVICE, 0, "usbc_1")

    assert entity.is_on is True
    await entity.async_turn_off()

    coordinator.async_set_port_switch.assert_awaited_once_with("device-1", 0, False)


@pytest.mark.asyncio
async def test_priority_select_entity_delegates_to_coordinator(hass, monkeypatch) -> None:
    coordinator, _mqtt = _prepare(
        hass,
        monkeypatch,
        telemetry=_telemetry(enabled=False, mask=5),
        initial={"device-1": _telemetry(enabled=False, mask=5)},
    )
    coordinator.async_set_port_priority = AsyncMock()
    entity = CloudPortPrioritySelect(coordinator, DEVICE)

    assert entity.current_option == "5"
    await entity.async_select_option("9")

    coordinator.async_set_port_priority.assert_awaited_once_with("device-1", 9)


@pytest.mark.asyncio
async def test_charging_mode_select_entity_delegates_to_coordinator(hass, monkeypatch) -> None:
    coordinator, _mqtt = _prepare(
        hass,
        monkeypatch,
        telemetry=_telemetry(enabled=False),
        initial={"device-1": _telemetry(enabled=False)},
    )
    coordinator.async_set_charging_mode = AsyncMock()
    entity = CloudChargingModeSelect(coordinator, DEVICE)

    assert entity.current_option == "ai_power"
    await entity.async_select_option("dual_laptop")

    coordinator.async_set_charging_mode.assert_awaited_once_with("device-1", 3)


@pytest.mark.asyncio
async def test_charging_mode_write_updates_telemetry_after_state_transition(
    hass, monkeypatch
) -> None:
    telemetry = _telemetry(enabled=False)
    telemetry["usage_mode"] = 3
    initial = _telemetry(enabled=False)
    coordinator, mqtt = _prepare(
        hass,
        monkeypatch,
        telemetry=telemetry,
        initial={"device-1": initial},
    )

    await coordinator.async_set_charging_mode("device-1", 3)

    assert coordinator.data == {"device-1": telemetry}
    frame = decode_envelope(mqtt.published[0])["data"]
    assert frame[:15] == bytes.fromhex("ff09180003000f0206a10122a20201")


@pytest.mark.asyncio
async def test_port_switch_write_updates_telemetry_after_state_transition(
    hass, monkeypatch
) -> None:
    coordinator, mqtt = _prepare(
        hass,
        monkeypatch,
        telemetry=_telemetry(enabled=False),
        initial={"device-1": _telemetry(enabled=True)},
    )

    await coordinator.async_set_port_switch("device-1", 0, False)

    assert coordinator.data == {"device-1": _telemetry(enabled=False)}
    frame = decode_envelope(mqtt.published[0])["data"]
    assert frame[:16] == bytes.fromhex("ff091c0003000f0207a10122a2020100")


@pytest.mark.asyncio
async def test_priority_write_fails_when_readback_does_not_match(hass, monkeypatch) -> None:
    coordinator, _mqtt = _prepare(
        hass,
        monkeypatch,
        telemetry=_telemetry(enabled=False, mask=0),
        initial={"device-1": _telemetry(enabled=False, mask=0)},
    )

    with pytest.raises(UpdateFailed, match="priority state did not match"):
        await coordinator.async_set_port_priority("device-1", 9)

    assert coordinator.data == {"device-1": _telemetry(enabled=False, mask=0)}


@pytest.mark.asyncio
async def test_switch_write_fails_when_readback_does_not_match(hass, monkeypatch) -> None:
    coordinator, _mqtt = _prepare(
        hass,
        monkeypatch,
        telemetry=_telemetry(enabled=True),
        initial={"device-1": _telemetry(enabled=False)},
    )

    with pytest.raises(UpdateFailed, match="port switch state did not match"):
        await coordinator.async_set_port_switch("device-1", 0, False)

    assert coordinator.data == {"device-1": _telemetry(enabled=False)}
