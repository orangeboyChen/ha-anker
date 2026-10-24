"""Read-only Anker telemetry sensors."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AnkerCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Set up supported Anker sensors."""
    async_add_entities([AnkerBatterySensor(entry.runtime_data)])


class AnkerBatterySensor(CoordinatorEntity[AnkerCoordinator], SensorEntity):
    """Expose the A110A battery percentage and preserve undecoded TLVs for diagnosis."""

    _attr_has_entity_name = True
    _attr_translation_key = "battery"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: AnkerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address.lower()}_battery"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.address.lower())},
            "name": coordinator.config_entry.data.get("name", "Anker Power Bank"),
            "manufacturer": "Anker",
            "model": "A110A",
        }

    @property
    def native_value(self) -> int | None:
        """Return battery percent when reported."""
        return (self.coordinator.data or {}).get("battery")

    @property
    def extra_state_attributes(self) -> dict:
        """Expose undecoded TLVs without assigning speculative meanings."""
        return {"raw_fields": (self.coordinator.data or {}).get("raw_fields", {})}
