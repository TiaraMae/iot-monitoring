"""HVAC offset calibration logic and API.

v4 drop-based protocol (firmware-driven):

1. Hidden button (Button 2, 5 s hold) -> the node publishes
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
   then aligns sensor 2 to sensor 1: sensor 1 (Port 1, return) is the trusted
   reference (offset stays 0); sensor 2 (Port 2, supply) gets the additive
   offset c = base_ds1 - base_ds2 (affine y = m*x + c with m = 1 — a single
   shared calibration point fixes c only). Both probes then read identically
   at the calibration point. Success marks the appliance `normal` and records
   `calibrated_at`; failure reverts to `offset_calibration_needed` with the
   previous offsets restored.

The drop is monitored on the supply probe (DS2) because it sits in the
supply-air stream and tracks the evaporator cold front fastest; v4 monitored
the coil DS18B20 directly, which this hardware does not have.
"""

from flask import Blueprint, jsonify
from flask_login import login_required, current_user

from datetime import timedelta

from app import config, models, mqtt


calibration_bp = Blueprint("calibration", __name__)

# In-memory calibration sessions: appliance_id -> progress tracker.
# In-memory only; a backend restart mid-session is healed by the firmware's
# 10-minute timeout (calibration_fail_request) or by re-pressing the hidden
# button (status stays `calibrating` but no tracker entry -> fresh approval).
CALIBRATION_TRACKER = {}

REQUIRED_DROP = 8.0          # firmware-side gate (DS1/reference drop, °C)
MIN_REFERENCE_DROP = 7.5     # backend sanity gate on the DS1 (reference) drop
MIN_TRACKING_DROP = 2.5      # backend sanity gate on the DS2 (tracking) drop
TIMEOUT_SECONDS = 600        # firmware-side overall timeout (10 min)
PROGRESS_GRACE_SECONDS = 30  # reconnect during calibrating: prove-alive window


def _expire_tracker(appliance_id):
    """Drop the tracker once the session is older than the firmware's
    10-minute timeout — by then the firmware is guaranteed idle (it either
    timed out and sent calibration_fail_request, or rebooted and lost its
    state). Returns True when a stale tracker was dropped."""
    tracker = CALIBRATION_TRACKER.get(appliance_id)
    if not tracker:
        return False
    start_time = tracker.get("start_time")
    if start_time and (config.now_wib() - config.to_wib(start_time)).total_seconds() > TIMEOUT_SECONDS:
        CALIBRATION_TRACKER.pop(appliance_id, None)
        return True
    return False


def _begin_session(appliance_id, mac):
    """Approve the calibration: zero offsets so calibration-period telemetry
    is raw, mark the appliance calibrating, and tell the node to capture its
    baseline. The previous offsets are kept in the tracker so a failed or
    cancelled re-calibration can restore them."""
    appliance = models.get_appliance(appliance_id) or {}
    prev_offsets = (appliance.get("treturn_offset") or 0.0,
                    appliance.get("tsupply_offset") or 0.0)
    models.update_offsets(appliance_id, 0.0, 0.0)
    models.update_operational_status(appliance_id, "calibrating")
    CALIBRATION_TRACKER[appliance_id] = {
        "start_time": config.now_wib(),
        "ds1": None,
        "base_ds1": None,
        "delta": 0.0,
        "prev_offsets": prev_offsets,
        "suspected_dead_at": None,
    }
    mqtt.send_node_command(mac, "startcalibration")


def _revert_to_calibration_needed(appliance_id, tracker):
    """Restore the offsets zeroed at session start and put the appliance back
    to `offset_calibration_needed` (the pre-calibration state)."""
    prev = (tracker or {}).get("prev_offsets")
    if prev is not None:
        models.update_offsets(appliance_id, prev[0], prev[1])
    models.update_operational_status(appliance_id, "offset_calibration_needed")


def reconcile_on_node_connect(appliance_id):
    """Called when a paired node (re)connects and requests its config while the
    appliance is `calibrating`. Two cases:
    - Tracker missing/stale: the session cannot be live (firmware calibration
      state is RAM-only, fail events are not buffered) -> revert immediately.
    - Tracker live: the reconnect may be a dead reboot OR a WiFi blip with the
      session still running. Arm a short prove-alive window: the firmware
      publishes calibration_progress every ~2 s, so if none arrive within
      PROGRESS_GRACE_SECONDS the session is declared dead and a later
      reconcile_calibration_state() reverts it. Returns True when reverted
      right now."""
    appliance = models.get_appliance(appliance_id)
    if not appliance or appliance["operational_status"] != "calibrating":
        return False
    tracker = CALIBRATION_TRACKER.get(appliance_id)
    if tracker and not _expire_tracker(appliance_id):
        deadline = config.now_wib() + timedelta(seconds=PROGRESS_GRACE_SECONDS)
        if not tracker.get("suspected_dead_at") or config.to_wib(tracker["suspected_dead_at"]) > deadline:
            tracker["suspected_dead_at"] = deadline
        return False
    _revert_to_calibration_needed(appliance_id, tracker)
    return True


