"""Constants for the DownG Scooter integration."""

from __future__ import annotations

DOMAIN = "downg_scooter"

CONF_ADDRESS = "address"
CONF_PROTOCOL = "protocol"
CONF_TOKEN = "token"

PROTOCOL_PLAIN = "55aa"
PROTOCOL_MIAUTH = "mi_auth"

DEFAULT_NAME = "DownG Scooter"
CONNECTED_POLL_INTERVAL = 10
DISCONNECTED_RETRY_INTERVAL = 60

PLATFORMS = ["binary_sensor", "button", "select", "sensor", "switch"]
