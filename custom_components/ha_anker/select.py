"""Cloud charger configuration selects with read-back verification."""

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .cloud_coordinator import AnkerCloudCoordinator
from .const import DOMAIN
from .mqtt_protocol import A2345_PRIORITY_MASKS

_PRIORITY_OPTIONS = tuple(str(mask) for mask in sorted(A2345_PRIORITY_MASKS))
_MODE_OPTIONS = ("ai_power", "port_priority", "dual_laptop", "low_power")
_MODE_VALUES = {name: index for index, name in enumerate(_MODE_OPTIONS, start=1)}
_MODE_NAMES = {value: name for name, value in _MODE_VALUES.items()}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Set up verified A2345 configuration selects."""
    if not isinstance(entry.runtime_data, AnkerCloudCoordinator):
        return
    async_add_entities(
        [
            CloudPortPrioritySelect(entry.runtime_data, device)
            for device in entry.runtime_data.devices
        ]
        + [
            CloudChargingModeSelect(entry.runtime_data, device)
            for device in entry.runtime_data.devices
        ]
    )


class CloudPortPrioritySelect(CoordinatorEntity[AnkerCloudCoordinator], SelectEntity):
    """Select the A2345 four-port priority bitmask."""

    _attr_has_entity_name = True
    _attr_options = _PRIORITY_OPTIONS
    _attr_icon = "mdi:order-numeric-ascending"

    def __init__(self, coordinator: AnkerCloudCoordinator, device: dict) -> None:
        super().__init__(coordinator)
        self.device_id = str(device["device_id"])
        self._attr_unique_id = f"{self.device_id}_port_priority_mask"
        self._attr_name = "Port priority mask"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, self.device_id)},
            "name": device.get("name") or device.get("device_sn") or "Anker Charger",
            "manufacturer": "Anker",
            "model": device.get("device_pn") or "A2345",
        }

    @property
    def available(self) -> bool:
        telemetry = (self.coordinator.data or {}).get(self.device_id, {})
        return super().available and isinstance(telemetry.get("port_priority_mask"), int)

    @property
    def current_option(self) -> str | None:
        telemetry = (self.coordinator.data or {}).get(self.device_id, {})
        mask = telemetry.get("port_priority_mask")
        return str(mask) if isinstance(mask, int) and 0 <= mask <= 15 else None

    async def async_select_option(self, option: str) -> None:
        try:
            mask = int(option, 10)
        except ValueError as err:
            raise ValueError("A2345 priority option must be a decimal mask") from err
        await self.coordinator.async_set_port_priority(self.device_id, mask)


class CloudChargingModeSelect(CoordinatorEntity[AnkerCloudCoordinator], SelectEntity):
    """Select one of the four non-custom A2345 charging modes."""

    _attr_has_entity_name = True
    _attr_options = _MODE_OPTIONS
    _attr_icon = "mdi:power-settings"

    def __init__(self, coordinator: AnkerCloudCoordinator, device: dict) -> None:
        super().__init__(coordinator)
        self.device_id = str(device["device_id"])
        self._attr_unique_id = f"{self.device_id}_usage_mode"
        self._attr_name = "Charging mode"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, self.device_id)},
            "name": device.get("name") or device.get("device_sn") or "Anker Charger",
            "manufacturer": "Anker",
            "model": device.get("device_pn") or "A2345",
        }

    @property
    def available(self) -> bool:
        telemetry = (self.coordinator.data or {}).get(self.device_id, {})
        return super().available and telemetry.get("usage_mode") in _MODE_NAMES

    @property
    def current_option(self) -> str | None:
        telemetry = (self.coordinator.data or {}).get(self.device_id, {})
        mode = telemetry.get("usage_mode")
        return _MODE_NAMES.get(mode)

    async def async_select_option(self, option: str) -> None:
        try:
            mode = _MODE_VALUES[option]
        except KeyError as err:
            raise ValueError("unsupported A2345 charging mode") from err
        await self.coordinator.async_set_charging_mode(self.device_id, mode)
