"""Bluetooth discovery and manual setup flow."""

from __future__ import annotations

import voluptuous as vol
from home_assistant_bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS, CONF_NAME

from .const import DOMAIN


class AnkerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configure an Anker BLE power bank."""

    VERSION = 1

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Add a discovered device without requiring a cloud account."""
        await self.async_set_unique_id(discovery_info.address.lower())
        self._abort_if_unique_id_configured()
        self.context["title_placeholders"] = {"name": discovery_info.name or discovery_info.address}
        self._discovery_info = discovery_info
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Confirm a discovered device."""
        if user_input is not None:
            info = self._discovery_info
            return self.async_create_entry(
                title=info.name or info.address,
                data={CONF_ADDRESS: info.address, CONF_NAME: info.name or info.address},
            )
        if self._discovery_info is None:
            return self.async_abort(reason="no_device")
        return self.async_show_form(step_id="confirm", data_schema=vol.Schema({}))

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Allow manual setup by Bluetooth address."""
        errors: dict[str, str] = {}
        if user_input is not None:
            from homeassistant.components import bluetooth

            address = user_input[CONF_ADDRESS].strip()
            device = bluetooth.async_ble_device_from_address(self.hass, address, connectable=True)
            if device is None:
                errors["base"] = "not_found"
            else:
                await self.async_set_unique_id(address.lower())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input.get(CONF_NAME) or address,
                    data={CONF_ADDRESS: address, CONF_NAME: user_input.get(CONF_NAME) or address},
                )
        schema = vol.Schema({vol.Required(CONF_ADDRESS): str, vol.Optional(CONF_NAME): str})
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
