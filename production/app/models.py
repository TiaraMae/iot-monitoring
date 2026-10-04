"""Database helper functions."""

from datetime import timedelta

from app import config
from app.db import get_conn, release_conn


def _to_dict(cursor, row):
    if row is None:
        return None
    cols = [desc[0] for desc in cursor.description]
    return {col: val for col, val in zip(cols, row)}


def get_user_by_id(user_id):
    conn = get_conn()
    if not conn:
        return None
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id, email, name FROM users WHERE id = %s", (user_id,)
        )
        return _to_dict(cur, cur.fetchone())
    finally:
        cur.close()
        release_conn(conn)


def get_user_by_email(email):
    conn = get_conn()
    if not conn:
        return None
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id, email, name, password_hash FROM users WHERE email = %s",
            (email,),
        )
        return _to_dict(cur, cur.fetchone())
    finally:
        cur.close()
        release_conn(conn)


def create_user(email, password_hash, name):
    conn = get_conn()
    if not conn:
        return None
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO users (email, password_hash, name) VALUES (%s, %s, %s) RETURNING id",
            (email, password_hash, name),
        )
        user_id = cur.fetchone()[0]
        conn.commit()
        return user_id
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        release_conn(conn)


def get_appliance(appliance_id):
    conn = get_conn()
    if not conn:
        return None
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM appliances WHERE id = %s", (appliance_id,))
        return _to_dict(cur, cur.fetchone())
    finally:
        cur.close()
        release_conn(conn)


def get_appliances_for_user(user_id):
    conn = get_conn()
    if not conn:
        return []
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT id, name, type, sub_type, is_inverter, brand, location,
                   created_at, operational_status, baseline_configured,
                   alert_enabled,
                   treturn_offset, tsupply_offset, delta_t_lcl,
                   delta_t_delay_minutes, atmospheric_pressure,
                   map_x, map_y, map_w, map_h, map_on_map, current_sensor
            FROM appliances
            WHERE user_id = %s
            ORDER BY created_at
        """, (user_id,))
        rows = cur.fetchall()
        appliances = []
        for r in rows:
            app = {
                "id": r[0],
                "name": r[1],
                "type": r[2],
                "sub_type": r[3],
                "is_inverter": r[4],
                "brand": r[5],
                "location": r[6],
                "created_at": r[7].isoformat() if r[7] else None,
                "status": r[8],
                "baseline_configured": r[9],
                "alert_enabled": r[10],
                "treturn_offset": r[11],
                "tsupply_offset": r[12],
                "delta_t_lcl": r[13],
                "delta_t_delay_minutes": r[14],
                "atmospheric_pressure": r[15],
                "map_x": r[16],
                "map_y": r[17],
                "map_w": r[18],
                "map_h": r[19],
                "map_on_map": r[20],
                "current_sensor": r[21],
                "alert_status": get_appliance_alert_status(r[0]),
            }
            appliances.append(app)
        return appliances
    finally:
        cur.close()
        release_conn(conn)


def get_sensor_node_by_mac(mac):
    conn = get_conn()
    if not conn:
        return None
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM sensor_nodes WHERE mac_address = %s", (mac,))
        return _to_dict(cur, cur.fetchone())
    finally:
        cur.close()
        release_conn(conn)


def get_node_by_appliance(appliance_id):
    conn = get_conn()
    if not conn:
        return None
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT * FROM sensor_nodes WHERE appliance_id = %s", (appliance_id,)
        )
        return _to_dict(cur, cur.fetchone())
    finally:
        cur.close()
        release_conn(conn)


def get_node_by_id(node_id):
    conn = get_conn()
    if not conn:
        return None
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM sensor_nodes WHERE id = %s", (node_id,))
        return _to_dict(cur, cur.fetchone())
    finally:
        cur.close()
        release_conn(conn)


def register_unpaired_node(mac):
    conn = get_conn()
    if not conn:
        return None
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO sensor_nodes (mac_address, status, last_seen)
            VALUES (%s, 'unpaired', NOW())
            ON CONFLICT (mac_address) DO UPDATE SET last_seen = NOW()
            RETURNING id
        """, (mac,))
        node_id = cur.fetchone()[0]
        conn.commit()
        return node_id
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        release_conn(conn)


