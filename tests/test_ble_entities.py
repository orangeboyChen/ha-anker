"""Offline tests for the A110A application-level entity surface."""

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_anker.const import CONF_ADDRESS, DOMAIN, TRANSPORT_BLE
from custom_components.ha_anker.coordinator import AnkerCoordinator
from custom_components.ha_anker.sensor import AnkerApplicationSensor


def _coordinator(hass) -> AnkerCoordinator:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test power bank",
        unique_id="ble:AA:BB:CC:DD:EE:FF",
        data={
            CONF_ADDRESS: "AA:BB:CC:DD:EE:FF",
            "transport": TRANSPORT_BLE,
            "name": "Test power bank",
        },
    )
    return AnkerCoordinator(hass, entry)


def test_application_sensor_uses_decoded_value_and_waits_for_missing_data(hass) -> None:
    coordinator = _coordinator(hass)
    sensor = AnkerApplicationSensor(
        coordinator, "battery_temperature", "Battery temperature", "°C", None
    )

    coordinator.data = {"battery_temperature": 26.0}
    assert sensor.native_value == 26.0
    assert sensor.available is True

    coordinator.data = {"raw_fields": {"0xa2": "04003500"}}
    assert sensor.native_value is None
    assert sensor.available is False
