"""Account-backed device selection tests."""

from datetime import UTC, datetime, timedelta

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.data_entry_flow import FlowResultType

from custom_components.ha_anker.cloud_auth import CloudAccount, CloudDevice
from custom_components.ha_anker.config_flow import AnkerConfigFlow
from custom_components.ha_anker.const import (
    CONF_DEVICE_ID,
    CONF_SELECTED_DEVICES,
    CONF_TRANSPORT,
    TRANSPORT_BLE,
    TRANSPORT_CLOUD,
)

ACCOUNT = CloudAccount(
    "account-1",
    "user@example.test",
    "token",
    datetime.now(UTC) + timedelta(hours=1),
    user_id="user-1",
)


def _flow(hass) -> AnkerConfigFlow:
    flow = AnkerConfigFlow()
    flow.hass = hass
    flow.context = {"source": SOURCE_USER}
    flow._cloud_account = ACCOUNT
    return flow


@pytest.mark.asyncio
async def test_user_flow_starts_with_account_selection(hass) -> None:
    flow = _flow(hass)
    result = await flow.async_step_user()

    assert result["step_id"] == "cloud_login"


@pytest.mark.asyncio
async def test_account_device_selection_adds_ble_device_without_address_entry(hass) -> None:
    flow = _flow(hass)
    flow._cloud_devices = (
        CloudDevice(
            device_id="serial-1",
            name="Prime Power Bank",
            device_sn="serial-1",
            device_pn="A110A",
            transport=TRANSPORT_BLE,
            address="AA:BB:CC:DD:EE:FF",
        ),
    )

    result = await flow.async_step_cloud_device({CONF_SELECTED_DEVICES: ["serial-1"]})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_TRANSPORT] == TRANSPORT_BLE
    assert result["data"][CONF_ADDRESS] == "AA:BB:CC:DD:EE:FF"
    assert result["data"][CONF_NAME] == "Prime Power Bank"
    assert result["data"][CONF_DEVICE_ID] == "serial-1"


@pytest.mark.asyncio
async def test_cloud_device_selection_remains_multi_select(hass) -> None:
    flow = _flow(hass)
    flow._cloud_devices = (
        CloudDevice("cloud-1", "Charger 1", device_pn="A2345"),
        CloudDevice("cloud-2", "Charger 2", device_pn="A2345"),
    )

    result = await flow.async_step_cloud_device({CONF_SELECTED_DEVICES: ["cloud-1", "cloud-2"]})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_TRANSPORT] == TRANSPORT_CLOUD
    assert len(result["data"][CONF_SELECTED_DEVICES]) == 2