def pair_node(node_id, appliance_id):
    conn = get_conn()
    if not conn:
        return False
    cur = conn.cursor()
    try:
        cur.execute("""
            UPDATE sensor_nodes
            SET appliance_id = %s, status = 'paired'
            WHERE id = %s
        """, (appliance_id, node_id))
        conn.commit()
        return cur.rowcount > 0
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        release_conn(conn)


def unpair_node_by_appliance(appliance_id):
    """Forget device: detach the node and permanently wipe every trace of the
    appliance — readings, sensor events (maintenance history), alerts, SPC
    baselines, and the appliance row itself. All in one transaction; the
    sensor_nodes row is kept as 'unpaired' so the MAC stays discoverable."""
    conn = get_conn()
    if not conn:
        return None
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE sensor_nodes SET appliance_id = NULL, status = 'unpaired' WHERE appliance_id = %s RETURNING id, mac_address",
            (appliance_id,),
        )
        row = cur.fetchone()
        if row:
            node_id, mac = row
            cur.execute("DELETE FROM hvac_readings WHERE sensor_node_id = %s", (node_id,))
            cur.execute("DELETE FROM dryer_readings WHERE sensor_node_id = %s", (node_id,))
            cur.execute("DELETE FROM sensor_events WHERE sensor_node_mac = %s", (mac,))
        cur.execute("DELETE FROM alerts WHERE appliance_id = %s", (appliance_id,))
        cur.execute("DELETE FROM spc_manual_baselines WHERE appliance_id = %s", (appliance_id,))
        cur.execute("DELETE FROM appliances WHERE id = %s", (appliance_id,))
        conn.commit()
        return mac if row else None
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        release_conn(conn)


def insert_hvac_reading(sensor_node_id, time, treturn, tsupply, icompressor):
    conn = get_conn()
    if not conn:
        return None
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO hvac_readings (sensor_node_id, time, treturn, tsupply, icompressor)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
        """, (sensor_node_id, time, treturn, tsupply, icompressor))
        reading_id = cur.fetchone()[0]
        conn.commit()
        return reading_id
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        release_conn(conn)


def insert_dryer_reading(
    sensor_node_id, time, texhaust, rh_exhaust, pressure, abs_pressure, imotor
):
    conn = get_conn()
    if not conn:
        return None
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO dryer_readings
            (sensor_node_id, time, texhaust, rh_exhaust, pressure, abs_pressure, imotor)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (sensor_node_id, time, texhaust, rh_exhaust, pressure, abs_pressure, imotor))
        reading_id = cur.fetchone()[0]
        conn.commit()
        return reading_id
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        release_conn(conn)


def get_latest_hvac_readings(appliance_id, limit=15):
    conn = get_conn()
    if not conn:
        return []
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT hr.time, hr.treturn, hr.tsupply, hr.icompressor
            FROM hvac_readings hr
            JOIN sensor_nodes sn ON hr.sensor_node_id = sn.id
            WHERE sn.appliance_id = %s
            ORDER BY hr.time DESC
            LIMIT %s
        """, (appliance_id, limit))
        rows = cur.fetchall()
        return [
            {
                "time": r[0],
                "treturn": r[1],
                "tsupply": r[2],
                "icompressor": r[3],
            }
            for r in rows
        ]
    finally:
        cur.close()
        release_conn(conn)


