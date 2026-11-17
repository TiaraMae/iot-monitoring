"""MQTT telemetry ingestion."""

import json
import time
from datetime import timedelta

from app import models, config, db, mqtt
from app import alerts


DEDUPE_CACHE = {}
# Backlog re-timing state, per MAC: monotonic time of the last message's
# arrival, and the reconnect (monotonic) the node was last seen after.
_LAST_ARRIVAL = {}
_SEEN_AFTER_CONNECT = {}


def _safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except (ValueError, TypeError):
        return default


def _compute_actual_time(data):
    now = config.now_wib()
    if "agoms" in data:
        return now - timedelta(milliseconds=max(0, int(data["agoms"])))
    if "ago_ms" in data:
        return now - timedelta(milliseconds=max(0, int(data["ago_ms"])))
    return now - timedelta(seconds=max(0, int(data.get("ago", 0))))


def _is_live_stamp(data):
    """True when the node marked the reading as fresh (not from its own buffer)."""
    try:
        if "agoms" in data:
            return int(data["agoms"]) == 0
        if "ago_ms" in data:
            return int(data["ago_ms"]) == 0
        return int(data.get("ago", 0)) == 0
    except (TypeError, ValueError):
        return False


def _backlog_time(mac, node_id, data, now):
    """Return the reconstructed time of a broker-queued reading, or None.

    While the backend is offline the nodes (on their own Wi-Fi) keep publishing
    live readings (agoms=0) at QoS 1; the broker queues them in our persistent
    session and delivers them as a burst on reconnect. Stamped by arrival time
    they would all land at "now" and the 1 s dedupe would drop most of them.
    Queued messages arrive in publish order, so each one is placed one
    telemetry interval after the node's previous stored reading.
    """
    arrival = time.monotonic()
    prev_arrival = _LAST_ARRIVAL.get(mac)
    _LAST_ARRIVAL[mac] = arrival

    connect_mono, session_present = mqtt.last_connect()
    if connect_mono is None or not session_present or not _is_live_stamp(data):
        return None
    if arrival - connect_mono > config.BACKLOG_WINDOW_SECONDS:
        return None

    first_after_connect = _SEEN_AFTER_CONNECT.get(mac) != connect_mono
    _SEEN_AFTER_CONNECT[mac] = connect_mono
    in_burst = (prev_arrival is not None
                and arrival - prev_arrival < config.BACKLOG_BURST_SECONDS)
    early_first = (first_after_connect
                   and arrival - connect_mono < config.BACKLOG_FIRST_MESSAGE_SECONDS)
    if not (in_burst or early_first):
        return None

    last_stored = DEDUPE_CACHE.get(mac)
    if last_stored is None:
        last_stored = models.get_last_reading_time(node_id)
    if last_stored is None:
        return None
    if (now - last_stored).total_seconds() <= config.BACKLOG_MIN_GAP_SECONDS:
        return None
    candidate = last_stored + timedelta(seconds=config.TELEMETRY_INTERVAL_SECONDS)
    if candidate > now - timedelta(seconds=config.BACKLOG_BURST_SECONDS):
        return None
    return candidate


def handle_telemetry(mac, payload):
    try:
        safe_str = payload.replace("nan", "null")
        data = json.loads(safe_str)
    except json.JSONDecodeError as e:
        print(f"Invalid telemetry payload from {mac}: {e}")
        return

    try:
        actual_time = _compute_actual_time(data)
    except (TypeError, ValueError):
        print(f"Invalid age field in telemetry from {mac}")
        return
    now = config.now_wib()
    if actual_time > now + timedelta(minutes=1):
        actual_time = now

    node = models.get_sensor_node_by_mac(mac)
    if node is None and not db.is_available():
        db.log_unavailable(f"telemetry from {mac}")
        return
    if not node:
        node_id = models.register_unpaired_node(mac)
        node = models.get_node_by_id(node_id)

    if node is None:
        return

    estimated = False
    backlog_time = _backlog_time(mac, node["id"], data, now)
    if backlog_time is not None:
        actual_time = backlog_time
        estimated = True

    # Deduplicate messages that arrive too close together.
    last_time = DEDUPE_CACHE.get(mac)
    if last_time and abs((actual_time - last_time).total_seconds()) < 1.0:
        return
    DEDUPE_CACHE[mac] = actual_time

    models.update_node_last_seen(node["id"])

    current = max(0.0, _safe_float(data.get("CurrentA"), 0.0))

    if node["appliance_id"] is None or node["status"] != "paired":
        # Unpaired nodes never stream telemetry (firmware only publishes once
        # calibrated), so there is no live data to preview here — the node
        # simply stays visible in the unpaired-node scan list via last_seen.
        return

    appliance = models.get_appliance(node["appliance_id"])
    if not appliance:
        return

    app_type = appliance["type"]
    appliance_id = appliance["id"]

    if "Dryer" in app_type:
        tex = _safe_float(data.get("BME280Temp"), None)
        rhex = _safe_float(data.get("BME280Hum"), None)
        pres = _safe_float(data.get("BME280Pres"), None)
        baselines = models.get_spc_baselines(appliance_id)
        pressure_baseline = baselines.get("pressure", {})
        atmospheric = appliance.get("atmospheric_pressure")
        if atmospheric is None:
            atmospheric = pressure_baseline.get("mean")
        gauge = pres - atmospheric if pres is not None and atmospheric is not None else None
        models.insert_dryer_reading(
            node["id"], actual_time, tex, rhex, gauge, pres, current,
            time_estimated=estimated,
        )
        reading_data = {
            "texhaust": tex,
            "rhexhaust": rhex,
            "pressure": gauge,
            "current": current,
            "_actual_time": actual_time,
        }
        alerts.check_dryer_faults(appliance_id, reading_data)
    else:
        treturn_raw = _safe_float(data.get("DS1Temp"), None)
        tsupply_raw = _safe_float(data.get("DS2Temp"), None)
        treturn = (treturn_raw + appliance["treturn_offset"]) if treturn_raw is not None else None
        tsupply = (tsupply_raw + appliance["tsupply_offset"]) if tsupply_raw is not None else None
        # Signed delta-T (GAP-8): positive = healthy cooling. A negative value
        # means the probes are swapped (or no cooling) and trips the low-delta-T
        # alert like any other below-LCL reading.
        delta_t = (treturn - tsupply) if treturn is not None and tsupply is not None else None

        models.insert_hvac_reading(
            node["id"], actual_time, treturn, tsupply, current,
            during_calibration=appliance.get("operational_status") == "calibrating",
            time_estimated=estimated,
        )

        # Skip alert evaluation during an active calibration session: offsets
        # are zeroed for the session, so the raw delta-T of the calibration
        # cooling run must not trip alerts.
        if (current >= config.RUNNING_CURRENT_THRESHOLD and delta_t is not None
                and appliance.get("operational_status") != "calibrating"):
            reading_data = {
                "current": current,
                "delta_t": delta_t,
                "_actual_time": actual_time,
            }
            alerts.check_hvac_delta_t_alert(appliance_id, reading_data)

    # Global dryer timeout sweep
    alerts.sweep_dryer_cycles(actual_time)
