"""Fault alert logic for HVAC delta-T and dryer cycles."""

import queue
import threading
from datetime import datetime, timedelta

import requests

from app import config
from app.db import get_conn, release_conn
from app import models


# --- Delta-T tracking ---
DELTA_T_TRACKER = {}  # appliance_id -> {start_time, last_alert_time, low_current_start}

# --- Dryer cycle tracking ---
DRYER_CYCLE_STATS = {}      # appliance_id -> cycle state dict
FAULT_ALERT_TRACKER = {}    # appliance_id -> {fault_type: {last_trigger, active}}
FAULT_ALERT_COOLDOWN = {}   # (appliance_id, fault_type) -> last_alert_timestamp


FAULT_DISCORD_MAP = {
    "fault_dryer_incomplete_drying": {
        "title": "Clothes Not Fully Dried",
        "description": "Dryer cycle completed but clothes retain excessive moisture.",
        "cause": "Overloading, worn heating element, or short cycle.",
        "action": "Reduce load size and run another cycle.",
    },
    "fault_dryer_roller_wear": {
        "title": "Barrel Roller Worn Out",
        "description": "Motor is drawing more current than normal to maintain drum rotation.",
        "cause": "Support rollers under the drum are worn, increasing mechanical friction.",
        "action": "Inspect and replace drum support rollers.",
    },
    "fault_dryer_belt_snapped": {
        "title": "Belt Snapped",
        "description": "Drive belt connecting motor to drum has broken or slipped off.",
        "cause": "Age, overloading, or misalignment.",
        "action": "Replace drive belt immediately.",
    },
    "fault_dryer_lint_blockage": {
        "title": "Lint Blockage Detected",
        "description": "Lint accumulation is restricting exhaust airflow.",
        "cause": "Failure to clean lint filter or exhaust duct; exterior vent obstruction.",
        "action": "Clean lint filter and inspect exhaust duct.",
    },
    "fault_dryer_exhaust_ventilation_blockage": {
        "title": "Exhaust Ventilation Blockage",
        "description": "Exhaust airflow is severely restricted.",
        "cause": "Lint buildup, blocked duct, or exterior vent obstruction.",
        "action": "Clean lint filter, inspect exhaust duct, and check exterior vent.",
    },
    "fault_hvac_low_delta_t": {
        "title": "HVAC Delta-T Below Limit",
        "description": "Temperature split across the evaporator coil has stayed below the configured minimum value while the compressor is running.",
        "cause": "Low refrigerant, dirty filter, or compressor issue.",
        "action": "Check air filter and schedule HVAC technician if alert persists.",
    },
}


def _get_appliance_alert_gates(appliance_id, cur):
    cur.execute(
        "SELECT baseline_configured, alert_enabled FROM appliances WHERE id = %s",
        (appliance_id,),
    )
    row = cur.fetchone()
    if not row:
        return False, False
    return bool(row[0]), bool(row[1])