def get_latest_dryer_reading(appliance_id):
    conn = get_conn()
    if not conn:
        return None
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT dr.time, dr.texhaust, dr.rh_exhaust, dr.pressure, dr.abs_pressure, dr.imotor
            FROM dryer_readings dr
            JOIN sensor_nodes sn ON dr.sensor_node_id = sn.id
            WHERE sn.appliance_id = %s
            ORDER BY dr.time DESC
            LIMIT 1
        """, (appliance_id,))
        row = cur.fetchone()
        if not row:
            return None
        return {
            "time": row[0],
            "texhaust": row[1],
            "rh_exhaust": row[2],
            "pressure": row[3],
            "abs_pressure": row[4],
            "imotor": row[5],
        }
    finally:
        cur.close()
        release_conn(conn)


def get_latest_hvac_reading(appliance_id):
    readings = get_latest_hvac_readings(appliance_id, limit=1)
    return readings[0] if readings else None


def compute_avg_running_delta_t(rows, threshold, warmup_minutes):
    """Average signed delta-T (treturn - tsupply, no abs) over running readings.

    rows: chronologically ordered dicts with keys time, treturn, tsupply,
    icompressor. A running segment starts at the first reading with
    icompressor >= threshold after an idle one; readings within the first
    `warmup_minutes` of a segment are skipped (starting transients). If the
    window begins mid-run, the first row's time is treated as the segment
    start. Returns None when no reading qualifies.
    """
    warmup = timedelta(minutes=warmup_minutes)
    segment_start = None
    total = 0.0
    count = 0
    for r in rows:
        running = (r.get("icompressor") or 0.0) >= threshold
        if not running:
            segment_start = None
            continue
        t = r.get("time")
        if segment_start is None:
            # idle->running transition (or window starting mid-run)
            segment_start = t
        if t is not None and segment_start is not None and t - segment_start < warmup:
            continue
        tret, tsup = r.get("treturn"), r.get("tsupply")
        if tret is None or tsup is None:
            continue
        total += tret - tsup
        count += 1
    return round(total / count, 2) if count else None


def compute_daily_running_averages(rows, threshold, warmup_minutes, limit=30):
    """Per-calendar-day average return/supply temperatures over RUNNING
    readings, skipping the first `warmup_minutes` of each compressor run —
    the same rule as compute_avg_running_delta_t, so the daily table's
    Avg Delta-T matches the device card's definition.

    rows: chronologically ordered dicts with keys time, treturn, tsupply,
    icompressor. Run segments are detected across the WHOLE series: an idle
    gap starts a new segment, but a day boundary does NOT — a run spanning
    midnight keeps its warmup window measured from the real run start.
    Returns newest-first: [{"date": "YYYY-MM-DD", "avg_return": x,
    "avg_supply": y}, ...] limited to the `limit` most recent days with data.
    """
    warmup = timedelta(minutes=warmup_minutes)
    segment_start = None
    buckets = {}  # date -> [sum_return, sum_supply, count]
    for r in rows:
        running = (r.get("icompressor") or 0.0) >= threshold
        if not running:
            segment_start = None
            continue
        t = r.get("time")
        if segment_start is None:
            # idle->running transition (or series starting mid-run)
            segment_start = t
        if t is None or segment_start is None or t - segment_start < warmup:
            continue
        tret, tsup = r.get("treturn"), r.get("tsupply")
        if tret is None or tsup is None:
            continue
        b = buckets.setdefault(t.date(), [0.0, 0.0, 0])
        b[0] += tret
        b[1] += tsup
        b[2] += 1
    days = sorted(buckets.items(), key=lambda kv: kv[0], reverse=True)[:limit]
    return [
        {
            "date": d.isoformat(),
            "avg_return": round(s / c, 2),
            "avg_supply": round(p / c, 2),
        }
        for d, (s, p, c) in days
        if c
    ]


def get_avg_delta_t_running_24h(appliance_id):
    """Average running delta-T over the last 24 h (5-min per-run warmup applied)."""
    conn = get_conn()
    if not conn:
        return None
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT hr.time, hr.treturn, hr.tsupply, hr.icompressor
            FROM hvac_readings hr
            JOIN sensor_nodes sn ON hr.sensor_node_id = sn.id
            WHERE sn.appliance_id = %s
              AND hr.time >= NOW() - INTERVAL '24 hours'
            ORDER BY hr.time ASC
        """, (appliance_id,))
        rows = [
            {"time": r[0], "treturn": r[1], "tsupply": r[2], "icompressor": r[3]}
            for r in cur.fetchall()
        ]
    finally:
        cur.close()
        release_conn(conn)
    return compute_avg_running_delta_t(
        rows, config.RUNNING_CURRENT_THRESHOLD, config.AVG_DELTA_T_WARMUP_MINUTES
    )


