"""Constants for the DownG Scooter integration."""

from __future__ import annotations

DOMAIN = "downg_scooter"

CONF_ADDRESS = "address"
CONF_PROTOCOL = "protocol"
CONF_TOKEN = "token"

PROTOCOL_PLAIN = "55aa"
PROTOCOL_MIAUTH = "mi_auth"

DEFAULT_NAME = "DownG Scooter"
DEFAULT_SCAN_INTERVAL = 30

PLATFORMS = ["binary_sensor", "button", "select", "sensor", "switch"]
