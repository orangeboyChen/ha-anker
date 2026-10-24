"""Manual BLE connection controls."""

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AnkerCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Set up explicit connect/disconnect buttons."""
    async_add_entities(
        [AnkerConnectButton(entry.runtime_data), AnkerDisconnectButton(entry.runtime_data)]
    )


class AnkerConnectionButton(CoordinatorEntity[AnkerCoordinator], ButtonEntity):
    """Base class for connection controls."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_has_entity_name = True

    def __init__(self, coordinator: AnkerCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address.lower()}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = {"identifiers": {(DOMAIN, coordinator.address.lower())}}


class AnkerConnectButton(AnkerConnectionButton):
    """Connect and resume periodic updates."""

    def __init__(self, coordinator: AnkerCoordinator) -> None:
        super().__init__(coordinator, "connect")

    @property
    def available(self) -> bool:
        return not self.coordinator.connected

    async def async_press(self) -> None:
        await self.coordinator.async_connect()


class AnkerDisconnectButton(AnkerConnectionButton):
    """Disconnect and pause automatic reconnects."""

    def __init__(self, coordinator: AnkerCoordinator) -> None:
        super().__init__(coordinator, "disconnect")

    @property
    def available(self) -> bool:
        return self.coordinator.connected

    async def async_press(self) -> None:
        await self.coordinator.async_disconnect()
