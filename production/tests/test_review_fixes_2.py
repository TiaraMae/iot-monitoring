"""Regression checks for the round-2 review fixes (2026-11-14).

  PERF  Avg Delta-T 24h and /api/energy_summary are cached (same values,
        refreshed per TTL; explicit clears work).
  LOG   app.logstamp stamps each line once and never double-stamps.
  CSP   Content-Security-Policy header on pages.
  B15   One alert per bad dryer cycle: ventilation blockage suppresses the
        same-cycle incomplete-drying alert, keeping the >90 % RH critical.
  B14   Rows recorded during a calibration session are excluded from the
        temperature averages (and restart the warmup) but still count for
        current/energy; telemetry flags them while status == calibrating.

Self-contained fixture; cleans up. MQTT disabled.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("IOT_DISABLE_MQTT", "1")

import io
import json
from datetime import datetime, timedelta

import bcrypt

from app import create_app, models, config
import app.alerts as alerts
import app.analytics as analytics
import app.telemetry as telemetry
from app.logstamp import _StampedStream

TEST_EMAIL = 'test_reviewfixes2@example.com'
TEST_PASSWORD = 'Test1234'
HVAC_MAC = 'RF:00:00:00:00:21'
DRY_MAC = 'RF:00:00:00:00:22'

USER_ID = HVAC_ID = DRY_ID = HVAC_NODE = DRY_NODE = None


def sql(query, params=(), fetch=None):
    conn = models.get_conn()
    cur = conn.cursor()
    cur.execute(query, params)
    row = cur.fetchone() if fetch == 'one' else (cur.fetchall() if fetch == 'all' else None)
    conn.commit()
    cur.close()
    models.release_conn(conn)
    return row


def cleanup_fixture():
    for mac in (HVAC_MAC, DRY_MAC):
        row = sql("SELECT id FROM sensor_nodes WHERE mac_address = %s", (mac,), fetch='one')
        if row:
            sql("DELETE FROM hvac_readings WHERE sensor_node_id = %s", (row[0],))
            sql("DELETE FROM dryer_readings WHERE sensor_node_id = %s", (row[0],))
            sql("DELETE FROM sensor_nodes WHERE id = %s", (row[0],))
    row = sql("SELECT id FROM users WHERE email = %s", (TEST_EMAIL,), fetch='one')
    if row:
        for (aid,) in sql("SELECT id FROM appliances WHERE user_id = %s", (row[0],), fetch='all'):
            sql("DELETE FROM alerts WHERE appliance_id = %s", (aid,))
            sql("DELETE FROM spc_manual_baselines WHERE appliance_id = %s", (aid,))
            alerts.DRYER_CYCLE_STATS.pop(aid, None)
            for k in [k for k in alerts.FAULT_ALERT_COOLDOWN if k[0] == aid]:
                alerts.FAULT_ALERT_COOLDOWN.pop(k, None)
        sql("DELETE FROM appliances WHERE user_id = %s", (row[0],))
        sql("DELETE FROM users WHERE id = %s", (row[0],))


def setup_fixture():
    global USER_ID, HVAC_ID, DRY_ID, HVAC_NODE, DRY_NODE
    cleanup_fixture()
    h = bcrypt.hashpw(TEST_PASSWORD.encode(), bcrypt.gensalt()).decode()
    USER_ID = models.create_user(TEST_EMAIL, h, 'Review Fixes 2')
    HVAC_ID = sql("""
        INSERT INTO appliances (user_id, name, type, operational_status, cf, deductor,
                                current_sensor, calibrated_at, initial_calibrated_at)
        VALUES (%s, 'R2 AC', 'HVAC', 'normal', 11.0, 0.033, 'ZHT103C', NOW(), NOW())
        RETURNING id""", (USER_ID,), fetch='one')[0]
    DRY_ID = sql("""
        INSERT INTO appliances (user_id, name, type, operational_status, cf, deductor,
                                current_sensor, baseline_configured, alert_enabled)
        VALUES (%s, 'R2 Dryer', 'Gas Dryer', 'normal', 33.0, 0.111, 'SCT013-015', TRUE, TRUE)
        RETURNING id""", (USER_ID,), fetch='one')[0]
    HVAC_NODE = sql("INSERT INTO sensor_nodes (mac_address, status, appliance_id, last_seen) "
                    "VALUES (%s, 'paired', %s, NOW()) RETURNING id", (HVAC_MAC, HVAC_ID), fetch='one')[0]
    DRY_NODE = sql("INSERT INTO sensor_nodes (mac_address, status, appliance_id, last_seen) "
                   "VALUES (%s, 'paired', %s, NOW()) RETURNING id", (DRY_MAC, DRY_ID), fetch='one')[0]


def add_hvac(ts, tr, ts_, amps, cal=False):
    sql("INSERT INTO hvac_readings (sensor_node_id, time, treturn, tsupply, icompressor, during_calibration) "
        "VALUES (%s, %s, %s, %s, %s, %s)", (HVAC_NODE, ts, tr, ts_, amps, cal))


def run_rows(start, minutes, delta, cal=False, amps=3.0):
    """10 s-cadence running rows as dicts (for the pure functions)."""
    return [{"time": start + timedelta(seconds=10 * i), "treturn": 25.0,
             "tsupply": 25.0 - delta, "icompressor": amps, "during_calibration": cal}
            for i in range(minutes * 6)]


def main():
    app = create_app()
    failures = []

    def check(name, cond):
        print(('PASS' if cond else 'FAIL') + ': ' + name)
        if not cond:
            failures.append(name)

    setup_fixture()
    try:
        # ---------------- B14: pure functions ----------------
        t0 = datetime(2026, 11, 1, 8, 0, 0)
        # 10 min normal run (delta 10) -> 20 min calibration rows (delta 0,
        # raw) -> 10 min normal run (delta 10). If calibration rows counted,
        # the average would be pulled toward 0.
        rows = (run_rows(t0, 10, 10.0)
                + run_rows(t0 + timedelta(minutes=10), 20, 0.0, cal=True)
                + run_rows(t0 + timedelta(minutes=30), 10, 10.0))
        avg = models.compute_avg_running_delta_t(rows, 0.25, 5)
        check('B14 avg Delta-T excludes calibration rows (== 10.0)', avg == 10.0)
        unflagged = [dict(r, during_calibration=False) for r in rows]
        check('B14 sanity: same rows unflagged pull the average down',
              models.compute_avg_running_delta_t(unflagged, 0.25, 5) < 10.0)
        # Warmup restarts after calibration: the first 5 min of the post-
        # calibration run (here delta 99) must be skipped.
        rows_w = (run_rows(t0, 10, 10.0)
                  + run_rows(t0 + timedelta(minutes=10), 5, 0.0, cal=True)
                  + run_rows(t0 + timedelta(minutes=15), 5, 99.0)
                  + run_rows(t0 + timedelta(minutes=20), 10, 10.0))
        check('B14 warmup restarts after a calibration session',
              models.compute_avg_running_delta_t(rows_w, 0.25, 5) == 10.0)
        daily = models.compute_daily_running_averages(rows, 0.25, 5)
        check('B14 daily averages exclude calibration rows',
              len(daily) == 1 and round(daily[0]['avg_return'] - daily[0]['avg_supply'], 2) == 10.0)
        exp_rows = [dict(r, icompressor=(5.0 if r['during_calibration'] else 3.0)) for r in rows]
        exp = models.compute_daily_export_averages(exp_rows, 0.25, 5, ['treturn', 'tsupply'])
        check('B14 export temps exclude calibration rows',
              round(exp[0]['avg_treturn'] - exp[0]['avg_tsupply'], 2) == 10.0)
        n_cal, n_norm = 120, 120
        expected_cur = round((n_cal * 5.0 + n_norm * 3.0) / (n_cal + n_norm), 3)
        check('B14 export Avg Current still counts calibration rows',
              exp[0]['avg_current'] == expected_cur)
        tuples = [(r['time'], r['icompressor'], r['treturn'], r['tsupply'], r['during_calibration'])
                  for r in rows]
        check('B14 energy unaffected by the flag column',
              analytics._compute_energy_kwh(tuples, 220.0)
              == analytics._compute_energy_kwh([t[:4] for t in tuples], 220.0) > 0)

        # ---------------- B14: telemetry sets the flag ----------------
        sql("UPDATE appliances SET operational_status = 'calibrating' WHERE id = %s", (HVAC_ID,))
        telemetry.DEDUPE_CACHE.pop(HVAC_MAC, None)
        telemetry.handle_telemetry(HVAC_MAC, json.dumps(
            {"DS1Temp": 25.0, "DS2Temp": 20.0, "CurrentA": 3.0, "agoms": 600000}))
        sql("UPDATE appliances SET operational_status = 'normal' WHERE id = %s", (HVAC_ID,))
        telemetry.DEDUPE_CACHE.pop(HVAC_MAC, None)
        telemetry.handle_telemetry(HVAC_MAC, json.dumps(
            {"DS1Temp": 25.0, "DS2Temp": 15.0, "CurrentA": 3.0, "agoms": 0}))
        flags = sql("SELECT during_calibration FROM hvac_readings WHERE sensor_node_id = %s "
                    "ORDER BY time", (HVAC_NODE,), fetch='all')
        check('B14 telemetry flags rows while calibrating, not otherwise',
              [f[0] for f in flags] == [True, False])
        sql("DELETE FROM hvac_readings WHERE sensor_node_id = %s", (HVAC_NODE,))

        # ---------------- PERF: Avg Delta-T cache ----------------
        now = datetime.now()
        for i in range(60):  # 10 min of delta 10, all past warmup after 5 min
            add_hvac(now - timedelta(minutes=20) + timedelta(seconds=10 * i), 25.0, 15.0, 3.0)
        models.clear_avg_delta_t_cache()
        v1 = models.get_avg_delta_t_running_24h(HVAC_ID)
        for i in range(60):  # a later run with delta 2 changes the true average
            add_hvac(now - timedelta(minutes=8) + timedelta(seconds=5 * i), 25.0, 23.0, 3.0)
        v2 = models.get_avg_delta_t_running_24h(HVAC_ID)
        check('PERF avg Delta-T served from cache within TTL', v1 == 10.0 and v2 == v1)
        ts, val = models._AVG_DELTA_T_CACHE[HVAC_ID]
        models._AVG_DELTA_T_CACHE[HVAC_ID] = (ts - config.AVG_DELTA_T_CACHE_SECONDS - 1, val)
        v3 = models.get_avg_delta_t_running_24h(HVAC_ID)
        check('PERF avg Delta-T recomputed after TTL', v3 is not None and v3 != v1)

        with app.test_client() as client:
            client.post('/login', data={'email': TEST_EMAIL, 'password': TEST_PASSWORD})

            # ---------------- CSP ----------------
            for path in ('/login', '/dashboard'):
                r = client.get(path)
                csp = r.headers.get('Content-Security-Policy', '')
                check(f'CSP header on {path}',
                      "default-src 'self'" in csp and 'https://cdn.jsdelivr.net' in csp
                      and "frame-ancestors 'self'" in csp)

            # ---------------- PERF: energy summary cache ----------------
            month = now.strftime('%Y-%m')
            # The summary only counts readings after the appliance was
            # created; backdate the fixture so the test readings qualify.
            sql("UPDATE appliances SET created_at = NOW() - INTERVAL '2 hours' "
                "WHERE id = %s", (HVAC_ID,))
            analytics.clear_energy_cache()
            e1 = client.get(f'/api/energy_summary?month={month}').get_json()['total_kwh']
            for i in range(60):
                add_hvac(now - timedelta(minutes=40) + timedelta(seconds=10 * i), 25.0, 15.0, 6.0)
            e2 = client.get(f'/api/energy_summary?month={month}').get_json()['total_kwh']
            check('PERF energy summary served from cache within TTL', e2 == e1)
            analytics.clear_energy_cache()
            e3 = client.get(f'/api/energy_summary?month={month}').get_json()['total_kwh']
            check('PERF energy summary recomputed after clear', e3 > e1)
            analytics.clear_energy_cache()

        # ---------------- LOG stamps ----------------
        buf = io.StringIO()
        st = _StampedStream(buf)
        st.write("hello\nworld\n")
        st.write("[2026-11-14 10:00:00] already stamped\n")
        st.write("par")
        st.write("tial\n")
        lines = buf.getvalue().splitlines()
        check('LOG every line stamped once', len(lines) == 4
              and all(l.startswith('[20') for l in lines)
              and lines[0].endswith('] hello') and lines[1].endswith('] world'))
        check('LOG pre-stamped line left untouched',
              lines[2] == '[2026-11-14 10:00:00] already stamped')
        check('LOG partial writes stamped once',
              lines[3].count('] partial') == 1 and lines[3].count('[20') == 1)
        try:
            st.write(b"")
            bytes_rejected = False
        except TypeError:
            bytes_rejected = True
        check('LOG stream rejects bytes like a real text stream (click probe)',
              bytes_rejected)

        # ---------------- B15: one alert per bad dryer cycle ----------------
        for metric, ucl in (('rhexhaust', 40.0), ('texhaust', 50.0), ('pressure', 5.0)):
            sql("INSERT INTO spc_manual_baselines (appliance_id, metric_name, ucl) "
                "VALUES (%s, %s, %s)", (DRY_ID, metric, ucl))
        baselines = models.get_spc_baselines(DRY_ID)

        def finalize(rh, max_temp, max_p):
            sql("DELETE FROM alerts WHERE appliance_id = %s", (DRY_ID,))
            for k in [k for k in alerts.FAULT_ALERT_COOLDOWN if k[0] == DRY_ID]:
                alerts.FAULT_ALERT_COOLDOWN.pop(k, None)
            alerts.DRYER_CYCLE_STATS[DRY_ID] = {
                "in_cycle": True, "last_time": datetime.now(),
                "motor_readings": [2.0] * 6, "rh_history": [rh] * 6,
                "max_temp": max_temp, "max_gauge_pressure": max_p,
                "spike_state": "IDLE", "spike_max": 0.0, "spike_valley": 0.0,
            }
            alerts._finalize_dryer_cycle(DRY_ID, baselines, datetime.now())
            return sql("SELECT alert_type, severity FROM alerts WHERE appliance_id = %s "
                       "ORDER BY id", (DRY_ID,), fetch='all')

        got = finalize(70.0, 60.0, 1.0)
        check('B15 blockage only (warning), no duplicate incomplete-drying',
              got == [('fault_dryer_exhaust_ventilation_blockage', 'warning')])
        got = finalize(95.0, 60.0, 1.0)
        check('B15 blockage with RH > 90 % is critical (rule carried over)',
              got == [('fault_dryer_exhaust_ventilation_blockage', 'critical')])
        got = finalize(70.0, 60.0, 6.0)
        check('B15 blockage with high gauge pressure is critical',
              got == [('fault_dryer_exhaust_ventilation_blockage', 'critical')])
        got = finalize(70.0, 45.0, 1.0)
        check('B15 RH high but temp normal -> incomplete drying as before',
              got == [('fault_dryer_incomplete_drying', 'warning')])
        got = finalize(95.0, 45.0, 1.0)
        check('B15 RH > 90 % with temp normal -> incomplete drying critical as before',
              got == [('fault_dryer_incomplete_drying', 'critical')])
        got = finalize(30.0, 60.0, 1.0)
        check('B15 healthy cycle -> no alert', got == [])
    finally:
        models.clear_avg_delta_t_cache()
        analytics.clear_energy_cache()
        cleanup_fixture()

    if failures:
        print(f'\n{len(failures)} check(s) FAILED')
        sys.exit(1)
    print('\nAll round-2 review-fix checks passed.')


if __name__ == '__main__':
    main()
