"""HVAC offset calibration logic and API.

v4 drop-based protocol (firmware-driven):

1. Button 2 (5 s hold) -> the node publishes
   ``event_button2_offset_calibration_request`` and waits up to 10 s for the
   backend to approve with the ``startcalibration`` control command.
2. The firmware captures a baseline (DS1/DS2 averages) from the next valid
   sample window — the AC must be OFF at this point — then the user starts the
   AC in cooling mode.
3. While running, the firmware publishes throttled ``calibration_progress``
   events ``{ds2, base_ds2, delta}`` and, once the supply probe (DS2) has
   dropped >= 8.0 C below baseline, a ``calibration_success_request`` with the
   base/final readings. On the 10-minute timeout it publishes a
   ``calibration_fail_request``.
4. The backend validates the reported drop (supply >= 7.5 C, return >= 2.5 C),
   computes additive offsets that zero the inter-probe error at the captured
   baseline, marks the appliance ``normal``, and acknowledges the node.

The drop is monitored on the supply probe (DS2) because it sits in the
supply-air stream and tracks the evaporator cold front fastest; v4 monitored
the coil DS18B20 directly, which this hardware does not have.
"""

from datetime import datetime, timezone

from flask import Blueprint, jsonify
from flask_login import login_required, current_user

from app import models, mqtt


calibration_bp = Blueprint("calibration", __name__)

# In-memory calibration sessions: appliance_id -> progress tracker.
# In-memory only; a backend restart mid-session is healed by the firmware's
# 10-minute timeout (calibration_fail_request) or by re-pressing Button 2
# (status stays `calibrating` but no tracker entry -> fresh approval).
CALIBRATION_TRACKER = {}

REQUIRED_DROP = 8.0          # firmware-side gate (DS2 drop, °C)
MIN_SUPPLY_DELTA = 7.5       # backend sanity gate (v4: t3/coil >= 7.5)
MIN_RETURN_DELTA = 2.5       # backend sanity gate (v4: t1/t2 >= 2.5)
TIMEOUT_SECONDS = 600        # firmware-side overall timeout (10 min)


def _expire_tracker(appliance_id):
    """Drop the tracker once the session is older than the firmware's
    10-minute timeout — by then the firmware is guaranteed idle (it either
    timed out and sent calibration_fail_request, or rebooted and lost its
    state). Returns True when a stale tracker was dropped."""
    tracker = CALIBRATION_TRACKER.get(appliance_id)
    if not tracker:
        return False
    start_time = tracker.get("start_time")
    if start_time and (datetime.now(timezone.utc) - start_time).total_seconds() > TIMEOUT_SECONDS:
        CALIBRATION_TRACKER.pop(appliance_id, None)
        return True
    return False


def _begin_session(appliance_id, mac):
    """Approve the calibration: zero offsets so calibration-period telemetry
    is raw, mark the appliance calibrating, and tell the node to capture its
    baseline."""
    models.update_offsets(appliance_id, 0.0, 0.0)
    models.update_operational_status(appliance_id, "calibrating")
    CALIBRATION_TRACKER[appliance_id] = {
        "start_time": datetime.now(timezone.utc),
        "ds2": None,
        "base_ds2": None,
        "delta": 0.0,
    }
    mqtt.send_node_command(mac, "startcalibration")


def handle_offset_calibration_event(mac):
    """Button 2 request: approve (startcalibration), deny busy, or fail."""
    node = models.get_sensor_node_by_mac(mac)
    if not node or node["appliance_id"] is None:
        mqtt.send_node_command(mac, "offsetcalibrationfailack")
        return

    appliance = models.get_appliance(node["appliance_id"])
    if not appliance:
        mqtt.send_node_command(mac, "offsetcalibrationfailack")
        return

    if "Dryer" in appliance["type"]:
        mqtt.send_node_command(mac, "offsetcalibrationfailack")
        return

    status = appliance["operational_status"]
    if status == "offset_calibration_needed":
        _begin_session(appliance["id"], mac)
    elif status == "calibrating":
        _expire_tracker(appliance["id"])
        if appliance["id"] in CALIBRATION_TRACKER:
            # Session already active for this backend process.
            mqtt.send_node_command(mac, "actiondenied:busy")
        else:
            # No live session: either the backend restarted mid-session
            # (tracker is in-memory only) or the firmware rebooted and its
            # 10-minute timeout already elapsed. Re-approve so the node is
            # not left waiting for an approval that already happened.
            _begin_session(appliance["id"], mac)
    else:
        mqtt.send_node_command(mac, "offsetcalibrationfailack")


