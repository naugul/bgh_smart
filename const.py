"""Constants for the BGH Smart Control integration."""

DOMAIN = "bgh_smart"

# Configuration
CONF_HOST = "host"
CONF_NAME = "name"

# UDP Ports
UDP_SEND_PORT = 20910
UDP_RECV_PORT = 20911
UDP_SOURCE_PORT = 54563  # Puerto origen requerido por protocolo BGH

# Update interval (seconds) - Backup polling when broadcasts don't work
# The broadcast listener will handle most updates in real-time
UPDATE_INTERVAL = 5

# BGH Protocol Commands (hex)
CMD_STATUS = "00000000000000accf23aa3190590001e4"
# CMD_CONTROL se construye dinámicamente con el Device ID del aire

# Mode mapping
MODE_OFF = 0
MODE_COOL = 1
MODE_HEAT = 2
MODE_DRY = 3
MODE_FAN = 4
MODE_AUTO = 254

MODES = {
    MODE_OFF: "off",
    MODE_COOL: "cool",
    MODE_HEAT: "heat",
    MODE_DRY: "dry",
    MODE_FAN: "fan_only",
    MODE_AUTO: "auto",
}

MODES_REVERSE = {v: k for k, v in MODES.items()}

# Fan speeds
FAN_LOW = 1
FAN_MEDIUM = 2
FAN_HIGH = 3

FAN_MODES = {
    FAN_LOW: "low",
    FAN_MEDIUM: "medium",
    FAN_HIGH: "high",
}

FAN_MODES_REVERSE = {v: k for k, v in FAN_MODES.items()}

# Temperature limits
MIN_TEMP = 16
MAX_TEMP = 30