def get_latest_current(appliance_id):
    conn = get_conn()
    if not conn:
        return 0.0
    cur = conn.cursor()
    try:
        cur.execute("SELECT type FROM appliances WHERE id = %s", (appliance_id,))
        type_row = cur.fetchone()
        if not type_row:
            return 0.0
        app_type = type_row[0]
        if "Dryer" in app_type:
            cur.execute("""
                SELECT dr.imotor FROM dryer_readings dr
                JOIN sensor_nodes sn ON dr.sensor_node_id = sn.id
                WHERE sn.appliance_id = %s
                ORDER BY dr.time DESC LIMIT 1
            """, (appliance_id,))
        else:
            cur.execute("""
                SELECT hr.icompressor FROM hvac_readings hr
                JOIN sensor_nodes sn ON hr.sensor_node_id = sn.id
                WHERE sn.appliance_id = %s
                ORDER BY hr.time DESC LIMIT 1
            """, (appliance_id,))
        row = cur.fetchone()
        return float(row[0]) if row and row[0] is not None else 0.0
    except Exception:
        return 0.0
    finally:
        cur.close()
        release_conn(conn)


def update_offsets(appliance_id, treturn_offset, tsupply_offset):
    conn = get_conn()
    if not conn:
        return False
    cur = conn.cursor()
    try:
        cur.execute("""
            UPDATE appliances
            SET treturn_offset = %s, tsupply_offset = %s, updated_at = NOW()
            WHERE id = %s
        """, (treturn_offset, tsupply_offset, appliance_id))
        conn.commit()
        return cur.rowcount > 0
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        release_conn(conn)


def mark_calibrated(appliance_id):
    """Record a successful calibration: `calibrated_at` refreshes every time;
    `initial_calibrated_at` (the display-window start) is written only on the
    FIRST success and never moves, so re-calibration keeps all history."""
    conn = get_conn()
    if not conn:
        return
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE appliances SET calibrated_at = NOW(), "
            "initial_calibrated_at = COALESCE(initial_calibrated_at, NOW()) "
            "WHERE id = %s",
            (appliance_id,),
        )
        conn.commit()
    finally:
        cur.close()
        release_conn(conn)


def delete_hvac_readings_before(appliance_id, ts):
    """Delete HVAC readings recorded before `ts` for this appliance. Used once —
    at the FIRST successful calibration — to drop the raw uncalibrated
    calibration-session window so data display starts at initial calibration."""
    conn = get_conn()
    if not conn:
        return
    cur = conn.cursor()
    try:
        cur.execute("""
            DELETE FROM hvac_readings
            WHERE sensor_node_id IN (SELECT id FROM sensor_nodes WHERE appliance_id = %s)
              AND time < %s
        """, (appliance_id, ts))
        conn.commit()
    finally:
        cur.close()
        release_conn(conn)


def update_operational_status(appliance_id, status):
    conn = get_conn()
    if not conn:
        return False
    cur = conn.cursor()
    try:
        cur.execute("""
            UPDATE appliances
            SET operational_status = %s, updated_at = NOW()
            WHERE id = %s
        """, (status, appliance_id))
        conn.commit()
        return cur.rowcount > 0
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        release_conn(conn)


