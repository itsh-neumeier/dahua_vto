"""Constants for the DahuaVTO integration."""

DOMAIN = "dahua_vto"

# Config entry keys
CONF_HOST = "host"
CONF_PORT = "port"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"

# Defaults
DEFAULT_PORT = 80
DEFAULT_USERNAME = "admin"
DEFAULT_NAME = "DahuaVTO"

# Platforms
PLATFORMS = ["event", "binary_sensor", "button", "camera", "sensor", "image"]

# Event codes to subscribe to
EVENT_CODES = [
    "BackKeyLight",
    "RingBell",
    "VideoTalk",       # VTO button press / video call start (some firmware)
    "AccessControl",
    "VideoMotion",
    "AlarmLocal",
    "CallNoAnswered",  # VTO button press on GOLIATH firmware (call not answered)
    "ProfileAlarmTransmit",
    "KeepLightOn",
    # Door contact sensor events
    "DoorStatus",
    "DoorOpen",
    "DoorClose",
]

# Dispatcher signal keys
SIGNAL_EVENT = f"{DOMAIN}_event"
SIGNAL_AVAILABILITY = f"{DOMAIN}_availability"

# Access control methods
ACCESS_METHOD_CARD = 0
ACCESS_METHOD_PIN = 1
ACCESS_METHOD_FINGERPRINT = 6
ACCESS_METHOD_FACE = 128

# Reconnect delays (seconds)
RECONNECT_DELAYS = [3, 10, 30, 60, 120]

# Event stream timeout (seconds) – reconnect if no data received for this long.
# Dahua VTO devices send periodic keepalives but can be silent for 90–120 s.
# 300 s gives plenty of margin before treating silence as a dead connection.
STREAM_TIMEOUT = 300

# How many consecutive stream failures before marking entities unavailable.
# The first drop is treated as a normal reconnect (no availability change).
FAILURES_BEFORE_UNAVAILABLE = 2

# RTSP port
RTSP_PORT = 554
