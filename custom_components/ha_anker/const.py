"""Constants for the Anker integration."""

from datetime import timedelta

DOMAIN = "ha_anker"
PLATFORMS = ("sensor", "button", "binary_sensor", "switch", "select")
CONF_ADDRESS = "address"
CONF_NAME = "name"
CONF_TRANSPORT = "transport"
CONF_ACCOUNT_ID = "account_id"
CONF_DEVICE_ID = "device_id"
CONF_DEVICE_PN = "device_pn"
CONF_DEVICE_SN = "device_sn"
CONF_SELECTED_DEVICES = "selected_devices"
TRANSPORT_BLE = "ble"
TRANSPORT_CLOUD = "cloud"
DEFAULT_SCAN_INTERVAL = timedelta(seconds=30)
DEFAULT_CLOUD_SCAN_INTERVAL = timedelta(seconds=30)
SERVICE_UUID = "8c850001-0302-41c5-b46e-cf057c562025"
WRITE_UUID = "8c850002-0302-41c5-b46e-cf057c562025"
NOTIFY_UUID = "8c850003-0302-41c5-b46e-cf057c562025"
SUPPORTED_PRODUCT = "A110A"