def insert_alert(
    appliance_id, alert_type, message, value=None, threshold=None, severity="warning"
):
    conn = get_conn()
    if not conn:
        return None
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO alerts
            (appliance_id, alert_type, message, value, threshold, severity, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, NOW())
            RETURNING id
        """, (appliance_id, alert_type, message, value, threshold, severity))
        alert_id = cur.fetchone()[0]
        conn.commit()
        return alert_id
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        release_conn(conn)


def resolve_alert(alert_id):
    conn = get_conn()
    if not conn:
        return False
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE alerts SET resolved_at = NOW() WHERE id = %s", (alert_id,)
        )
        conn.commit()
        return cur.rowcount > 0
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        release_conn(conn)


def get_unresolved_alerts_for_appliance(appliance_id):
    conn = get_conn()
    if not conn:
        return []
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT id, alert_type, message, value, threshold, severity, created_at
            FROM alerts
            WHERE appliance_id = %s AND resolved_at IS NULL
            ORDER BY created_at DESC
        """, (appliance_id,))
        rows = cur.fetchall()
        return [
            {
                "id": r[0],
                "alert_type": r[1],
                "message": r[2],
                "value": r[3],
                "threshold": r[4],
                "severity": r[5],
                "created_at": r[6].isoformat() if r[6] else None,
            }
            for r in rows
        ]
    finally:
        cur.close()
        release_conn(conn)


def get_alerts_for_appliance(appliance_id):
    """Return all alerts for an appliance, including resolved ones."""
    conn = get_conn()
    if not conn:
        return []
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT id, alert_type, message, value, threshold, severity, created_at, resolved_at
            FROM alerts
            WHERE appliance_id = %s
            ORDER BY created_at DESC
        """, (appliance_id,))
        rows = cur.fetchall()
        return [
            {
                "id": r[0],
                "alert_type": r[1],
                "message": r[2],
                "value": r[3],
                "threshold": r[4],
                "severity": r[5],
                "created_at": r[6].isoformat() if r[6] else None,
                "resolved_at": r[7].isoformat() if r[7] else None,
            }
            for r in rows
        ]
    finally:
        cur.close()
        release_conn(conn)


def get_appliance_alert_status(appliance_id):
    conn = get_conn()
    if not conn:
        return "normal"
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT severity FROM alerts
            WHERE appliance_id = %s AND resolved_at IS NULL
            ORDER BY CASE severity
                WHEN 'critical' THEN 3
                WHEN 'warning' THEN 2
                ELSE 1
            END DESC
            LIMIT 1
        """, (appliance_id,))
        row = cur.fetchone()
        return row[0] if row and row[0] else "normal"
    except Exception as e:
        print(f"get_appliance_alert_status error: {e}")
        return "normal"
    finally:
        cur.close()
        release_conn(conn)


