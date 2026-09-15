"""Environment-only configuration and constants."""

import os
from dotenv import load_dotenv

load_dotenv()


def _require_env(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} environment variable is required")
    return value


FLASK_SECRET_KEY = _require_env("FLASK_SECRET_KEY")

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "iot_production_db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = _require_env("DB_PASSWORD")

MQTT_HOST = _require_env("MQTT_HOST")
MQTT_PORT = int(os.getenv("MQTT_PORT", "8883"))
MQTT_USER = _require_env("MQTT_USER")
MQTT_PASS = _require_env("MQTT_PASS")

RUNNING_CURRENT_THRESHOLD = 0.25
MQTT_TOPIC_PREFIX = "iot/production/nodes"
DELTA_T_ALERT_COOLDOWN_SECONDS = 600
UNPAIRED_TIMEOUT_SECONDS = 30
# A paired node streams telemetry every 10 s; silence beyond this marks it offline.
OFFLINE_TIMEOUT_SECONDS = 120
# Default running delay (minutes) before the HVAC delta-T alert fires, applied
# when a delta-T LCL baseline is saved without an explicit delay.
DEFAULT_DELTA_T_DELAY_MINUTES = 5

# Device types stored in appliances.type (pairing form values).
VALID_DEVICE_TYPES = ("HVAC", "Gas Dryer")

# Current-sensor profiles: CF/deductor are owned by the backend and pushed to
# the node via setcf:/setdeductor: (persisted in ESP32 NVS). Firmware computes
# amps = (adc_voltage * cf) - deductor. Values were empirically calibrated:
#   SCT013-015 -> 15A-class clamp (used on dryers / higher-current HVAC)
#   ZHT103C    -> small 5A-class CT (residential HVAC)
CURRENT_SENSOR_PROFILES = {
    "SCT013-015": {"cf": 33.0, "deductor": 0.111},
    "ZHT103C": {"cf": 11.0, "deductor": 0.033},
}
DEFAULT_CURRENT_SENSOR = "SCT013-015"
