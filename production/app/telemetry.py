"""MQTT telemetry ingestion."""

import json
from datetime import timedelta

from app import models, config
from app import alerts


DEDUPE_CACHE = {}


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


def handle_telemetry(mac, payload):
    try:
        safe_str = payload.replace("nan", "null")
        data = json.loads(safe_str)
    except json.JSONDecodeError as e:
        print(f"Invalid telemetry payload from {mac}: {e}")
        return

    actual_time = _compute_actual_time(data)
    now = config.now_wib()
    if actual_time > now + timedelta(minutes=1):
        actual_time = now

    # Deduplicate messages that arrive too close together.
    last_time = DEDUPE_CACHE.get(mac)
    if last_time and abs((actual_time - last_time).total_seconds()) < 1.0:
        return
    DEDUPE_CACHE[mac] = actual_time

    node = models.get_sensor_node_by_mac(mac)
    if not node:
        node_id = models.register_unpaired_node(mac)
        node = models.get_node_by_id(node_id)

    if node is None:
        return

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
            node["id"], actual_time, tex, rhex, gauge, pres, current
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
            node["id"], actual_time, treturn, tsupply, current
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
