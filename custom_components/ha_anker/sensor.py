"""Read-only Anker telemetry sensors."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .cloud_coordinator import AnkerCloudCoordinator
from .const import DOMAIN
from .coordinator import AnkerCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Set up supported Anker sensors."""
    if isinstance(entry.runtime_data, AnkerCloudCoordinator):
        entities = [
            CloudPortSensor(entry.runtime_data, device, port, metric)
            for device in entry.runtime_data.devices
            for port in ("usbc_1", "usbc_2", "usbc_3", "usbc_4", "usba_1", "usba_2")
            for metric in ("status", "voltage", "current", "power")
        ]
        entities.extend(
            CloudAggregateSensor(entry.runtime_data, device, metric)
            for device in entry.runtime_data.devices
            for metric in ("total_current", "total_power", "nonzero_status_count")
        )
        async_add_entities(entities)
        return
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


class CloudPortSensor(CoordinatorEntity[AnkerCloudCoordinator], SensorEntity):
    """Expose the verified A2345 port telemetry fields."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, device: dict, port: str, metric: str) -> None:
        super().__init__(coordinator)
        self.device_id = str(device["device_id"])
        self.port = port
        self.metric = metric
        self._attr_unique_id = f"{self.device_id}_{port}_{metric}"
        self._attr_name = f"{port.replace('_', ' ').title()} {metric}"
        self._attr_native_unit_of_measurement = {
            "status": None,
            "voltage": "V",
            "current": "A",
            "power": "W",
        }[metric]
        self._attr_device_class = {
            "status": None,
            "voltage": SensorDeviceClass.VOLTAGE,
            "current": SensorDeviceClass.CURRENT,
            "power": SensorDeviceClass.POWER,
        }[metric]
        if metric == "status":
            self._attr_state_class = None
        self._attr_device_info = {
            "identifiers": {(DOMAIN, self.device_id)},
            "name": device.get("name") or device.get("device_sn") or "Anker Charger",
            "manufacturer": "Anker",
            "model": device.get("device_pn") or "A2345",
        }

    @property
    def native_value(self):
        telemetry = (self.coordinator.data or {}).get(self.device_id, {})
        return telemetry.get(f"{self.port}_{self.metric}")

    @property
    def extra_state_attributes(self) -> dict:
        telemetry = (self.coordinator.data or {}).get(self.device_id, {})
        return {"raw_fields": telemetry.get("raw_fields", {})}


class CloudAggregateSensor(CoordinatorEntity[AnkerCloudCoordinator], SensorEntity):
    """Expose sums/counts derived only from complete port measurements."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, device: dict, metric: str) -> None:
        super().__init__(coordinator)
        self.device_id = str(device["device_id"])
        self.metric = metric
        self._attr_unique_id = f"{self.device_id}_{metric}"
        self._attr_name = {
            "total_current": "Total current",
            "total_power": "Total power",
            "nonzero_status_count": "Non-zero port status count",
        }[metric]
        self._attr_native_unit_of_measurement = {
            "total_current": "A",
            "total_power": "W",
            "nonzero_status_count": None,
        }[metric]
        self._attr_device_class = {
            "total_current": SensorDeviceClass.CURRENT,
            "total_power": SensorDeviceClass.POWER,
            "nonzero_status_count": None,
        }[metric]
        self._attr_device_info = {
            "identifiers": {(DOMAIN, self.device_id)},
            "name": device.get("name") or device.get("device_sn") or "Anker Charger",
            "manufacturer": "Anker",
            "model": device.get("device_pn") or "A2345",
        }

    @property
    def native_value(self):
        telemetry = (self.coordinator.data or {}).get(self.device_id, {})
        return telemetry.get(self.metric)

    @property
    def extra_state_attributes(self) -> dict:
        telemetry = (self.coordinator.data or {}).get(self.device_id, {})
        return {"raw_fields": telemetry.get("raw_fields", {})}
