"""Constants for the DownG Scooter integration."""

from __future__ import annotations

DOMAIN = "downg_scooter"

CONF_ADDRESS = "address"
CONF_MODEL_HINT = "model_hint"
CONF_PROTOCOL = "protocol"

PROTOCOL_PLAIN = "55aa"
PROTOCOL_ENCRYPTED = "5aa5"

DEFAULT_NAME = "DownG Scooter"
DEFAULT_SCAN_INTERVAL = 30

PLATFORMS = ["sensor", "switch"]
