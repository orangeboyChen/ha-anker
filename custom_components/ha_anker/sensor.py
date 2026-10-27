"""Read-only Anker telemetry sensors."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .cloud_coordinator import AnkerCloudCoordinator
from .const import DOMAIN
from .coordinator import AnkerCoordinator

_A110A_APPLICATION_METRICS = (
    ("total_charging_power", "W", SensorDeviceClass.POWER),
    ("total_discharging_power", "W", SensorDeviceClass.POWER),
    ("c1_power", "W", SensorDeviceClass.POWER),
    ("c1_voltage", "V", SensorDeviceClass.VOLTAGE),
    ("c1_current", "A", SensorDeviceClass.CURRENT),
    ("c2_power", "W", SensorDeviceClass.POWER),
    ("c2_voltage", "V", SensorDeviceClass.VOLTAGE),
    ("c2_current", "A", SensorDeviceClass.CURRENT),
    ("a_power", "W", SensorDeviceClass.POWER),
    ("a_voltage", "V", SensorDeviceClass.VOLTAGE),
    ("a_current", "A", SensorDeviceClass.CURRENT),
    ("c1_power_status", None, None),
    ("c2_power_status", None, None),
    ("a_power_status", None, None),
    ("health", PERCENTAGE, None),
    ("battery_cycles", "cycles", None),
    ("battery_temperature", "°C", SensorDeviceClass.TEMPERATURE),
    ("shell_temperature", "°C", SensorDeviceClass.TEMPERATURE),
)


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
        entities.extend(
            CloudRawTelemetrySensor(entry.runtime_data, device)
            for device in entry.runtime_data.devices
        )
        async_add_entities(entities)
        return
    coordinator = entry.runtime_data
    entities = [AnkerBatterySensor(coordinator)]
    entities.extend(
        AnkerApplicationSensor(coordinator, key, unit, device_class)
        for key, unit, device_class in _A110A_APPLICATION_METRICS
    )
    async_add_entities(entities)


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


class AnkerApplicationSensor(CoordinatorEntity[AnkerCoordinator], SensorEntity):
    """Expose an application-level A110A metric when its wire value is known.

    The APK names these values, but the A110A TLV identifiers and conversions
    are not all verified yet.  The coordinator therefore only publishes a
    metric after a protocol decoder has established it; missing values remain
    unavailable instead of being inferred from an unknown TLV.
    """

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: AnkerCoordinator,
        key: str,
        unit: str | None,
        device_class: SensorDeviceClass | None,
    ) -> None:
        super().__init__(coordinator)
        self.key = key
        self._attr_translation_key = key
        self._attr_unique_id = f"{coordinator.address.lower()}_{key}"
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        if key.endswith("_power_status"):
            self._attr_state_class = None
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.address.lower())},
            "name": coordinator.config_entry.data.get("name", "Anker Power Bank"),
            "manufacturer": "Anker",
            "model": "A110A",
        }

    @property
    def available(self) -> bool:
        """Only expose a value once the coordinator has decoded this metric."""
        return super().available and (self.coordinator.data or {}).get(self.key) is not None

    @property
    def native_value(self) -> int | float | str | None:
        """Return the verified application-level value, if present."""
        value = (self.coordinator.data or {}).get(self.key)
        if self.key.endswith("_power_status"):
            return {0: "idle", 1: "output", 2: "input"}.get(value)
        return value


class CloudPortSensor(CoordinatorEntity[AnkerCloudCoordinator], SensorEntity):
    """Expose the verified A2345 port telemetry fields."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator, device: dict, port: str, metric: str) -> None:
        super().__init__(coordinator)
        self.device_id = str(device["device_id"])
        self.port = port
        self.metric = metric
        self._attr_unique_id = f"{self.device_id}_{port}_{metric}"
        self._attr_translation_key = f"port_{metric}"
        self._attr_translation_placeholders = {
            "port": {
                "usbc_1": "USB C1",
                "usbc_2": "USB C2",
                "usbc_3": "USB C3",
                "usbc_4": "USB C4",
                "usba_1": "USB-A1",
                "usba_2": "USB-A2",
            }[port]
        }
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
        value = telemetry.get(f"{self.port}_{self.metric}")
        if self.metric == "status":
            return {0: "idle", 1: "output", 2: "input"}.get(value)
        return value

    @property
    def extra_state_attributes(self) -> dict:
        telemetry = (self.coordinator.data or {}).get(self.device_id, {})
        return {"raw_fields": telemetry.get("raw_fields", {})}


class CloudAggregateSensor(CoordinatorEntity[AnkerCloudCoordinator], SensorEntity):
    """Expose sums/counts derived only from complete port measurements."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator, device: dict, metric: str) -> None:
        super().__init__(coordinator)
        self.device_id = str(device["device_id"])
        self.metric = metric
        self._attr_unique_id = f"{self.device_id}_{metric}"
        self._attr_translation_key = metric
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


class CloudRawTelemetrySensor(CoordinatorEntity[AnkerCloudCoordinator], SensorEntity):
    """Keep every A2345 response field visible without assigning meanings."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator, device: dict) -> None:
        super().__init__(coordinator)
        self.device_id = str(device["device_id"])
        self._attr_unique_id = f"{self.device_id}_raw_telemetry"
        self._attr_translation_key = "raw_telemetry"
        self._attr_icon = "mdi:code-json"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, self.device_id)},
            "name": device.get("name") or device.get("device_sn") or "Anker Charger",
            "manufacturer": "Anker",
            "model": device.get("device_pn") or "A2345",
        }

    @property
    def native_value(self):
        telemetry = (self.coordinator.data or {}).get(self.device_id, {})
        command = telemetry.get("command")
        return f"0x{command:04x}" if isinstance(command, int) else None

    @property
    def extra_state_attributes(self) -> dict:
        telemetry = (self.coordinator.data or {}).get(self.device_id, {})
        return {
            "raw_fields": telemetry.get("raw_fields", {}),
            "decoded_fields": {
                key: value
                for key, value in telemetry.items()
                if key not in {"command", "raw_fields"}
            },
        }