def handle_calibration_progress(appliance_id, data):
    """Firmware progress event: track the live DS2 drop for the progress API."""
    appliance = models.get_appliance(appliance_id)
    if not appliance or appliance["operational_status"] != "calibrating":
        return

    tracker = CALIBRATION_TRACKER.setdefault(appliance_id, {
        "start_time": datetime.now(timezone.utc),
        "ds2": None,
        "base_ds2": None,
        "delta": 0.0,
    })
    try:
        tracker["ds2"] = float(data.get("ds2"))
        tracker["base_ds2"] = float(data.get("base_ds2"))
        tracker["delta"] = float(data.get("delta"))
    except (TypeError, ValueError):
        pass


def handle_calibration_success_request(mac, appliance, data):
    """Firmware detected the >= 8 C supply drop: validate and store offsets."""
    appliance_id = appliance["id"]
    if appliance["operational_status"] != "calibrating":
        return

    CALIBRATION_TRACKER.pop(appliance_id, None)

    base = data.get("base") or {}
    final = data.get("final") or {}
    try:
        base_ds1 = float(base.get("ds1"))
        base_ds2 = float(base.get("ds2"))
        final_ds1 = float(final.get("ds1"))
        final_ds2 = float(final.get("ds2"))
        ds1_delta = abs(base_ds1 - final_ds1)
        ds2_delta = abs(base_ds2 - final_ds2)
    except (TypeError, ValueError):
        ds1_delta = ds2_delta = 0.0
        final_ds2 = None

    # The drop is signed: only cooling counts. A supply temperature that rose
    # (or never moved) must never complete calibration, no matter the magnitude.
    no_cooling = final_ds2 is None or final_ds2 >= base_ds2

    if no_cooling or ds2_delta < MIN_SUPPLY_DELTA or ds1_delta < MIN_RETURN_DELTA:
        models.update_operational_status(appliance_id, "offset_calibration_needed")
        mqtt.send_node_command(mac, "offsetcalibrationfailack")
        print(f"Calibration appliance {appliance_id} REJECTED: "
              f"supply drop {ds2_delta:.2f} C (need {MIN_SUPPLY_DELTA}), "
              f"return drop {ds1_delta:.2f} C (need {MIN_RETURN_DELTA}), "
              f"cooling={not no_cooling}")
        return

    # Zero the inter-probe error at the captured baseline (AC-off) reference.
    ref = (base_ds1 + base_ds2) / 2.0
    treturn_offset = round(ref - base_ds1, 4)
    tsupply_offset = round(ref - base_ds2, 4)

    models.update_offsets(appliance_id, treturn_offset, tsupply_offset)
    models.update_operational_status(appliance_id, "normal")
    mqtt.send_node_command(mac, "offsetcalibrationsuccessack")
    print(f"Calibration appliance {appliance_id} SUCCESS: "
          f"treturn_offset={treturn_offset}, tsupply_offset={tsupply_offset}")


def handle_calibration_fail_request(mac, appliance_id):
    """Firmware timeout / approval-timeout: revert to calibration needed."""
    CALIBRATION_TRACKER.pop(appliance_id, None)
    appliance = models.get_appliance(appliance_id)
    if not appliance:
        return
    if appliance["operational_status"] == "calibrating":
        models.update_operational_status(appliance_id, "offset_calibration_needed")
        mqtt.send_node_command(mac, "offsetcalibrationfailack")


@calibration_bp.route("/api/device/<int:appliance_id>/calibration_status")
@login_required
def api_calibration_status(appliance_id):
    appliance = models.get_appliance(appliance_id)
    if not appliance or appliance["user_id"] != current_user.id:
        return jsonify({"error": "not found"}), 404

    return jsonify({
        "operational_status": appliance["operational_status"],
        "treturn_offset": appliance["treturn_offset"],
        "tsupply_offset": appliance["tsupply_offset"],
    })


@calibration_bp.route("/api/device/<int:appliance_id>/calibration_progress")
@login_required
def api_calibration_progress(appliance_id):
    appliance = models.get_appliance(appliance_id)
    if not appliance or appliance["user_id"] != current_user.id:
        return jsonify({"error": "not found"}), 404

    if appliance["operational_status"] == "calibrating":
        if _expire_tracker(appliance_id):
            # Session outlived the firmware's 10-minute timeout (firmware
            # rebooted mid-calibration): revert so the UI offers a restart.
            models.update_operational_status(appliance_id, "offset_calibration_needed")
            return jsonify({"progress_pct": 100})
        tracker = CALIBRATION_TRACKER.get(appliance_id, {})
        delta = tracker.get("delta") or 0.0
        start_time = tracker.get("start_time")
        elapsed = (
            (datetime.now(timezone.utc) - start_time).total_seconds()
            if start_time else 0.0
        )
        return jsonify({
            "state": "calibrating",
            "ds2": tracker.get("ds2"),
            "base_ds2": tracker.get("base_ds2"),
            "delta": round(delta, 2),
            "required": REQUIRED_DROP,
            "progress_pct": min(100, max(0, int(delta / REQUIRED_DROP * 100))),
            "elapsed_s": int(elapsed),
            "timeout_s": TIMEOUT_SECONDS,
        })
    return jsonify({"progress_pct": 100})
