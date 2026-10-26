"""Cloud charger configuration selects with read-back verification."""

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .cloud_coordinator import AnkerCloudCoordinator
from .const import DOMAIN

_PRIORITY_OPTIONS = tuple(str(mask) for mask in range(16))


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
