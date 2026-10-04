"""Verify dryer cycle rehydration after a backend restart (GAP-5).

Self-contained like its sibling script-tests: creates its own
user/appliance/node fixture, runs two scenarios, cleans up afterwards.
  A) A cycle that FINISHED while the backend was "down" (last reading older
     than the 120 s boundary) must be finalized by rehydration and its
     end-of-cycle faults evaluated (incomplete drying here).
  B) A cycle still RUNNING (recent readings) must be rebuilt in memory
     (in_cycle) without being finalized.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Never let tests connect to the shared MQTT broker: each connection leaves a
# 1-hour persistent session behind (~60 wasted session-minutes per test run).
os.environ.setdefault("IOT_DISABLE_MQTT", "1")

from datetime import datetime, timedelta
import bcrypt

from app import create_app
from app import models, alerts

USER_ID = None
APPLIANCE_ID = None
NODE_ID = None
TEST_MAC = 'AA:BB:CC:DD:EE:09'


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
    global USER_ID, APPLIANCE_ID, NODE_ID
    row = models.get_user_by_email('test_rehydrate@example.com')
    if row:
        USER_ID = row['id']
    else:
        password_hash = bcrypt.hashpw(b'Test1234', bcrypt.gensalt()).decode()
        USER_ID = models.create_user('test_rehydrate@example.com', password_hash, 'Rehydrate Test')

    APPLIANCE_ID = sql("""
        INSERT INTO appliances
        (user_id, name, type, sub_type, is_inverter, location, brand,
         operational_status, cf, deductor, alert_enabled, baseline_configured)
        VALUES (%s, 'Rehydrate Test Dryer', 'Gas Dryer', NULL, FALSE, 'Home', 'Generic',
                'normal', 33.0, 0.111, TRUE, TRUE)
        RETURNING id
    """, (USER_ID,), fetch='one')[0]
    NODE_ID = sql("""
        INSERT INTO sensor_nodes (mac_address, status, appliance_id, last_seen)
        VALUES (%s, 'paired', %s, NOW()) RETURNING id
    """, (TEST_MAC, APPLIANCE_ID), fetch='one')[0]

    # SPC baselines: RH UCL 40 (end-of-cycle RH above this = incomplete drying)
    for metric, ucl, lcl, mean in (("rhexhaust", 40.0, None, 45.0),
                                   ("current", 5.0, 1.0, 2.0),
                                   ("texhaust", 80.0, None, 60.0)):
        sql("""
            INSERT INTO spc_manual_baselines (appliance_id, metric_name, ucl, lcl, mean, updated_at)
            VALUES (%s, %s, %s, %s, %s, NOW())
        """, (APPLIANCE_ID, metric, ucl, lcl, mean))


def insert_reading(ts, imotor, rh):
    sql("""
        INSERT INTO dryer_readings (sensor_node_id, time, texhaust, rh_exhaust, pressure, abs_pressure, imotor)
        VALUES (%s, %s, 60.0, %s, 1.0, 1013.0, %s)
    """, (NODE_ID, ts, rh, imotor))


def insert_cycle(end_offset_s, count=20, rh_early=45.0, rh_late=70.0):
    base = datetime.now() - timedelta(seconds=end_offset_s + 10 * (count - 1))
    for i in range(count):
        rh = rh_late if i >= count - 6 else rh_early
        insert_reading(base + timedelta(seconds=10 * i), 2.0, rh)


def clear_state():
    sql("DELETE FROM dryer_readings WHERE sensor_node_id = %s", (NODE_ID,))
    sql("DELETE FROM alerts WHERE appliance_id = %s", (APPLIANCE_ID,))
    alerts.DRYER_CYCLE_STATS.pop(APPLIANCE_ID, None)
    alerts.FAULT_ALERT_TRACKER.pop(APPLIANCE_ID, None)
    keys = [k for k in alerts.FAULT_ALERT_COOLDOWN if k[0] == APPLIANCE_ID]
    for k in keys:
        alerts.FAULT_ALERT_COOLDOWN.pop(k, None)


def count_alerts(alert_type):
    return sql("SELECT COUNT(*) FROM alerts WHERE appliance_id = %s AND alert_type = %s",
               (APPLIANCE_ID, alert_type), fetch='one')[0]


def cleanup_fixture():
    clear_state()
    sql("DELETE FROM spc_manual_baselines WHERE appliance_id = %s", (APPLIANCE_ID,))
    sql("DELETE FROM sensor_nodes WHERE appliance_id = %s", (APPLIANCE_ID,))
    sql("DELETE FROM appliances WHERE id = %s", (APPLIANCE_ID,))
    sql("DELETE FROM users WHERE id = %s", (USER_ID,))


def main():
    app = create_app()
    failures = []

    def check(name, cond):
        print(('PASS' if cond else 'FAIL') + ': ' + name)
        if not cond:
            failures.append(name)

    with app.app_context():
        setup_fixture()
        try:
            # --- Scenario A: orphaned FINISHED cycle (backend was down) ---
            clear_state()
            insert_cycle(end_offset_s=600)  # last reading 10 min ago -> gap > 120 s
            alerts.rehydrate_dryer_cycles()
            n = count_alerts('fault_dryer_incomplete_drying')
            check('A) orphaned cycle finalized -> incomplete-drying alert inserted', n >= 1)
            check('A) cycle stats closed after finalize',
                  not alerts.DRYER_CYCLE_STATS.get(APPLIANCE_ID, {}).get('in_cycle', False))

            # --- Scenario A2: cooldown seeding prevents re-notify ---
            # The A alert now exists in the DB; replaying the SAME orphaned
            # cycle again must not insert a duplicate (cooldown re-armed from DB).
            alerts.rehydrate_dryer_cycles()
            check('A2) cooldown seeded from DB -> no duplicate alert',
                  count_alerts('fault_dryer_incomplete_drying') == n)

            # --- Scenario B: cycle still RUNNING (recent readings) ---
            clear_state()
            insert_cycle(end_offset_s=5)  # last reading 5 s ago -> still live
            alerts.rehydrate_dryer_cycles()
            stats = alerts.DRYER_CYCLE_STATS.get(APPLIANCE_ID, {})
            check('B) running cycle rebuilt in memory (in_cycle)', stats.get('in_cycle') is True)
            check('B) running cycle NOT finalized (no end-of-cycle alert)',
                  count_alerts('fault_dryer_incomplete_drying') == 0)
            check('B) motor readings rebuilt', len(stats.get('motor_readings', [])) >= 20)
        finally:
            cleanup_fixture()

    if failures:
        print('FAILED:', failures)
        sys.exit(1)
    print('All dryer-rehydration checks passed.')


if __name__ == '__main__':
    main()