def get_spc_baselines(appliance_id):
    conn = get_conn()
    if not conn:
        return {}
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT metric_name, ucl, lcl, mean
            FROM spc_manual_baselines
            WHERE appliance_id = %s
        """, (appliance_id,))
        result = {}
        for r in cur.fetchall():
            result[r[0]] = {
                "ucl": float(r[1]) if r[1] is not None else None,
                "lcl": float(r[2]) if r[2] is not None else None,
                "mean": float(r[3]) if r[3] is not None else None,
            }
        return result
    finally:
        cur.close()
        release_conn(conn)


def get_spc_baselines_updated_at(appliance_id):
    conn = get_conn()
    if not conn:
        return None
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT MAX(updated_at) FROM spc_manual_baselines WHERE appliance_id = %s",
            (appliance_id,),
        )
        row = cur.fetchone()
        return row[0] if row and row[0] else None
    finally:
        cur.close()
        release_conn(conn)


def save_spc_baselines(appliance_id, baselines):
    """baselines: dict of metric_name -> {ucl, lcl, mean}."""
    conn = get_conn()
    if not conn:
        return False, "DB connection error"
    cur = conn.cursor()
    try:
        for metric, vals in baselines.items():
            ucl = float(vals["ucl"]) if vals.get("ucl") is not None else None
            lcl = float(vals["lcl"]) if vals.get("lcl") is not None else None
            mean = float(vals["mean"]) if vals.get("mean") is not None else None
            if mean is None and ucl is not None and lcl is not None:
                mean = (ucl + lcl) / 2.0
            cur.execute("""
                INSERT INTO spc_manual_baselines
                (appliance_id, metric_name, ucl, lcl, mean, updated_at)
                VALUES (%s, %s, %s, %s, %s, NOW())
                ON CONFLICT (appliance_id, metric_name) DO UPDATE SET
                    ucl = EXCLUDED.ucl,
                    lcl = EXCLUDED.lcl,
                    mean = EXCLUDED.mean,
                    updated_at = NOW()
            """, (appliance_id, metric, ucl, lcl, mean))
        cur.execute(
            "UPDATE appliances SET baseline_configured = TRUE WHERE id = %s",
            (appliance_id,),
        )
        conn.commit()
        return True, "Baseline saved successfully"
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        cur.close()
        release_conn(conn)


def insert_sensor_event(mac, event_type):
    conn = get_conn()
    if not conn:
        return None
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO sensor_events (sensor_node_mac, event_type, timestamp)
            VALUES (%s, %s, NOW())
            RETURNING id
        """, (mac, event_type))
        event_id = cur.fetchone()[0]
        conn.commit()
        return event_id
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        release_conn(conn)


def get_appliance_voltage(appliance_id):
    conn = get_conn()
    if not conn:
        return 220.0
    cur = conn.cursor()
    try:
        cur.execute("SELECT voltage FROM appliances WHERE id = %s", (appliance_id,))
        row = cur.fetchone()
        return float(row[0]) if row and row[0] is not None else 220.0
    except Exception:
        return 220.0
    finally:
        cur.close()
        release_conn(conn)


def get_user_webhook(appliance_id):
    conn = get_conn()
    if not conn:
        return None
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT u.discord_webhook_url
            FROM users u
            JOIN appliances a ON a.user_id = u.id
            WHERE a.id = %s
        """, (appliance_id,))
        row = cur.fetchone()
        return row[0] if row and row[0] else None
    except Exception as e:
        print(f"Error fetching webhook: {e}")
        return None
    finally:
        cur.close()
        release_conn(conn)


def get_user_webhook_by_user_id(user_id):
    conn = get_conn()
    if not conn:
        return None
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT discord_webhook_url FROM users WHERE id = %s", (user_id,)
        )
        row = cur.fetchone()
        return row[0] if row and row[0] else None
    except Exception as e:
        print(f"Error fetching webhook: {e}")
        return None
    finally:
        cur.close()
        release_conn(conn)


def update_user_webhook(user_id, webhook_url):
    """Save or clear the Discord webhook URL for a user."""
    conn = get_conn()
    if not conn:
        return False
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE users SET discord_webhook_url = %s WHERE id = %s",
            (webhook_url if webhook_url else None, user_id),
        )
        conn.commit()
        return cur.rowcount > 0
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        release_conn(conn)


def get_appliance_name(appliance_id):
    conn = get_conn()
    if not conn:
        return None
    cur = conn.cursor()
    try:
        cur.execute("SELECT name FROM appliances WHERE id = %s", (appliance_id,))
        row = cur.fetchone()
        return row[0] if row else None
    except Exception as e:
        print(f"Error fetching appliance name: {e}")
        return None
    finally:
        cur.close()
        release_conn(conn)


def update_node_last_seen(node_id):
    conn = get_conn()
    if not conn:
        return False
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE sensor_nodes SET last_seen = NOW() WHERE id = %s", (node_id,)
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        release_conn(conn)


def get_unpaired_nodes():
    conn = get_conn()
    if not conn:
        return []
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT id, mac_address, status, last_seen
            FROM sensor_nodes
            WHERE status = 'unpaired'
              AND last_seen >= NOW() - INTERVAL '30 seconds'
            ORDER BY id
        """)
        rows = cur.fetchall()
        return [
            {
                "id": r[0],
                "mac_address": r[1],
                "status": r[2],
                "last_seen": r[3],
                "readings": {},
            }
            for r in rows
        ]
    finally:
        cur.close()
        release_conn(conn)


