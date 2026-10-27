"""Bluetooth discovery and cloud account/device setup flows."""

from __future__ import annotations

import voluptuous as vol
from home_assistant_bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS, CONF_EMAIL, CONF_NAME, CONF_PASSWORD
from homeassistant.helpers.selector import SelectSelector, SelectSelectorConfig

from .cloud_auth import (
    CloudAccount,
    CloudAuthenticationError,
    CloudAuthManager,
    CloudDevice,
    CloudProtocolUnavailableError,
)
from .const import (
    CONF_ACCOUNT_ID,
    CONF_DEVICE_ID,
    CONF_SELECTED_DEVICES,
    CONF_TRANSPORT,
    DOMAIN,
    TRANSPORT_BLE,
    TRANSPORT_CLOUD,
)


class AnkerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configure an Anker BLE or cloud-connected device."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._cloud_manager: CloudAuthManager | None = None
        self._cloud_account: CloudAccount | None = None
        self._cloud_devices: tuple[CloudDevice, ...] = ()

    @property
    def cloud_manager(self) -> CloudAuthManager:
        """Return the lazily created account manager."""
        if self._cloud_manager is None:
            self._cloud_manager = CloudAuthManager(self.hass)
        return self._cloud_manager

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
            if info is None:
                return self.async_abort(reason="no_device")
            return self.async_create_entry(
                title=info.name or info.address,
                data={
                    CONF_TRANSPORT: TRANSPORT_BLE,
                    CONF_ADDRESS: info.address,
                    CONF_NAME: info.name or info.address,
                },
            )
        if self._discovery_info is None:
            return self.async_abort(reason="no_device")
        return self.async_show_form(step_id="confirm", data_schema=vol.Schema({}))

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Start with Anker login; choose Bluetooth or cloud devices by model."""
        return await self.async_step_cloud_account(user_input)

    async def async_step_cloud_account(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Select a cached account or start a new login."""
        errors: dict[str, str] = {}
        accounts = await self.cloud_manager.store.async_load()
        if user_input is not None:
            account_id = user_input[CONF_ACCOUNT_ID]
            if account_id == "new":
                return await self.async_step_cloud_login()
            account = next((item for item in accounts if item.account_id == account_id), None)
            if account is None:
                errors["base"] = "account_not_found"
            else:
                try:
                    self._cloud_account = await self.cloud_manager.async_ensure_valid(account)
                    return await self.async_step_cloud_device()
                except CloudAuthenticationError:
                    errors["base"] = "auth_expired"
                    accounts = await self.cloud_manager.store.async_load()
                except CloudProtocolUnavailableError:
                    errors["base"] = "cloud_unavailable"
        if not accounts:
            return await self.async_step_cloud_login()
        choices = {account.account_id: account.email for account in accounts}
        choices["new"] = (
            "添加其他账号" if self.hass.config.language.startswith("zh") else "Add another account"
        )
        return self.async_show_form(
            step_id="cloud_account",
            data_schema=vol.Schema({vol.Required(CONF_ACCOUNT_ID): vol.In(choices)}),
            errors=errors,
        )

    async def async_step_cloud_login(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Authenticate a new account without persisting its password."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                self._cloud_account = await self.cloud_manager.async_login(
                    user_input[CONF_EMAIL], user_input[CONF_PASSWORD]
                )
                return await self.async_step_cloud_device()
            except CloudAuthenticationError:
                errors["base"] = "invalid_auth"
            except CloudProtocolUnavailableError:
                errors["base"] = "cloud_unavailable"
        return self.async_show_form(
            step_id="cloud_login",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_EMAIL): str,
                    vol.Required(CONF_PASSWORD): vol.All(str, vol.Length(min=1)),
                }
            ),
            errors=errors,
        )

    async def async_step_cloud_device(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Let the user choose one device from the authenticated account."""
        if self._cloud_account is None:
            return self.async_abort(reason="account_not_found")
        errors: dict[str, str] = {}
        if not self._cloud_devices:
            try:
                self._cloud_devices = tuple(
                    await self.cloud_manager.async_list_devices(self._cloud_account)
                )
            except CloudAuthenticationError:
                return await self.async_step_cloud_account()
            except CloudProtocolUnavailableError:
                errors["base"] = "cloud_unavailable"
            finally:
                await self.cloud_manager.async_close()
            if not self._cloud_devices:
                if user_input is not None:
                    errors["base"] = errors.get("base", "no_devices")
                return self.async_show_form(
                    step_id="cloud_device", data_schema=vol.Schema({}), errors=errors
                )
        configured = {
            entry.data.get(CONF_DEVICE_ID)
            for entry in self.hass.config_entries.async_entries(DOMAIN)
            if isinstance(entry.data.get(CONF_DEVICE_ID), str)
        }
        configured.update(
            device_id
            for entry in self.hass.config_entries.async_entries(DOMAIN)
            if entry.data.get(CONF_ACCOUNT_ID) == self._cloud_account.account_id
            for item in entry.data.get(CONF_SELECTED_DEVICES, [])
            if isinstance(item, dict)
            for device_id in [item.get("device_id")]
            if isinstance(device_id, str)
        )
        available = [device for device in self._cloud_devices if device.device_id not in configured]
        if user_input is not None:
            selected_ids = user_input.get(CONF_SELECTED_DEVICES, [])
            if isinstance(selected_ids, str):
                selected_ids = [selected_ids]
            selected = [device for device in available if device.device_id in selected_ids]
            if not selected or len(selected) != len(set(selected_ids)):
                errors["base"] = "device_not_found" if selected_ids else "no_devices_selected"
            elif len({device.transport for device in selected}) != 1:
                errors["base"] = "mixed_transports"
            elif selected[0].transport == TRANSPORT_BLE and len(selected) != 1:
                errors["base"] = "select_one_bluetooth"
            elif selected[0].transport == TRANSPORT_BLE and not selected[0].address:
                errors["base"] = "bluetooth_address_missing"
            elif selected[0].transport == TRANSPORT_BLE:
                device = selected[0]
                await self.async_set_unique_id(device.address.lower())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=device.name,
                    data={
                        CONF_TRANSPORT: TRANSPORT_BLE,
                        CONF_ADDRESS: device.address,
                        CONF_NAME: device.name,
                        CONF_DEVICE_ID: device.device_id,
                    },
                )
            else:
                unique_id = (
                    f"{TRANSPORT_CLOUD}:{self._cloud_account.account_id}:"
                    f"{','.join(sorted(selected_ids))}"
                )
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=(
                        selected[0].name
                        if len(selected) == 1
                        else f"Anker devices ({len(selected)})"
                    ),
                    data={
                        CONF_TRANSPORT: TRANSPORT_CLOUD,
                        CONF_ACCOUNT_ID: self._cloud_account.account_id,
                        CONF_SELECTED_DEVICES: [
                            {
                                "device_id": device.device_id,
                                "device_sn": device.device_sn,
                                "device_pn": device.device_pn,
                                "name": device.name,
                                "transport": device.transport,
                                "address": device.address,
                                "online": device.online,
                            }
                            for device in selected
                        ],
                    },
                )
        if not available:
            return self.async_abort(reason="no_devices")
        choices = [
            {
                "value": device.device_id,
                "label": (
                    f"{device.name} ({device.device_pn}, {self._transport_label(device.transport)})"
                ),
            }
            for device in available
        ]
        return self.async_show_form(
            step_id="cloud_device",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SELECTED_DEVICES): SelectSelector(
                        SelectSelectorConfig(options=choices, multiple=True, mode="list")
                    )
                }
            ),
            errors=errors,
        )

    def _transport_label(self, transport: str) -> str:
        """Return a localized transport label for selector options."""
        if self.hass.config.language.startswith("zh"):
            return "蓝牙" if transport == TRANSPORT_BLE else "Wi-Fi"
        return "Bluetooth" if transport == TRANSPORT_BLE else "Wi-Fi"
