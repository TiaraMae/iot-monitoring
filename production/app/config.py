"""Environment-only configuration and constants."""

import os
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

# All system times are WIB (Asia/Jakarta, UTC+7). DB timestamps use naive
# local NOW() (the server host must be set to Jakarta time); reading times and
# in-memory trackers use now_wib()/to_wib() so everything compares correctly.
TIMEZONE = ZoneInfo("Asia/Jakarta")


def now_wib():
    """Current time as a naive WIB datetime (matches DB naive-local NOW())."""
    return datetime.now(TIMEZONE).replace(tzinfo=None)


def to_wib(dt):
    """Normalize a datetime to naive WIB.

    Aware datetimes are converted to WIB; naive datetimes are assumed to
    already be WIB (DB values, server-local values).
    """
    if dt is None:
        return now_wib()
    if dt.tzinfo is not None:
        return dt.astimezone(TIMEZONE).replace(tzinfo=None)
    return dt


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
UNPAIRED_TIMEOUT_SECONDS = 30
# A paired node streams telemetry every 10 s; silence beyond this marks it offline.
OFFLINE_TIMEOUT_SECONDS = 120
# A need-calibration node streams no telemetry by design (mandatory calibration);
# its only liveness signal is the idle checkin every 10 minutes, so the offline
# window must match that heartbeat (10 min + 60 s margin).
NEED_CALIBRATION_OFFLINE_TIMEOUT_SECONDS = 660
# Default running delay (minutes) before the HVAC delta-T alert fires, applied
# when a delta-T LCL baseline is saved without an explicit delay.
DEFAULT_DELTA_T_DELAY_MINUTES = 5
# Minutes skipped at the start of each compressor run before a reading counts
# toward the 24 h average delta-T on the device card (starting transients).
AVG_DELTA_T_WARMUP_MINUTES = 5
# Must match MAX_CHART_POINTS in templates/dashboard.html — the chart requests
# this many points, and the API must not truncate the window below it (GAP-9).
MAX_CHART_POINTS = 1080

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

# --- OTA firmware updates (see OTA.md) ---
# OTA_TOKEN guards the firmware download route (/firmware/<token>/<file>).
# OTA_PUBLIC_BASE is the URL prefix the NODE uses to download (must be
# reachable from the node: LAN IP + http for local dev, the public domain in
# production). Downloads go straight to this server over HTTPS — they never
# transit MQTT, keeping the free EMQX tier untouched. The route 503s when
# OTA_TOKEN is unset (safe default).
OTA_TOKEN = os.getenv("OTA_TOKEN") or None
OTA_PUBLIC_BASE = (os.getenv("OTA_PUBLIC_BASE") or "").rstrip("/") or None
OTA_FIRMWARE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "firmware_ota",
)
# Compiled firmware binary name allowlist (no path traversal, .bin only).
import re as _re
OTA_FILENAME_RE = _re.compile(r"^[A-Za-z0-9_.-]+\.bin$")
