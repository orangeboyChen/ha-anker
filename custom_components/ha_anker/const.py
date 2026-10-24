"""Constants for the Anker integration."""

from datetime import timedelta

DOMAIN = "ha_anker"
PLATFORMS = ("sensor", "button", "binary_sensor")
CONF_ADDRESS = "address"
CONF_NAME = "name"
DEFAULT_SCAN_INTERVAL = timedelta(seconds=30)
SERVICE_UUID = "8c850001-0302-41c5-b46e-cf057c562025"
WRITE_UUID = "8c850002-0302-41c5-b46e-cf057c562025"
NOTIFY_UUID = "8c850003-0302-41c5-b46e-cf057c562025"
SUPPORTED_PRODUCT = "A110A"
