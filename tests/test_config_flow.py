"""Configuration flow tests for offline-friendly BLE setup."""

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.data_entry_flow import FlowResultType

from custom_components.ha_anker.config_flow import AnkerConfigFlow
from custom_components.ha_anker.const import CONF_TRANSPORT, TRANSPORT_BLE


@pytest.mark.asyncio
async def test_manual_ble_setup_does_not_require_discovery(hass) -> None:
    """An offline or out-of-range device can still be added to HA."""
    flow = AnkerConfigFlow()
    flow.hass = hass
    flow.context = {"source": SOURCE_USER}

    result = await flow.async_step_user(
        {
            CONF_TRANSPORT: TRANSPORT_BLE,
            CONF_ADDRESS: "AA:BB:CC:DD:EE:FF",
            CONF_NAME: "Offline power bank",
        }
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ADDRESS] == "AA:BB:CC:DD:EE:FF"