def _insert_fault_alert(
    appliance_id, alert_type, message, value, threshold, severity, alert_time, cur, conn
):
    """Insert a fault alert with a 10-minute cooldown per fault type."""
    cooldown_key = (appliance_id, alert_type)
    last_alert = FAULT_ALERT_COOLDOWN.get(cooldown_key)
    if last_alert and (alert_time - last_alert).total_seconds() < 600:
        return

    try:
        cur.execute("""
            INSERT INTO alerts
            (appliance_id, alert_type, message, value, threshold, severity, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (appliance_id, alert_type, message, value, threshold, severity, alert_time))
        conn.commit()
        FAULT_ALERT_COOLDOWN[cooldown_key] = alert_time
        tracker = FAULT_ALERT_TRACKER.setdefault(appliance_id, {})
        ft = tracker.setdefault(alert_type, {"last_trigger": alert_time, "active": True})
        ft["last_trigger"] = alert_time
        ft["active"] = True
        send_discord_alert(appliance_id, alert_type, message, value, threshold, severity)
    except Exception as e:
        print(f"Fault alert insert error: {e}")


def _post_discord_embed(webhook_url, embed):
    """Synchronous POST of one embed; shared by the worker and the fallback."""
    try:
        try:
            _discord_queue.put_nowait((webhook_url, embed))
            _ensure_discord_worker()
        except queue.Full:
            # Queue saturated (webhook down for a while): never drop an alert
            # silently — post synchronously as a last resort.
            _post_discord_embed(webhook_url, embed)
    except Exception as e:
        print(f"Discord alert failed: {e}")


# GAP-4: alerts are dispatched on a background worker so a slow/dead webhook
# never stalls MQTT ingestion on the paho callback thread.
_discord_queue = queue.Queue(maxsize=50)
_discord_worker = None
_discord_worker_lock = threading.Lock()


def _discord_worker_loop():
    while True:
        item = _discord_queue.get()
        try:
            webhook_url, embed = item
            _post_discord_embed(webhook_url, embed)
        except Exception as e:
            print(f"Discord worker error: {e}")
        finally:
            _discord_queue.task_done()


def _ensure_discord_worker():
    global _discord_worker
    with _discord_worker_lock:
        if _discord_worker is None or not _discord_worker.is_alive():
            _discord_worker = threading.Thread(
                target=_discord_worker_loop, name="discord-alerts", daemon=True
            )
            _discord_worker.start()


def send_discord_alert(appliance_id, alert_type, message, value=None, threshold=None, severity="warning"):
    try:
        webhook_url = models.get_user_webhook(appliance_id)
        if not webhook_url:
            return

        app_name = models.get_appliance_name(appliance_id)
        fault_meta = FAULT_DISCORD_MAP.get(alert_type)

        if severity == "critical":
            embed_color = 0xEF4444
            severity_prefix = "CRITICAL: "
        else:
            embed_color = 0xF59E0B
            severity_prefix = "WARNING: "

        if fault_meta:
            embed = {
                "title": f"{severity_prefix}{fault_meta['title']}",
                "description": (
                    f"{message}\n\n"
                    f"**Appliance:** {app_name or f'ID {appliance_id}'}\n"
                    f"**Cause:** {fault_meta['cause']}\n"
                    f"**Recommended Action:** {fault_meta['action']}"
                ),
                "color": embed_color,
                "timestamp": datetime.now(config.TIMEZONE).isoformat(),
                "footer": {"text": "IoT Monitoring & Predictive Maintenance"},
            }
        else:
            embed = {
                "title": f"{alert_type.replace('_', ' ').title()}",
                "description": message,
                "color": 0x64748B,
                "fields": [
                    {"name": "Appliance", "value": app_name or f"ID {appliance_id}", "inline": True},
                    {"name": "Value", "value": str(value) if value is not None else "N/A", "inline": True},
                    {"name": "Threshold", "value": str(threshold) if threshold is not None else "N/A", "inline": True},
                ],
                "timestamp": datetime.now(config.TIMEZONE).isoformat(),
                "footer": {"text": "IoT Monitoring & Predictive Maintenance"},
            }

        resp = requests.post(webhook_url, json={"embeds": [embed]}, timeout=5)
        if resp.status_code not in (200, 204):
            print(f"Discord webhook returned {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"Discord alert failed: {e}")


# ---------------------------------------------------------------------------
# HVAC Delta-T alert
# ---------------------------------------------------------------------------

def check_hvac_delta_t_alert(appliance_id, reading_data):
    appliance = models.get_appliance(appliance_id)
    if not appliance:
        return

    if not appliance.get("baseline_configured") or not appliance.get("alert_enabled"):
        return

    delta_t_lcl = appliance.get("delta_t_lcl")
    delay_minutes = appliance.get("delta_t_delay_minutes")
    if delta_t_lcl is None or delay_minutes is None:
        return

    current = reading_data.get("current", 0.0)
    delta_t = reading_data.get("delta_t", 0.0)
    actual_time = config.to_wib(reading_data.get("_actual_time"))

    tracker = DELTA_T_TRACKER.setdefault(appliance_id, {
        "in_run": False,
        "dip_start": None,
        "below_since": None,
        "alerted_in_run": False,
        "last_ts": None,
        "last_alert_time": None,
    })

    # Stale/backlogged readings (offline-buffer flushes carry backdated
    # timestamps and can arrive AFTER newer live readings) must never drive
    # the alert state machine — they replay history the FSM has already
    # passed. The reading itself is still stored by telemetry.py.
    if tracker["last_ts"] is not None and actual_time <= tracker["last_ts"]:
        return

    # Gap close: > 30 s of sample-time silence means the compressor was off
    # OR the node disconnected (live cadence is 10 s). This is the only way a
    # disconnect — whose idle readings sat in the offline buffer and arrive
    # later as stale backfill — can close the run.
    if tracker["last_ts"] is not None and (actual_time - tracker["last_ts"]).total_seconds() > 30:
        tracker["in_run"] = False
        tracker["below_since"] = None
        tracker["alerted_in_run"] = False
    tracker["last_ts"] = actual_time

    if current < config.RUNNING_CURRENT_THRESHOLD:
        # Idle-duration close: normal off-cycles keep sending idle readings
        # every 10 s (no sample gap), so the run closes here instead.
        if tracker["in_run"]:
            if tracker["dip_start"] is None:
                tracker["dip_start"] = actual_time
            elif (actual_time - tracker["dip_start"]).total_seconds() > 30:
                tracker["in_run"] = False
                tracker["dip_start"] = None
                tracker["below_since"] = None
                tracker["alerted_in_run"] = False
        return

    # Running reading: a short dip ends without consequences.
    tracker["dip_start"] = None
    if not tracker["in_run"]:
        # Run start (first running reading after > 30 s idle). The evaluation
        # window ALWAYS restarts here — never from stale/cached data.
        tracker["in_run"] = True
        tracker["below_since"] = None
        tracker["alerted_in_run"] = False

    if delta_t >= delta_t_lcl:
        # Healthy: pause the continuous below-minimum clock. The latch is
        # NOT cleared — exactly one alert per compressor run.
        tracker["below_since"] = None
        return

    if tracker["below_since"] is None:
        tracker["below_since"] = actual_time
        return

    elapsed = (actual_time - tracker["below_since"]).total_seconds()
    if elapsed >= delay_minutes * 60:
        # One alert per compressor run: a faulted AC running for hours must
        # not spam alerts. The latch holds for the whole run (even across
        # healthy bounces) and clears only at the next run start.
        if not tracker["alerted_in_run"]:
            # Fire: DB insert first, latch immediately, Discord best-effort —
            # a webhook failure must never suppress or repeat an alert.
            alert_id = models.insert_alert(
                appliance_id=appliance_id,
                alert_type="fault_hvac_low_delta_t",
                message=(
                    f"Delta-T {delta_t:.2f}C below minimum {delta_t_lcl:.2f}C "
                    f"for {delay_minutes} minutes (current {current:.2f}A)"
                ),
                value=delta_t,
                threshold=delta_t_lcl,
                severity="warning",
            )
            tracker["last_alert_time"] = actual_time
            tracker["alerted_in_run"] = True
            print(f"DELTA-T ALERT fired: appliance {appliance_id} alert {alert_id} "
                  f"at {actual_time} (delta {delta_t:.2f}C, min {delta_t_lcl:.2f}C)")
            try:
                send_discord_alert(
                    appliance_id,
                    "fault_hvac_low_delta_t",
                    f"Delta-T stayed below the minimum value for {delay_minutes} minutes.",
                    delta_t,
                    delta_t_lcl,
                    "warning",
                )
            except Exception as e:
                print(f"Discord delivery failed for appliance {appliance_id}: {e}")


# ---------------------------------------------------------------------------
# Dryer fault detection (ported from v4)
# ---------------------------------------------------------------------------

def _compute_motor_baseline_median(motor_readings, filter_threshold=None):
    if not motor_readings:
        return 0.0
    readings = motor_readings
    if filter_threshold is not None:
        readings = [r for r in readings if r <= filter_threshold]
        if not readings:
            return 0.0
    sorted_r = sorted(readings)
    n = len(sorted_r)
    if n % 2 == 1:
        return sorted_r[n // 2]
    return (sorted_r[n // 2 - 1] + sorted_r[n // 2]) / 2.0


def check_dryer_faults(appliance_id, reading_data):
    conn = get_conn()
    if not conn:
        return
    cur = conn.cursor()
    try:
        baseline_configured, alert_enabled = _get_appliance_alert_gates(appliance_id, cur)
        if not baseline_configured or not alert_enabled:
            return

        baselines = models.get_spc_baselines(appliance_id)
        now = config.now_wib()
        _check_dryer_faults(appliance_id, reading_data, baselines, now, cur, conn)
    finally:
        cur.close()
        release_conn(conn)


def _check_dryer_faults(appliance_id, reading_data, baselines, now, cur, conn):
    current = reading_data.get("current", 0.0)
    texhaust = reading_data.get("texhaust", 0.0)
    rhexhaust = reading_data.get("rhexhaust", 0.0)
    gauge_pressure = reading_data.get("pressure")
    actual_time = config.to_wib(reading_data.get("_actual_time", now))

    stats = DRYER_CYCLE_STATS.setdefault(appliance_id, {})
    mean_current = baselines.get("current", {}).get("mean", 2.0)

    # Cycle end due to gap > 120s
    if stats.get("in_cycle", False) and "last_time" in stats:
        gap = (actual_time - stats["last_time"]).total_seconds()
        if gap > 120:
            _finalize_dryer_cycle(appliance_id, baselines, actual_time)
            stats = DRYER_CYCLE_STATS[appliance_id] = {}

    # Start new cycle
    if current >= config.RUNNING_CURRENT_THRESHOLD and not stats.get("in_cycle", False):
        DRYER_CYCLE_STATS[appliance_id] = {
            "in_cycle": True,
            "start_time": actual_time,
            "last_time": actual_time,
            "idle_start_time": None,
            "motor_readings": [current],
            "spike_peaks": [],
            "spike_state": "IDLE",
            "spike_max": 0.0,
            "spike_valley": 0.0,
            "prev_current": current,
            "min_current": current,
            "max_temp": texhaust,
            "max_gauge_pressure": gauge_pressure if gauge_pressure is not None else 0.0,
            "temp_history": [texhaust],
            "rh_history": [rhexhaust] if rhexhaust is not None else [],
            "consecutive_below_lcl": 0,
            "belt_snap_triggered": False,
            "roller_wear_triggered": False,
        }
        return

    if stats.get("in_cycle", False):
        stats["last_time"] = actual_time
        if texhaust is not None:
            stats["max_temp"] = max(stats.get("max_temp", texhaust) or texhaust, texhaust)
        if gauge_pressure is not None:
            stats["max_gauge_pressure"] = max(
                stats.get("max_gauge_pressure", gauge_pressure) or gauge_pressure,
                gauge_pressure,
            )
        stats.setdefault("temp_history", []).append(texhaust)
        if rhexhaust is not None:
            stats.setdefault("rh_history", []).append(rhexhaust)
        stats["min_current"] = min(stats.get("min_current", 999.0), current)

        if current >= config.RUNNING_CURRENT_THRESHOLD:
            if stats.get("idle_start_time") is not None:
                stats["idle_start_time"] = None

            stats.setdefault("motor_readings", []).append(current)
            prev = stats.get("prev_current", 0.0)
            prominence = 0.40
            state = stats.get("spike_state", "IDLE")
            spike_max = stats.get("spike_max", 0.0)
            spike_valley = stats.get("spike_valley", 0.0)

            if current > prev:
                if state == "FALLING":
                    if spike_max > 0:
                        prom = spike_max - spike_valley
                        if prom >= prominence and spike_max > mean_current + 0.15:
                            stats.setdefault("spike_peaks", []).append(spike_max)
                    spike_max = 0.0
                    spike_valley = 0.0
                if state in ("IDLE", "FALLING"):
                    spike_valley = prev
                state = "RISING"
                if current > spike_max:
                    spike_max = current
            elif current < prev:
                if state == "RISING" and (spike_max <= 0.1 or current < spike_max - 0.1):
                    state = "FALLING"

            stats["spike_state"] = state
            stats["spike_max"] = spike_max
            stats["spike_valley"] = spike_valley

            # Belt snap detection
            current_lcl = baselines.get("current", {}).get("lcl")
            if current_lcl is not None:
                if current < current_lcl:
                    stats["consecutive_below_lcl"] = stats.get("consecutive_below_lcl", 0) + 1
                else:
                    stats["consecutive_below_lcl"] = 0

                if (
                    stats["consecutive_below_lcl"] >= 3
                    and not stats.get("belt_snap_triggered", False)
                ):
                    _insert_fault_alert(
                        appliance_id,
                        "fault_dryer_belt_snapped",
                        (
                            f"Belt snapped - motor current dropped below minimum {current_lcl:.3f}A "
                            f"for 3 consecutive readings (last: {current:.3f}A)"
                        ),
                        current,
                        current_lcl,
                        "critical",
                        actual_time,
                        cur,
                        conn,
                    )
                    stats["belt_snap_triggered"] = True

            # Roller wear detection
            current_ucl = baselines.get("current", {}).get("ucl")
            if current_ucl is not None:
                if current > current_ucl:
                    stats["consecutive_above_ucl"] = stats.get("consecutive_above_ucl", 0) + 1
                else:
                    stats["consecutive_above_ucl"] = 0
                if (
                    stats["consecutive_above_ucl"] >= 3
                    and not stats.get("roller_wear_triggered", False)
                ):
                    _insert_fault_alert(
                        appliance_id,
                        "fault_dryer_roller_wear",
                        (
                            f"Barrel roller worn out - motor current exceeded maximum {current_ucl:.3f}A "
                            f"for 3 consecutive readings (last: {current:.3f}A)"
                        ),
                        current,
                        current_ucl,
                        "warning",
                        actual_time,
                        cur,
                        conn,
                    )
                    stats["roller_wear_triggered"] = True
        else:
            # Idle during cycle
            stats["consecutive_below_lcl"] = 0
            stats["consecutive_above_ucl"] = 0
            if stats.get("idle_start_time") is None:
                stats["idle_start_time"] = actual_time
            idle_duration = (actual_time - stats["idle_start_time"]).total_seconds()
            if idle_duration >= 120:
                _finalize_dryer_cycle(appliance_id, baselines, actual_time)
                return

        stats["prev_current"] = current


def _finalize_dryer_cycle(appliance_id, baselines, now):
    stats = DRYER_CYCLE_STATS.get(appliance_id, {})
    if not stats or not stats.get("in_cycle", False):
        return

    cycle_end_time = stats.get("last_time", now)

    conn = get_conn()
    if not conn:
        DRYER_CYCLE_STATS[appliance_id] = {}
        return
    cur = conn.cursor()
    try:
        baseline_configured, alert_enabled = _get_appliance_alert_gates(appliance_id, cur)
        if not baseline_configured or not alert_enabled:
            DRYER_CYCLE_STATS[appliance_id] = {}
            return

        motor_readings = stats.get("motor_readings", [])
        rh_history = stats.get("rh_history", [])
        max_temp = stats.get("max_temp", 0.0)

        mean_current = baselines.get("current", {}).get("mean", 2.0)
        prominence = 0.40
        spike_state = stats.get("spike_state", "IDLE")
        spike_max = stats.get("spike_max", 0.0)
        spike_valley = stats.get("spike_valley", 0.0)
        if spike_state in ("RISING", "FALLING") and spike_max > 0:
            prom = spike_max - spike_valley
            if prom >= prominence and spike_max > mean_current + 0.15:
                stats.setdefault("spike_peaks", []).append(spike_max)

        end_rh_avg = 0.0
        if rh_history:
            last_rh = rh_history[-6:] if len(rh_history) > 6 else rh_history
            end_rh_avg = sum(last_rh) / len(last_rh)

        max_gauge_pressure = stats.get("max_gauge_pressure", 0.0)

        # Exhaust ventilation blockage
        rhexhaust_ucl = baselines.get("rhexhaust", {}).get("ucl")
        texhaust_ucl = baselines.get("texhaust", {}).get("ucl")
        pressure_ucl = baselines.get("pressure", {}).get("ucl")
        if rhexhaust_ucl is not None and texhaust_ucl is not None:
            if end_rh_avg > rhexhaust_ucl and max_temp > texhaust_ucl:
                if pressure_ucl is not None and max_gauge_pressure > pressure_ucl:
                    severity = "critical"
                    msg = (
                        f"CRITICAL - Exhaust ventilation blockage detected: "
                        f"gauge pressure {max_gauge_pressure:.2f} hPa > maximum {pressure_ucl:.2f} hPa, "
                        f"end RH {end_rh_avg:.1f}% > maximum {rhexhaust_ucl:.1f}%, "
                        f"max temp {max_temp:.1f}C > maximum {texhaust_ucl:.1f}C"
                    )
                else:
                    severity = "warning"
                    msg = (
                        f"Warning - High exhaust RH and temperature: "
                        f"end RH {end_rh_avg:.1f}% > maximum {rhexhaust_ucl:.1f}%, "
                        f"max temp {max_temp:.1f}C > maximum {texhaust_ucl:.1f}C"
                    )
                _insert_fault_alert(
                    appliance_id,
                    "fault_dryer_exhaust_ventilation_blockage",
                    msg,
                    max_gauge_pressure if severity == "critical" else end_rh_avg,
                    pressure_ucl if severity == "critical" else rhexhaust_ucl,
                    severity,
                    cycle_end_time,
                    cur,
                    conn,
                )

        # Incomplete drying
        if rhexhaust_ucl is not None and end_rh_avg > rhexhaust_ucl:
            if end_rh_avg > 90.0:
                severity = "critical"
                msg = (
                    f"Severely incomplete drying - end RH {end_rh_avg:.1f}% exceeds 90% "
                    f"(not drying at all)"
                )
            else:
                severity = "warning"
                msg = (
                    f"Clothes not fully dried - end RH {end_rh_avg:.1f}% exceeds maximum "
                    f"{rhexhaust_ucl:.1f}%"
                )
            _insert_fault_alert(
                appliance_id,
                "fault_dryer_incomplete_drying",
                msg,
                end_rh_avg,
                rhexhaust_ucl,
                severity,
                cycle_end_time,
                cur,
                conn,
            )

        # Belt snap end-of-cycle backup
        current_lcl = baselines.get("current", {}).get("lcl")
        if current_lcl is not None and not stats.get("belt_snap_triggered", False):
            last_3 = motor_readings[-3:] if len(motor_readings) >= 3 else motor_readings
            if len(last_3) >= 3 and all(r < current_lcl for r in last_3):
                _insert_fault_alert(
                    appliance_id,
                    "fault_dryer_belt_snapped",
                    (
                        f"Belt snapped - last 3 motor readings all below minimum {current_lcl:.3f}A"
                    ),
                    last_3[-1],
                    current_lcl,
                    "critical",
                    cycle_end_time,
                    cur,
                    conn,
                )

        # Roller wear end-of-cycle backup
        current_ucl = baselines.get("current", {}).get("ucl")
        if current_ucl is not None and not stats.get("roller_wear_triggered", False):
            if motor_readings:
                filter_threshold = (sum(motor_readings) / len(motor_readings)) * 1.15
                median_current = _compute_motor_baseline_median(
                    motor_readings, filter_threshold=filter_threshold
                )
                if median_current > current_ucl:
                    _insert_fault_alert(
                        appliance_id,
                        "fault_dryer_roller_wear",
                        (
                            f"Barrel roller worn out - motor baseline median {median_current:.3f}A "
                            f"exceeded maximum {current_ucl:.3f}A during cycle"
                        ),
                        median_current,
                        current_ucl,
                        "warning",
                        cycle_end_time,
                        cur,
                        conn,
                    )
    finally:
        cur.close()
        release_conn(conn)
        DRYER_CYCLE_STATS[appliance_id] = {}


def sweep_dryer_cycles(actual_time):
    for appliance_id, stats in list(DRYER_CYCLE_STATS.items()):
        if stats.get("in_cycle", False) and "last_time" in stats:
            gap = (actual_time - stats["last_time"]).total_seconds()
            if gap > 120:
                baselines = models.get_spc_baselines(appliance_id)
                _finalize_dryer_cycle(appliance_id, baselines, actual_time)


def clear_appliance_trackers(appliance_id):
    DELTA_T_TRACKER.pop(appliance_id, None)
    DRYER_CYCLE_STATS.pop(appliance_id, None)
    FAULT_ALERT_TRACKER.pop(appliance_id, None)
    keys = [k for k in FAULT_ALERT_COOLDOWN if k[0] == appliance_id]
    for k in keys:
        FAULT_ALERT_COOLDOWN.pop(k, None)


# ---------------------------------------------------------------------------
# Dryer cycle rehydration (GAP-5)
# ---------------------------------------------------------------------------

REHYDRATE_READING_LIMIT = 2000          # ~5.5 h of readings at a 10 s cadence
REHYDRATE_COOLDOWN_WINDOW_HOURS = 24    # alerts fired within this window re-arm cooldowns


def _seed_alert_cooldowns_from_db(appliance_id, cur):
    """Re-arm per-(appliance, fault-type) cooldowns from alerts already fired
    recently, so replaying history after a restart does not re-notify."""
    cutoff = config.now_wib() - timedelta(hours=REHYDRATE_COOLDOWN_WINDOW_HOURS)
    cur.execute(
        """
        SELECT alert_type, MAX(created_at)
        FROM alerts
        WHERE appliance_id = %s AND created_at >= %s
        GROUP BY alert_type
        """,
        (appliance_id, cutoff),
    )
    for alert_type, created_at in cur.fetchall():
        if created_at is not None:
            FAULT_ALERT_COOLDOWN[(appliance_id, alert_type)] = created_at


def rehydrate_dryer_cycles():
    """Rebuild in-memory dryer cycle state from DB readings at startup.

    Cycle stats are RAM-only; without this, a backend restart mid-cycle would
    silently skip that cycle's end-of-cycle fault evaluation. Replays the
    trailing contiguous readings (gap <= 120 s) through the normal engine
    with alert insertion enabled, after seeding cooldowns from the alerts
    table. Never raises — rehydration must not break startup.
    """
    conn = get_conn()
    if not conn:
        return
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT DISTINCT a.id
            FROM appliances a
            JOIN sensor_nodes sn ON sn.appliance_id = a.id
            WHERE a.type LIKE %s AND sn.status = 'paired'
            """,
            ("%Dryer%",),
        )
        appliance_ids = [row[0] for row in cur.fetchall()]
    finally:
        cur.close()
        release_conn(conn)

    for appliance_id in appliance_ids:
        try:
            _rehydrate_one_dryer(appliance_id)
        except Exception as e:
            print(f"Dryer rehydration failed for appliance {appliance_id}: {e}")


