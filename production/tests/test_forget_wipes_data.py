"""Verify that forgetting a device permanently wipes all of its data.

Regression test: forgetting a device previously only detached the sensor node,
so readings, sensor events (maintenance history), alerts, and SPC baselines
survived a forget + re-pair (and even leaked into another account re-pairing
the same node, since sensor_events is keyed by MAC).

Self-contained: creates its own user/appliance/node fixtures and cleans up.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Never let tests connect to the shared MQTT broker: each connection leaves a
# 1-hour persistent session behind (~60 wasted session-minutes per test run).
os.environ.setdefault("IOT_DISABLE_MQTT", "1")

import bcrypt

from app import create_app
from app import models


TEST_EMAIL = 'test_forget_wipe@example.com'
TEST_PASSWORD = 'Test1234'
TEST_MAC = 'AA:BB:CC:DD:EE:05'

USER_ID = None
APPLIANCE_ID = None


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
    password_hash = bcrypt.hashpw(TEST_PASSWORD.encode(), bcrypt.gensalt()).decode()
    USER_ID = models.create_user(TEST_EMAIL, password_hash, 'Forget Wipe Test')
    APPLIANCE_ID = sql("""
        INSERT INTO appliances
        (user_id, name, type, sub_type, is_inverter, location, brand,
         operational_status, cf, deductor, alert_enabled)
        VALUES (%s, 'Forget Wipe HVAC', 'HVAC', 'split_residential', FALSE, 'Home', 'Generic',
                'normal', 11.0, 0.033, TRUE)
        RETURNING id
    """, (USER_ID,), fetch='one')[0]
    sql("""
        INSERT INTO sensor_nodes (mac_address, status, appliance_id, last_seen)
        VALUES (%s, 'paired', %s, NOW())
    """, (TEST_MAC, APPLIANCE_ID))


def cleanup_fixture():
    # Wipe in FK-safe order regardless of what the test left behind.
    sql("DELETE FROM hvac_readings WHERE sensor_node_id IN (SELECT id FROM sensor_nodes WHERE mac_address = %s)", (TEST_MAC,))
    sql("DELETE FROM dryer_readings WHERE sensor_node_id IN (SELECT id FROM sensor_nodes WHERE mac_address = %s)", (TEST_MAC,))
    sql("DELETE FROM sensor_events WHERE sensor_node_mac = %s", (TEST_MAC,))
    sql("DELETE FROM sensor_nodes WHERE mac_address = %s", (TEST_MAC,))
    if APPLIANCE_ID:
        sql("DELETE FROM alerts WHERE appliance_id = %s", (APPLIANCE_ID,))
        sql("DELETE FROM spc_manual_baselines WHERE appliance_id = %s", (APPLIANCE_ID,))
        sql("DELETE FROM appliances WHERE id = %s", (APPLIANCE_ID,))
    if USER_ID:
        sql("DELETE FROM users WHERE id = %s", (USER_ID,))


def seed_data():
    node_id = sql("SELECT id FROM sensor_nodes WHERE mac_address = %s", (TEST_MAC,), fetch='one')[0]
    sql("INSERT INTO hvac_readings (sensor_node_id, time, treturn, tsupply, icompressor) VALUES (%s, NOW(), 26.0, 18.0, 1.2)", (node_id,))
    sql("INSERT INTO dryer_readings (sensor_node_id, time, texhaust, rh_exhaust, pressure, abs_pressure, imotor) VALUES (%s, NOW(), 55.0, 40.0, 1.0, 1013.0, 2.0)", (node_id,))
    sql("INSERT INTO sensor_events (sensor_node_mac, event_type) VALUES (%s, 'maintenance'), (%s, 'maintenance')", (TEST_MAC, TEST_MAC))
    sql("INSERT INTO alerts (appliance_id, alert_type, message, severity) VALUES (%s, 'fault_hvac_low_delta_t', 'low delta-t', 'critical')", (APPLIANCE_ID,))
    sql("INSERT INTO spc_manual_baselines (appliance_id, metric_name, lcl) VALUES (%s, 'deltat', 5.0)", (APPLIANCE_ID,))


def count(table, where, params):
    return sql(f"SELECT COUNT(*) FROM {table} WHERE {where}", params, fetch='one')[0]


def main():
    create_app()
    setup_fixture()
    failures = []

    def check(name, cond):
        print(('PASS' if cond else 'FAIL') + ': ' + name)
        if not cond:
            failures.append(name)

    try:
        seed_data()

        mac = models.unpair_node_by_appliance(APPLIANCE_ID)

        check('returns the node MAC', mac == TEST_MAC)
        check('hvac_readings wiped',
              count('hvac_readings', 'sensor_node_id IN (SELECT id FROM sensor_nodes WHERE mac_address = %s)', (TEST_MAC,)) == 0)
        check('dryer_readings wiped',
              count('dryer_readings', 'sensor_node_id IN (SELECT id FROM sensor_nodes WHERE mac_address = %s)', (TEST_MAC,)) == 0)
        check('sensor_events (maintenance history) wiped',
              count('sensor_events', 'sensor_node_mac = %s', (TEST_MAC,)) == 0)
        check('alerts wiped', count('alerts', 'appliance_id = %s', (APPLIANCE_ID,)) == 0)
        check('spc_manual_baselines wiped', count('spc_manual_baselines', 'appliance_id = %s', (APPLIANCE_ID,)) == 0)
        check('appliance row deleted', count('appliances', 'id = %s', (APPLIANCE_ID,)) == 0)
        node = sql("SELECT status, appliance_id FROM sensor_nodes WHERE mac_address = %s", (TEST_MAC,), fetch='one')
        check('node row kept as unpaired', node == ('unpaired', None))

        if failures:
            print(f'\n{len(failures)} check(s) FAILED')
            sys.exit(1)
        print('\nAll forget-wipe checks passed.')
    finally:
        cleanup_fixture()


if __name__ == '__main__':
    main()
