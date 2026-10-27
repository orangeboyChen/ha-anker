"""BLE connection status entity."""

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AnkerCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Set up the connection sensor."""
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