def reconcile_calibration_state(appliance):
    """Revert a `calibrating` appliance to `offset_calibration_needed` when the
    session cannot be live: tracker missing, tracker older than the firmware's
    10-minute timeout, or the node reconnected and never proved itself with a
    calibration_progress event within PROGRESS_GRACE_SECONDS. Called from the
    read endpoints (/latest and calibration_progress) so the UI converges even
    without a modal open. Returns True when the status was reverted."""
    if not appliance or appliance["operational_status"] != "calibrating":
        return False
    appliance_id = appliance["id"]
    tracker = CALIBRATION_TRACKER.get(appliance_id)
    if not tracker:
        _revert_to_calibration_needed(appliance_id, None)
        return True
    if _expire_tracker(appliance_id):
        _revert_to_calibration_needed(appliance_id, tracker)
        return True
    suspected = tracker.get("suspected_dead_at")
    if suspected and config.now_wib() > config.to_wib(suspected):
        CALIBRATION_TRACKER.pop(appliance_id, None)
        _revert_to_calibration_needed(appliance_id, tracker)
        return True
    return False


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
        "start_time": config.now_wib(),
        "ds1": None,
        "base_ds1": None,
        "delta": 0.0,
        "prev_offsets": None,
        "suspected_dead_at": None,
    })
    try:
        tracker["ds1"] = float(data.get("ds1"))
        tracker["base_ds1"] = float(data.get("base_ds1"))
        tracker["delta"] = float(data.get("delta"))
    except (TypeError, ValueError):
        pass
    # A progress event proves the firmware session is alive: clear any
    # prove-alive suspicion armed by a reconnect during calibration.
    tracker["suspected_dead_at"] = None


def handle_calibration_success_request(mac, appliance, data):
    """Firmware detected the >= 8 C supply drop: validate and store offsets."""
    appliance_id = appliance["id"]
    if appliance["operational_status"] != "calibrating":
        return

    tracker = CALIBRATION_TRACKER.pop(appliance_id, None) or {}

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

    # The drop is validated on the REFERENCE probe (DS1): only cooling counts,
    # a temperature rise must never complete calibration. DS2 (tracking probe)
    # must also have moved: both probes are assumed to sit at the same point,
    # so a DS2 that did not follow indicates a dead or sluggish probe.
    no_cooling = final_ds1 is None or final_ds1 >= base_ds1

    if no_cooling or ds1_delta < MIN_REFERENCE_DROP or ds2_delta < MIN_TRACKING_DROP:
        prev = tracker.get("prev_offsets")
        if prev is not None:
            models.update_offsets(appliance_id, prev[0], prev[1])
        models.update_operational_status(appliance_id, "offset_calibration_needed")
        mqtt.send_node_command(mac, "offsetcalibrationfailack")
        print(f"Calibration appliance {appliance_id} REJECTED: "
              f"reference (DS1) drop {ds1_delta:.2f} C (need {MIN_REFERENCE_DROP}), "
              f"tracking (DS2) drop {ds2_delta:.2f} C (need {MIN_TRACKING_DROP}), "
              f"cooling={not no_cooling}")
        return

    # Sensor 1 (Port 1, return/DS1) is the trusted reference and is never
    # shifted. Sensor 2 (Port 2, supply/DS2) is aligned to it with the affine
    # model y = m*x + c. A single shared calibration point fixes c only, so
    # m = 1 (probe slopes are assumed equal); corrected DS2 = raw DS2 + c reads
    # exactly the same as DS1 at the calibration point.
    treturn_offset = 0.0
    tsupply_offset = round(base_ds1 - base_ds2, 4)

    models.update_offsets(appliance_id, treturn_offset, tsupply_offset)
    models.update_operational_status(appliance_id, "normal")
    is_initial_calibration = appliance.get("initial_calibrated_at") is None
    models.mark_calibrated(appliance_id)
    if is_initial_calibration:
        # The first calibration defines the display window: drop the raw
        # calibration-session readings recorded before this moment. A
        # RE-calibration must keep all history, so this runs only once.
        models.delete_hvac_readings_before(appliance_id, config.now_wib())
    mqtt.send_node_command(mac, "offsetcalibrationsuccessack")
    print(f"Calibration appliance {appliance_id} SUCCESS: "
          f"treturn_offset={treturn_offset}, tsupply_offset={tsupply_offset}")


