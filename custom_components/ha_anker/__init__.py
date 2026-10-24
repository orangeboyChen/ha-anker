"""Anker BLE power bank integration."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import PLATFORMS


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the configured power bank."""
    if entry.data.get("transport") == "cloud":
        from .cloud_coordinator import AnkerCloudCoordinator

        coordinator = AnkerCloudCoordinator(hass, entry)
    else:
        from .coordinator import AnkerCoordinator

        coordinator = AnkerCoordinator(hass, entry)

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Entity creation must not depend on the power bank being nearby.  The
    # first BLE connection is deliberately a background refresh so an offline
    # device can still be added and its connection controls remain available.
    coordinator.async_start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the configured power bank."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await entry.runtime_data.async_shutdown()
    return True
