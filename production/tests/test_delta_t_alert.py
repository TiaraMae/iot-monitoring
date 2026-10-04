"""Verify HVAC low delta-T alert fires after configured delay.

Self-contained after the 2026-09-05 DB reset: creates its own
user/appliance/node fixture if missing and cleans it up afterwards.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Never let tests connect to the shared MQTT broker: each connection leaves a
# 1-hour persistent session behind (~60 wasted session-minutes per test run).
os.environ.setdefault("IOT_DISABLE_MQTT", "1")

from datetime import datetime, timezone, timedelta
import bcrypt

from app import create_app
from app import models, alerts

USER_ID = None
APPLIANCE_ID = None
TEST_MAC = 'AA:BB:CC:DD:EE:02'

def sql(query, params=(), fetch=None):
    conn = models.get_conn()
    cur = conn.cursor()
    cur.execute(query, params)
    row = cur.fetchone() if fetch == 'one' else (cur.fetchall() if fetch == 'all' else None)
    conn.commit()
    cur.close()
    models.release_conn(conn)
    return row

def setup_fixture():
    global USER_ID, APPLIANCE_ID
    row = models.get_user_by_email('test_dashboard@example.com')
    if row:
        USER_ID = row['id']
    else:
        password_hash = bcrypt.hashpw(b'Test1234', bcrypt.gensalt()).decode()
        USER_ID = models.create_user('test_dashboard@example.com', password_hash, 'Dashboard Test')

    APPLIANCE_ID = sql("""
        INSERT INTO appliances
        (user_id, name, type, sub_type, is_inverter, location, brand,
         operational_status, cf, deductor, alert_enabled)
        VALUES (%s, 'Delta-T Alert Test HVAC', 'HVAC', 'split_duct', FALSE, 'Home', 'Generic',
                'normal', 11.0, 0.033, TRUE)
        RETURNING id
    """, (USER_ID,), fetch='one')[0]
    sql("""
        INSERT INTO sensor_nodes (mac_address, status, appliance_id, last_seen)
        VALUES (%s, 'paired', %s, NOW())
    """, (TEST_MAC, APPLIANCE_ID))

def set_baseline_and_thresholds():
    models.update_appliance_settings(APPLIANCE_ID, {
        'delta_t_lcl': 8.0,
        'delta_t_delay_minutes': 0,
        'alert_enabled': True,
        'voltage': 220,
        'treturn_offset': 0,
        'tsupply_offset': 0,
    })
    # mark baseline configured so alert gate passes
    sql("UPDATE appliances SET baseline_configured = TRUE WHERE id = %s", (APPLIANCE_ID,))

def clear_readings_and_alerts():
    sql("DELETE FROM hvac_readings WHERE sensor_node_id IN (SELECT id FROM sensor_nodes WHERE appliance_id = %s)", (APPLIANCE_ID,))
    sql("DELETE FROM alerts WHERE appliance_id = %s", (APPLIANCE_ID,))

def cleanup_fixture():
    clear_readings_and_alerts()
    sql("DELETE FROM spc_manual_baselines WHERE appliance_id = %s", (APPLIANCE_ID,))
    sql("DELETE FROM sensor_nodes WHERE appliance_id = %s", (APPLIANCE_ID,))
    sql("DELETE FROM appliances WHERE id = %s", (APPLIANCE_ID,))
    sql("DELETE FROM users WHERE id = %s", (USER_ID,))

def insert_running_reading(t_return, t_supply, current, mins_ago=0):
    node = models.get_node_by_appliance(APPLIANCE_ID)
    ts = datetime.now(timezone.utc) - timedelta(minutes=mins_ago)
    sql("""
        INSERT INTO hvac_readings (sensor_node_id, time, treturn, tsupply, icompressor)
        VALUES (%s, %s, %s, %s, %s)
    """, (node['id'], ts, t_return, t_supply, current))
    return ts

def count_alerts():
    row = sql("SELECT COUNT(*) FROM alerts WHERE appliance_id = %s AND alert_type = 'fault_hvac_low_delta_t'", (APPLIANCE_ID,), fetch='one')
    return row[0]

def main():
    app = create_app()
    with app.app_context():
        setup_fixture()
        failures = []

        def check(name, cond):
            print(('PASS' if cond else 'FAIL') + ': ' + name)
            if not cond:
                failures.append(name)

        try:
            set_baseline_and_thresholds()
            clear_readings_and_alerts()
            alerts.DELTA_T_TRACKER.pop(APPLIANCE_ID, None)

            # Insert 5 running readings over the last minute with delta-T below LCL
            for i in range(5, 0, -1):
                insert_running_reading(26.0, 20.0, 2.5, mins_ago=i*0.2)

            # Process all readings in chronological order so the tracker sees the elapsed time.
            all_readings = models.get_latest_hvac_readings(APPLIANCE_ID, limit=20)
            all_readings.sort(key=lambda r: r['time'])
            for r in all_readings:
                reading_data = {
                    'current': r['icompressor'],
                    'delta_t': abs((r['treturn'] or 0) - (r['tsupply'] or 0)),
                    '_actual_time': r['time'],
                }
                alerts.check_hvac_delta_t_alert(APPLIANCE_ID, reading_data)

            n = count_alerts()
            check('delta-T alert fires (first run)', n >= 1)

            # 2. One alert per compressor run: continuing low readings in the
            #    SAME run must not re-alert; after > 30 s idle a new run may.
            alerts.DELTA_T_TRACKER.pop(APPLIANCE_ID, None)
            clear_readings_and_alerts()
            now = datetime.now(timezone.utc)
            run_readings = [
                {'current': 2.5, 'delta_t': 2.0, '_actual_time': now + timedelta(seconds=i * 10)}
                for i in range(5)
            ]
            for rd in run_readings:
                alerts.check_hvac_delta_t_alert(APPLIANCE_ID, rd)
            n_same_run = count_alerts()
            check('same run: exactly one alert (no re-fire)', n_same_run == 1)

            new_cycle = [
                {'current': 0.1, 'delta_t': 2.0, '_actual_time': now + timedelta(seconds=60)},
                {'current': 0.1, 'delta_t': 2.0, '_actual_time': now + timedelta(seconds=100)},  # 40 s idle > 30 s -> reset
                {'current': 2.5, 'delta_t': 2.0, '_actual_time': now + timedelta(seconds=110)},
                {'current': 2.5, 'delta_t': 2.0, '_actual_time': now + timedelta(seconds=120)},
            ]
            for rd in new_cycle:
                alerts.check_hvac_delta_t_alert(APPLIANCE_ID, rd)
            n_new_cycle = count_alerts()
            check('new compressor run alerts again (no cooldown)', n_new_cycle == 2)

            # 3. Delay is measured from the NEW run start, never instantly:
            #    after idle > 30 s, silence until the delay elapses again.
            #    (Timestamps use the real 10 s telemetry cadence: wider steps
            #    would trip the >30 s run-gap close between a run's own
            #    readings.)
            models.update_appliance_settings(APPLIANCE_ID, {
                'delta_t_lcl': 8.0, 'delta_t_delay_minutes': 1, 'alert_enabled': True,
            })
            alerts.DELTA_T_TRACKER.pop(APPLIANCE_ID, None)
            clear_readings_and_alerts()
            t0 = datetime.now(timezone.utc)
            run1 = [
                {'current': 2.5, 'delta_t': 2.0, '_actual_time': t0 + timedelta(seconds=10 * i)}
                for i in range(7)
            ]
            for rd in run1:
                alerts.check_hvac_delta_t_alert(APPLIANCE_ID, rd)
            check('run1: alert after 1-min continuous low', count_alerts() == 1)

            idle = [
                {'current': 0.1, 'delta_t': 2.0, '_actual_time': t0 + timedelta(seconds=70 + 10 * i)}
                for i in range(7)
            ]
            for rd in idle:
                alerts.check_hvac_delta_t_alert(APPLIANCE_ID, rd)
            run2 = [
                {'current': 2.5, 'delta_t': 2.0, '_actual_time': t0 + timedelta(seconds=140 + 10 * i)}
                for i in range(6)
            ]
            for rd in run2:
                alerts.check_hvac_delta_t_alert(APPLIANCE_ID, rd)
            check('run2: no instant alert before the delay elapses', count_alerts() == 1)
            alerts.check_hvac_delta_t_alert(APPLIANCE_ID, {
                'current': 2.5, 'delta_t': 2.0, '_actual_time': t0 + timedelta(seconds=200)})  # elapsed 60
            check('run2: alert once the delay elapses', count_alerts() == 2)

            # 4. A stale backlogged reading (old _actual_time, e.g. an offline-
            #    buffer flush) never drives the FSM.
            alerts.check_hvac_delta_t_alert(APPLIANCE_ID, {
                'current': 2.5, 'delta_t': 2.0, '_actual_time': t0 + timedelta(seconds=30)})  # older than last_ts
            check('stale backlogged reading ignored', count_alerts() == 2)
            alerts.check_hvac_delta_t_alert(APPLIANCE_ID, {
                'current': 2.5, 'delta_t': 2.0, '_actual_time': t0 + timedelta(seconds=210)})
            check('live reading after a stale one processed normally', count_alerts() == 2)

            # 5. Healthy bounce mid-run does NOT re-arm a second alert.
            alerts.DELTA_T_TRACKER.pop(APPLIANCE_ID, None)
            clear_readings_and_alerts()
            t1 = datetime.now(timezone.utc)
            models.update_appliance_settings(APPLIANCE_ID, {
                'delta_t_lcl': 8.0, 'delta_t_delay_minutes': 0, 'alert_enabled': True,
            })
            bounce_seq = [
                {'current': 2.5, 'delta_t': 2.0, '_actual_time': t1 + timedelta(seconds=10 * i)}
                for i in range(2)
            ]  # delay 0 -> fires on the 2nd reading
            bounce_seq += [
                {'current': 2.5, 'delta_t': 12.0, '_actual_time': t1 + timedelta(seconds=20)},  # healthy: latch held
                {'current': 2.5, 'delta_t': 2.0, '_actual_time': t1 + timedelta(seconds=30)},
                {'current': 2.5, 'delta_t': 2.0, '_actual_time': t1 + timedelta(seconds=40)},
            ]
            for rd in bounce_seq:
                alerts.check_hvac_delta_t_alert(APPLIANCE_ID, rd)
            check('same-run refault after healthy bounce: still one alert', count_alerts() == 1)

            # 6. A dip below 0.25 A shorter than 30 s does not reset the clock.
            alerts.DELTA_T_TRACKER.pop(APPLIANCE_ID, None)
            clear_readings_and_alerts()
            t2 = datetime.now(timezone.utc)
            models.update_appliance_settings(APPLIANCE_ID, {
                'delta_t_lcl': 8.0, 'delta_t_delay_minutes': 2, 'alert_enabled': True,
            })
            dip_seq = [
                {'current': 2.5, 'delta_t': 2.0, '_actual_time': t2},
                {'current': 0.1, 'delta_t': 2.0, '_actual_time': t2 + timedelta(seconds=10)},  # dip starts
                {'current': 0.1, 'delta_t': 2.0, '_actual_time': t2 + timedelta(seconds=20)},  # dip 10 s < 30 s
                {'current': 2.5, 'delta_t': 2.0, '_actual_time': t2 + timedelta(seconds=30)},  # dip ends
            ]
            dip_seq += [
                {'current': 2.5, 'delta_t': 2.0, '_actual_time': t2 + timedelta(seconds=40 + 10 * i)}
                for i in range(10)
            ]
            for rd in dip_seq:
                alerts.check_hvac_delta_t_alert(APPLIANCE_ID, rd)
            check('short dip (<30 s) does not reset the clock', count_alerts() == 1)

            # 7. Disconnect with buffered idle: NO idle readings delivered
            #    live; the run must still close via the sample-time gap.
            alerts.DELTA_T_TRACKER.pop(APPLIANCE_ID, None)
            clear_readings_and_alerts()
            t3 = datetime.now(timezone.utc)
            models.update_appliance_settings(APPLIANCE_ID, {
                'delta_t_lcl': 8.0, 'delta_t_delay_minutes': 1, 'alert_enabled': True,
            })
            disc_seq = [
                {'current': 2.5, 'delta_t': 2.0, '_actual_time': t3 + timedelta(seconds=10 * i)}
                for i in range(7)
            ]  # fire at +60 s
            disc_seq += [
                {'current': 2.5, 'delta_t': 2.0, '_actual_time': t3 + timedelta(seconds=240)},  # 180 s silence -> gap close, run start
            ]
            disc_seq += [
                {'current': 2.5, 'delta_t': 2.0, '_actual_time': t3 + timedelta(seconds=250 + 10 * i)}
                for i in range(5)
            ]
            for rd in disc_seq:
                alerts.check_hvac_delta_t_alert(APPLIANCE_ID, rd)
            check('disconnect: run closes on sample gap, no instant re-alert', count_alerts() == 1)
            alerts.check_hvac_delta_t_alert(APPLIANCE_ID, {
                'current': 2.5, 'delta_t': 2.0, '_actual_time': t3 + timedelta(seconds=300)})  # elapsed 60
            check('disconnect: new run alerts after the delay', count_alerts() == 2)

            # 8. The exact observed failure shape: one live idle reading at
            #    disconnect, silence, live resume, then stale backfilled idle.
            alerts.DELTA_T_TRACKER.pop(APPLIANCE_ID, None)
            clear_readings_and_alerts()
            t4 = datetime.now(timezone.utc)
            live_seq = [
                {'current': 2.5, 'delta_t': 2.0, '_actual_time': t4 + timedelta(seconds=10 * i)}
                for i in range(7)
            ]  # fire at +60 s
            live_seq += [
                {'current': 0.0, 'delta_t': 2.0, '_actual_time': t4 + timedelta(seconds=70)},   # last live reading (idle)
                {'current': 2.5, 'delta_t': 2.0, '_actual_time': t4 + timedelta(seconds=240)},  # resume after 170 s silence
            ]
            live_seq += [
                {'current': 2.5, 'delta_t': 2.0, '_actual_time': t4 + timedelta(seconds=250 + 10 * i)}
                for i in range(5)
            ]
            for rd in live_seq:
                alerts.check_hvac_delta_t_alert(APPLIANCE_ID, rd)
            for back in range(75, 235, 10):  # stale backfilled idle readings
                alerts.check_hvac_delta_t_alert(APPLIANCE_ID, {
                    'current': 0.0, 'delta_t': 2.0, '_actual_time': t4 + timedelta(seconds=back)})
            check('disconnect+backfill: no alert before the delay', count_alerts() == 1)
            alerts.check_hvac_delta_t_alert(APPLIANCE_ID, {
                'current': 2.5, 'delta_t': 2.0, '_actual_time': t4 + timedelta(seconds=300)})
            check('disconnect+backfill: alert fires on schedule', count_alerts() == 2)

            # Calibration guard: raw (zeroed-offset) readings during a calibration
            # session must NOT create alerts; the same input under 'normal' must.
            # (The engine needs two low readings to fire: the first starts the
            # timer, the second trips it.)
            from app import telemetry
            set_baseline_and_thresholds()
            clear_readings_and_alerts()
            alerts.DELTA_T_TRACKER.pop(APPLIANCE_ID, None)
            sql("UPDATE appliances SET operational_status = 'calibrating' WHERE id = %s", (APPLIANCE_ID,))
            for _ in range(2):
                telemetry.handle_telemetry(TEST_MAC, '{"DS1Temp":22.0,"DS2Temp":20.0,"CurrentA":2.5}')
                telemetry.DEDUPE_CACHE.clear()
            n_cal = count_alerts()
            telemetry.DEDUPE_CACHE.clear()
            sql("UPDATE appliances SET operational_status = 'normal' WHERE id = %s", (APPLIANCE_ID,))
            for _ in range(2):
                telemetry.handle_telemetry(TEST_MAC, '{"DS1Temp":22.0,"DS2Temp":20.0,"CurrentA":2.5}')
                telemetry.DEDUPE_CACHE.clear()
            n_norm = count_alerts()
            check('alerts suppressed while calibrating', n_cal == 0 and n_norm >= 1)

            if failures:
                print(f'\n{len(failures)} check(s) FAILED')
                sys.exit(1)
            print('\nAll delta-T alert checks passed.')
        finally:
            cleanup_fixture()

if __name__ == '__main__':
    main()
