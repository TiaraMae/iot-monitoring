"""Verify the baseline editor keeps the delta-T alert thresholds in sync.

The alert engine (alerts.check_hvac_delta_t_alert) reads
appliances.delta_t_lcl / appliances.delta_t_delay_minutes, while the dashboard
baseline editor writes spc_manual_baselines. This test verifies that saving a
delta-T LCL via POST /api/device/<id>/baseline_config also updates the
appliances columns, that the alert delay defaults to
config.DEFAULT_DELTA_T_DELAY_MINUTES, that invalid delays are rejected, and
that DELETE clears the alert thresholds again.

Self-contained: creates its own user/appliance/node on the (freshly reset) DB
and cleans up afterwards.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Never let tests connect to the shared MQTT broker: each connection leaves a
# 1-hour persistent session behind (~60 wasted session-minutes per test run).
os.environ.setdefault("IOT_DISABLE_MQTT", "1")

import bcrypt

from app import create_app, config
from app import models

TEST_EMAIL = 'test_baseline_sync@example.com'
TEST_PASSWORD = 'Test1234'
TEST_MAC = 'AA:BB:CC:DD:EE:FF'

USER_ID = None
APPLIANCE_ID = None


def sql(query, params=(), fetch=None):
    conn = models.get_conn()
    cur = conn.cursor()
    cur.execute(query, params)
    if fetch == 'one':
        row = cur.fetchone()
    elif fetch == 'all':
        row = cur.fetchall()
    else:
        row = None
    conn.commit()
    cur.close()
    models.release_conn(conn)
    return row


def setup_fixture():
    global USER_ID, APPLIANCE_ID
    row = models.get_user_by_email(TEST_EMAIL)
    if row:
        USER_ID = row['id']
    else:
        password_hash = bcrypt.hashpw(TEST_PASSWORD.encode(), bcrypt.gensalt()).decode()
        USER_ID = models.create_user(TEST_EMAIL, password_hash, 'Baseline Sync Test')

    node = sql("SELECT id FROM sensor_nodes WHERE mac_address = %s", (TEST_MAC,), fetch='one')
    if node:
        sql("DELETE FROM sensor_nodes WHERE id = %s", (node[0],))

    APPLIANCE_ID = sql("""
        INSERT INTO appliances
        (user_id, name, type, sub_type, is_inverter, location, brand,
         operational_status, cf, deductor, alert_enabled)
        VALUES (%s, 'Sync Test HVAC', 'HVAC', 'split_duct', FALSE, 'Home', 'Generic',
                'normal', 11.0, 0.033, TRUE)
        RETURNING id
    """, (USER_ID,), fetch='one')[0]

    sql("""
        INSERT INTO sensor_nodes (mac_address, status, appliance_id, last_seen)
        VALUES (%s, 'paired', %s, NOW())
    """, (TEST_MAC, APPLIANCE_ID))


def get_alert_thresholds():
    row = sql(
        "SELECT delta_t_lcl, delta_t_delay_minutes FROM appliances WHERE id = %s",
        (APPLIANCE_ID,), fetch='one')
    return row[0], row[1]


def cleanup():
    sql("DELETE FROM hvac_readings WHERE sensor_node_id IN (SELECT id FROM sensor_nodes WHERE appliance_id = %s)", (APPLIANCE_ID,))
    sql("DELETE FROM alerts WHERE appliance_id = %s", (APPLIANCE_ID,))
    sql("DELETE FROM spc_manual_baselines WHERE appliance_id = %s", (APPLIANCE_ID,))
    sql("DELETE FROM sensor_nodes WHERE appliance_id = %s", (APPLIANCE_ID,))
    sql("DELETE FROM appliances WHERE id = %s", (APPLIANCE_ID,))
    sql("DELETE FROM users WHERE id = %s", (USER_ID,))


def main():
    app = create_app()
    with app.app_context():
        setup_fixture()
        # The endpoint sends a baseline:set command to the (nonexistent) node;
        # stub the MQTT publish so the test never touches the broker.
        import app as app_pkg
        app_pkg.mqtt.send_node_command = lambda *a, **k: None

        try:
            client = app.test_client()
            client.post('/login', data={'email': TEST_EMAIL, 'password': TEST_PASSWORD})
            url = f'/api/device/{APPLIANCE_ID}/baseline_config'
            failures = []

            def check(name, cond):
                print(('PASS' if cond else 'FAIL') + ': ' + name)
                if not cond:
                    failures.append(name)

            # 1. Save delta-T LCL + explicit delay -> both columns synced.
            #    The compressor-current UCL/LCL was removed for HVAC (delta-T is
            #    the only HVAC trigger); posting one must be ignored.
            r = client.post(url, json={
                'metrics': {'deltat': {'lcl': 8.0}, 'current': {'ucl': 10.0, 'lcl': 0.5}},
                'alert_delay_minutes': 7,
            })
            lcl, delay = get_alert_thresholds()
            check('POST with delay 200', r.status_code == 200)
            check('delta_t_lcl synced (8.0)', lcl == 8.0)
            check('delta_t_delay_minutes synced (7)', delay == 7)
            cur_row = sql("SELECT COUNT(*) FROM spc_manual_baselines WHERE appliance_id = %s AND metric_name = 'current'", (APPLIANCE_ID,), fetch='one')
            check('HVAC current baseline ignored (no row saved)', cur_row[0] == 0)

            # GET no longer returns a current metric for HVAC.
            r = client.get(url)
            check('GET omits current metric for HVAC', 'current' not in r.get_json().get('metrics', {}))

            # GET returns the alert fields for the UI to prefill.
            r = client.get(url)
            data = r.get_json()
            check('GET returns delta_t_lcl', data.get('delta_t_lcl') == 8.0)
            check('GET returns delta_t_delay_minutes', data.get('delta_t_delay_minutes') == 7)

            # 2. POST without delay keeps the existing delay.
            r = client.post(url, json={'metrics': {'deltat': {'lcl': 9.0}}})
            lcl, delay = get_alert_thresholds()
            check('POST without delay keeps existing (7)', r.status_code == 200 and delay == 7)
            check('delta_t_lcl updated (9.0)', lcl == 9.0)

            # 3. Default delay applied when column is NULL.
            models.update_appliance_settings(APPLIANCE_ID, {'delta_t_delay_minutes': None})
            r = client.post(url, json={'metrics': {'deltat': {'lcl': 9.0}}})
            lcl, delay = get_alert_thresholds()
            check('default delay applied (5)', r.status_code == 200 and delay == config.DEFAULT_DELTA_T_DELAY_MINUTES)

            # 4. Invalid delays rejected, columns untouched.
            before = get_alert_thresholds()
            for bad in (0, -3, 'abc'):
                r = client.post(url, json={
                    'metrics': {'deltat': {'lcl': 9.0}}, 'alert_delay_minutes': bad})
                check(f'delay {bad!r} rejected with 400', r.status_code == 400)
            check('columns untouched after invalid delay', get_alert_thresholds() == before)

            # 5. DELETE clears both alert columns.
            r = client.delete(url)
            lcl, delay = get_alert_thresholds()
            check('DELETE clears delta_t_lcl', r.status_code == 200 and lcl is None)
            check('DELETE clears delta_t_delay_minutes', delay is None)

            if failures:
                print(f'\n{len(failures)} check(s) FAILED')
                sys.exit(1)
            print('\nAll baseline/alert sync checks passed.')
        finally:
            cleanup()


if __name__ == '__main__':
    main()