def handle_calibration_fail_request(mac, appliance_id):
    """Firmware timeout / approval-timeout: revert to calibration needed,
    restoring the offsets that were zeroed when the session began."""
    tracker = CALIBRATION_TRACKER.pop(appliance_id, None) or {}
    appliance = models.get_appliance(appliance_id)
    if not appliance:
        return
    if appliance["operational_status"] == "calibrating":
        prev = tracker.get("prev_offsets")
        if prev is not None:
            models.update_offsets(appliance_id, prev[0], prev[1])
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
        if reconcile_calibration_state(appliance):
            # Session died (node cut off / rebooted / timed out): revert so the
            # UI offers a restart.
            return jsonify({"progress_pct": 100})
        tracker = CALIBRATION_TRACKER.get(appliance_id, {})
        delta = tracker.get("delta") or 0.0
        start_time = tracker.get("start_time")
        elapsed = (
            (config.now_wib() - config.to_wib(start_time)).total_seconds()
            if start_time else 0.0
        )
        return jsonify({
            "state": "calibrating",
            "ds1": tracker.get("ds1"),
            "base_ds1": tracker.get("base_ds1"),
            "delta": round(delta, 2),
            "required": REQUIRED_DROP,
            "progress_pct": min(100, max(0, int(delta / REQUIRED_DROP * 100))),
            "elapsed_s": int(elapsed),
            "timeout_s": TIMEOUT_SECONDS,
        })
    return jsonify({"progress_pct": 100})


@calibration_bp.route("/api/device/<int:appliance_id>/recalibrate", methods=["POST"])
@login_required
def api_recalibrate(appliance_id):
    """Arm a re-calibration without losing data: status -> offset_calibration_needed
    and the node is told immediately (telemetry gate closes until calibration
    succeeds). The user then runs the normal hidden-button flow on the hardware."""
    appliance = models.get_appliance(appliance_id)
    if not appliance or appliance["user_id"] != current_user.id:
        return jsonify({"error": "not found"}), 404
    if "Dryer" in appliance["type"]:
        return jsonify({"error": "dryer does not require offset calibration"}), 400
    if appliance["operational_status"] != "normal":
        return jsonify({"error":
            f"cannot recalibrate while status is {appliance['operational_status']}"}), 409

    models.update_operational_status(appliance_id, "offset_calibration_needed")
    node = models.get_node_by_appliance(appliance_id)
    if node:
        mqtt.send_node_command(node["mac_address"], "restore:offsetcalibrationneeded")
    return jsonify({"ok": True})


@calibration_bp.route("/api/device/<int:appliance_id>/calibration/cancel", methods=["POST"])
@login_required
def api_cancel_calibration(appliance_id):
    """Cancel a pending (offset_calibration_needed) or running (calibrating)
    calibration and restore the exact prior state: previous offsets and status
    normal. The firmware state machine is reset (failack) and its telemetry
    gate re-opened (restore:normal), so the node streams again immediately."""
    appliance = models.get_appliance(appliance_id)
    if not appliance or appliance["user_id"] != current_user.id:
        return jsonify({"error": "not found"}), 404
    if "Dryer" in appliance["type"]:
        return jsonify({"error": "dryer does not require offset calibration"}), 400

    status = appliance["operational_status"]
    if status not in ("offset_calibration_needed", "calibrating"):
        return jsonify({"error": f"nothing to cancel while status is {status}"}), 409
    if appliance.get("calibrated_at") is None:
        # Initial calibration is mandatory: there is nothing to fall back to,
        # so the device stays in its calibration state (no data shown) until
        # a calibration succeeds.
        return jsonify({"error": "initial calibration is required"}), 409

    tracker = CALIBRATION_TRACKER.pop(appliance_id, None) or {}
    node = models.get_node_by_appliance(appliance_id)
    mac = node["mac_address"] if node else None
    if mac:
        if status == "calibrating":
            # Reset the firmware calibration state machine to idle.
            mqtt.send_node_command(mac, "offsetcalibrationfailack")
        # Re-open the firmware telemetry gate (no-op if already open).
        mqtt.send_node_command(mac, "restore:normal")

    prev = tracker.get("prev_offsets")
    if prev is not None:
        models.update_offsets(appliance_id, prev[0], prev[1])
    models.update_operational_status(appliance_id, "normal")
    return jsonify({"ok": True})