def _rehydrate_one_dryer(appliance_id):
    readings = models.get_recent_dryer_readings(
        appliance_id, limit=REHYDRATE_READING_LIMIT
    )
    if not readings:
        return

    # Replay only the trailing contiguous block (gap <= 120 s between
    # consecutive readings). Over-inclusion is safe: the engine replicates
    # its own idle-finalize segmentation while replaying.
    start_idx = len(readings) - 1
    while start_idx > 0:
        gap = (
            readings[start_idx]["time"] - readings[start_idx - 1]["time"]
        ).total_seconds()
        if gap > 120:
            break
        start_idx -= 1

    conn = get_conn()
    if not conn:
        return
    cur = conn.cursor()
    try:
        baseline_configured, alert_enabled = _get_appliance_alert_gates(
            appliance_id, cur
        )
        if not baseline_configured or not alert_enabled:
            return
        _seed_alert_cooldowns_from_db(appliance_id, cur)
        baselines = models.get_spc_baselines(appliance_id)
        for r in readings[start_idx:]:
            reading_data = {
                "texhaust": r["texhaust"],
                "rhexhaust": r["rh_exhaust"],
                "pressure": r["pressure"],
                "current": max(0.0, float(r["imotor"] or 0.0)),
                "_actual_time": config.to_wib(r["time"]),
            }
            _check_dryer_faults(
                appliance_id, reading_data, baselines,
                reading_data["_actual_time"], cur, conn,
            )

        # If the rebuilt cycle already ended physically while the backend was
        # down (last reading older than the 120 s boundary), finalize it NOW so
        # its end-of-cycle faults are evaluated immediately instead of waiting
        # for the next telemetry message's sweep.
        stats = DRYER_CYCLE_STATS.get(appliance_id, {})
        if stats.get("in_cycle") and "last_time" in stats:
            age = (config.now_wib() - stats["last_time"]).total_seconds()
            if age > 120:
                _finalize_dryer_cycle(appliance_id, baselines, config.now_wib())
    finally:
        cur.close()
        release_conn(conn)