def get_all_nodes_for_user(user_id):
    conn = get_conn()
    if not conn:
        return []
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT sn.id, sn.mac_address, sn.status, a.name, sn.appliance_id
            FROM sensor_nodes sn
            LEFT JOIN appliances a ON sn.appliance_id = a.id
            WHERE a.user_id = %s OR sn.status = 'unpaired'
            ORDER BY sn.id
        """, (user_id,))
        rows = cur.fetchall()
        return [
            {
                "id": r[0],
                "mac_address": r[1],
                "status": r[2],
                "appliance": r[3] or "Unpaired",
                "appliance_id": r[4],
            }
            for r in rows
        ]
    finally:
        cur.close()
        release_conn(conn)


def update_appliance_settings(appliance_id, settings):
    """Update a subset of appliance settings. settings is a dict of field->value."""
    allowed = {
        "delta_t_lcl",
        "delta_t_delay_minutes",
        "voltage",
        "atmospheric_pressure",
        "alert_enabled",
        "treturn_offset",
        "tsupply_offset",
    }
    updates = {k: v for k, v in settings.items() if k in allowed}
    if not updates:
        return False

    conn = get_conn()
    if not conn:
        return False
    cur = conn.cursor()
    try:
        set_clause = ", ".join(f"{k} = %s" for k in updates)
        values = list(updates.values()) + [appliance_id]
        cur.execute(
            f"UPDATE appliances SET {set_clause}, updated_at = NOW() WHERE id = %s",
            values,
        )
        conn.commit()
        return cur.rowcount > 0
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        release_conn(conn)


def get_recent_dryer_readings(appliance_id, limit=60):
    conn = get_conn()
    if not conn:
        return []
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT dr.time, dr.texhaust, dr.rh_exhaust, dr.pressure, dr.abs_pressure, dr.imotor
            FROM dryer_readings dr
            JOIN sensor_nodes sn ON dr.sensor_node_id = sn.id
            WHERE sn.appliance_id = %s
            ORDER BY dr.time DESC
            LIMIT %s
        """, (appliance_id, limit))
        rows = cur.fetchall()
        return [
            {
                "time": r[0],
                "texhaust": r[1],
                "rh_exhaust": r[2],
                "pressure": r[3],
                "abs_pressure": r[4],
                "imotor": r[5],
            }
            for r in reversed(rows)
        ]
    finally:
        cur.close()
        release_conn(conn)


def acknowledge_alert(alert_id):
    conn = get_conn()
    if not conn:
        return False
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE alerts SET acknowledged = TRUE WHERE id = %s", (alert_id,)
        )
        conn.commit()
        return cur.rowcount > 0
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        release_conn(conn)


def get_maintenance_logs_for_appliance(appliance_id):
    """Return maintenance sensor events for the appliance's MAC."""
    conn = get_conn()
    if not conn:
        return []
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT se.timestamp
            FROM sensor_events se
            JOIN sensor_nodes sn ON se.sensor_node_mac = sn.mac_address
            WHERE sn.appliance_id = %s AND se.event_type = 'maintenance'
            ORDER BY se.timestamp DESC
        """, (appliance_id,))
        return [{"timestamp": r[0].isoformat() if r[0] else None} for r in cur.fetchall()]
    finally:
        cur.close()
        release_conn(conn)
