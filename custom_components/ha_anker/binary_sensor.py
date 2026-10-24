"""BLE connection status entity."""

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .cloud_coordinator import AnkerCloudCoordinator
from .const import DOMAIN
from .coordinator import AnkerCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Set up the connection sensor."""
    if isinstance(entry.runtime_data, AnkerCloudCoordinator):
        async_add_entities(
            [
                CloudConnectionSensor(entry.runtime_data, device)
                for device in entry.runtime_data.devices
            ]
        )
        return
    async_add_entities([AnkerConnectionSensor(entry.runtime_data)])


class AnkerConnectionSensor(CoordinatorEntity[AnkerCoordinator], BinarySensorEntity):
    """Show the live BLE session state."""

    _attr_has_entity_name = True
    _attr_translation_key = "connection"

    def __init__(self, coordinator: AnkerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address.lower()}_connection"
        self._attr_device_info = {"identifiers": {(DOMAIN, coordinator.address.lower())}}

    @property
    def is_on(self) -> bool:
        return self.coordinator.connected


class CloudConnectionSensor(CoordinatorEntity[AnkerCloudCoordinator], BinarySensorEntity):
    """Show whether the cloud MQTT session is connected."""

    _attr_has_entity_name = True
    _attr_translation_key = "connection"

    def __init__(self, coordinator, device: dict) -> None:
        super().__init__(coordinator)
        self.device_id = str(device["device_id"])
        self._attr_unique_id = f"{self.device_id}_cloud_connection"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, self.device_id)},
            "name": device.get("name") or device.get("device_sn") or "Anker Charger",
            "manufacturer": "Anker",
            "model": device.get("device_pn") or "A2345",
        }

    @property
    def is_on(self) -> bool:
        return self.coordinator.connected
