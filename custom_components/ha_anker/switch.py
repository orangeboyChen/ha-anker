"""Cloud port relay controls for the A2345 charger."""

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .cloud_coordinator import AnkerCloudCoordinator
from .const import DOMAIN

# A2345 reports two USB-A measurement slots, but the reference command map
# exposes one shared USB-A relay.  Keep both A1/A2 as sensors and expose only
# the actual five switch selectors here.
_PORTS = ("usbc_1", "usbc_2", "usbc_3", "usbc_4", "usba")
_PORT_NAMES = {
    "usbc_1": "USB C1",
    "usbc_2": "USB C2",
    "usbc_3": "USB C3",
    "usbc_4": "USB C4",
    "usba": "USB-A",
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Set up port relay switches for cloud chargers only."""
    if not isinstance(entry.runtime_data, AnkerCloudCoordinator):
        return
    async_add_entities(
        [
            CloudPortSwitch(entry.runtime_data, device, port_index, port_name)
            for device in entry.runtime_data.devices
            for port_index, port_name in enumerate(_PORTS)
        ]
    )


class CloudPortSwitch(CoordinatorEntity[AnkerCloudCoordinator], SwitchEntity):
    """Control one A2345 port relay through the verified setting map."""

    _attr_has_entity_name = True
    _attr_device_class = SwitchDeviceClass.OUTLET

    def __init__(self, coordinator, device: dict, port: int, port_name: str) -> None:
        super().__init__(coordinator)
        self.device_id = str(device["device_id"])
        self.port = port
        self.port_name = port_name
        self._attr_unique_id = f"{self.device_id}_{port_name}_switch"
        self._attr_translation_key = "port_switch"
        self._attr_translation_placeholders = {"port": _PORT_NAMES[port_name]}
        self._attr_device_info = {
            "identifiers": {(DOMAIN, self.device_id)},
            "name": device.get("name") or device.get("device_sn") or "Anker Charger",
            "manufacturer": "Anker",
            "model": device.get("device_pn") or "A2345",
        }

    @property
    def available(self) -> bool:
        return super().available and self.device_id in (self.coordinator.data or {})

    @property
    def is_on(self) -> bool:
        telemetry = (self.coordinator.data or {}).get(self.device_id, {})
        value = telemetry.get(f"{self.port_name}_status")
        if self.port_name == "usba":
            value = telemetry.get("usba_switch")
            if not isinstance(value, int):
                value = telemetry.get("usba_1_status", telemetry.get("usba_2_status"))
        else:
            value = telemetry.get(f"{self.port_name}_switch", value)
        return isinstance(value, int) and value != 0

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_set_port_switch(self.device_id, self.port, True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_set_port_switch(self.device_id, self.port, False)
