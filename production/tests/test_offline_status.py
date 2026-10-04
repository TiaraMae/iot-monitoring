"""Verify /api/device/<id>/latest offline semantics.

A paired node streaming telemetry every 10 s must flip to is_offline once
silent for longer than config.OFFLINE_TIMEOUT_SECONDS.

Self-contained: creates its own user/appliance/node fixture and cleans up.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Never let tests connect to the shared MQTT broker: each connection leaves a
# 1-hour persistent session behind (~60 wasted session-minutes per test run).
os.environ.setdefault("IOT_DISABLE_MQTT", "1")

from datetime import datetime, timedelta
import bcrypt

from app import create_app, config
from app import models

TEST_EMAIL = 'test_offline_status@example.com'
TEST_PASSWORD = 'Test1234'
TEST_MAC = 'AA:BB:CC:DD:EE:04'

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
    USER_ID = models.create_user(TEST_EMAIL, password_hash, 'Offline Test')
    APPLIANCE_ID = sql("""
        INSERT INTO appliances
        (user_id, name, type, sub_type, is_inverter, location, brand,
         operational_status, cf, deductor, alert_enabled)
        VALUES (%s, 'Offline Test AC', 'HVAC', 'split_residential', FALSE, 'Home', 'Generic',
                'normal', 11.0, 0.033, TRUE)
        RETURNING id
    """, (USER_ID,), fetch='one')[0]
    sql("""
        INSERT INTO sensor_nodes (mac_address, status, appliance_id, last_seen)
        VALUES (%s, 'paired', %s, NOW())
    """, (TEST_MAC, APPLIANCE_ID))


def cleanup_fixture():
    sql("DELETE FROM sensor_nodes WHERE appliance_id = %s", (APPLIANCE_ID,))
    sql("DELETE FROM appliances WHERE id = %s", (APPLIANCE_ID,))
    sql("DELETE FROM users WHERE id = %s", (USER_ID,))


def set_last_seen(when):
    sql("UPDATE sensor_nodes SET last_seen = %s WHERE appliance_id = %s", (when, APPLIANCE_ID))


def main():
    app = create_app()
    with app.app_context():
        import app as app_pkg
        app_pkg.mqtt.send_node_command = lambda *a, **k: None
        setup_fixture()
        failures = []

        def check(name, cond):
            print(('PASS' if cond else 'FAIL') + ': ' + name)
            if not cond:
                failures.append(name)

        try:
            client = app.test_client()
            client.post('/login', data={'email': TEST_EMAIL, 'password': TEST_PASSWORD})
            url = f'/api/device/{APPLIANCE_ID}/latest'

            # The column is TIMESTAMP WITHOUT TIME ZONE and production writes it
            # via NOW() (naive LOCAL wall time), so fixtures must use the same clock.
            now_local_naive = datetime.now()

            # Fresh last_seen -> online.
            set_last_seen(now_local_naive)
            check('fresh last_seen -> is_offline false',
                  client.get(url).get_json().get('is_offline') is False)

            # last_seen older than the configured threshold -> offline.
            stale = now_local_naive - timedelta(seconds=config.OFFLINE_TIMEOUT_SECONDS + 30)
            set_last_seen(stale)
            check('stale last_seen -> is_offline true',
                  client.get(url).get_json().get('is_offline') is True)

            # last_seen just inside the threshold -> still online.
            recent = now_local_naive - timedelta(seconds=config.OFFLINE_TIMEOUT_SECONDS - 30)
            set_last_seen(recent)
            check('last_seen inside threshold -> is_offline false',
                  client.get(url).get_json().get('is_offline') is False)

            # Node removed entirely -> offline.
            sql("DELETE FROM sensor_nodes WHERE appliance_id = %s", (APPLIANCE_ID,))
            check('no node -> is_offline true',
                  client.get(url).get_json().get('is_offline') is True)

            # Need-calibration state: the node is silent by design and only
            # checks in every 10 minutes, so the offline window must match that
            # heartbeat instead of the 120 s streaming timeout.
            sql("""
                INSERT INTO sensor_nodes (mac_address, status, appliance_id, last_seen)
                VALUES (%s, 'paired', %s, NOW())
            """, (TEST_MAC, APPLIANCE_ID))
            sql("UPDATE appliances SET operational_status = 'offset_calibration_needed' WHERE id = %s", (APPLIANCE_ID,))

            quiet_but_checking_in = now_local_naive - timedelta(seconds=config.OFFLINE_TIMEOUT_SECONDS + 60)
            set_last_seen(quiet_but_checking_in)
            check('need-calibration, 180 s silence -> still online',
                  client.get(url).get_json().get('is_offline') is False)

            long_silence = now_local_naive - timedelta(seconds=config.NEED_CALIBRATION_OFFLINE_TIMEOUT_SECONDS + 60)
            set_last_seen(long_silence)
            check('need-calibration, > 11 min silence -> offline',
                  client.get(url).get_json().get('is_offline') is True)

            # Back to normal: the strict 120 s streaming timeout applies again.
            sql("UPDATE appliances SET operational_status = 'normal' WHERE id = %s", (APPLIANCE_ID,))
            set_last_seen(quiet_but_checking_in)
            check('normal status, 180 s silence -> offline (strict timeout)',
                  client.get(url).get_json().get('is_offline') is True)

            if failures:
                print(f'\n{len(failures)} check(s) FAILED')
                sys.exit(1)
            print('\nAll offline-status checks passed.')
        finally:
            cleanup_fixture()


if __name__ == '__main__':
    main()
