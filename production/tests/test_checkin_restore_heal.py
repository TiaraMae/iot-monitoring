"""Verify the checkin-time restore:normal self-heal.

Incident 2026-10-14..15: a paired node rebooted while the backend was
unreachable. Its boot-time config request vanished, the firmware only
re-requests config while UNPAIRED, and its runtime calibration flag
(calibrationAcked, not persisted for HVAC) stayed false — so it sat
"Connected" in the broker while discarding every sample, and the dashboard
showed it offline.

Fix: on every checkin from a paired node whose appliance is operational_status
"normal" in the DB, the backend re-pushes restore:normal (idempotent no-op for
an already-healthy node). Nodes that genuinely need calibration must NOT
receive it.

Self-contained: creates its own user/appliance/node fixture and cleans up.
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("IOT_DISABLE_MQTT", "1")

from datetime import datetime
import bcrypt

from app import create_app, config
from app import models

TEST_EMAIL = 'test_checkin_heal@example.com'
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
    USER_ID = models.create_user(TEST_EMAIL, password_hash, 'Checkin Heal Test')
    APPLIANCE_ID = sql("""
        INSERT INTO appliances
        (user_id, name, type, sub_type, is_inverter, location, brand,
         operational_status, cf, deductor, alert_enabled)
        VALUES (%s, 'Checkin Heal AC', 'HVAC', 'split_residential', FALSE, 'Home', 'Generic',
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


def main():
    app = create_app()
    with app.app_context():
        import app as app_pkg
        sent = []
        app_pkg.mqtt.send_node_command = lambda mac, cmd: sent.append((mac, cmd))
        setup_fixture()
        failures = []

        def check(name, cond):
            print(('PASS' if cond else 'FAIL') + ': ' + name)
            if not cond:
                failures.append(name)

        def fresh_checkin():
            # Bypass the 5 s same-event dedupe window between consecutive cases.
            app_pkg.devices.EVENT_DEDUPE_CACHE.clear()
            sent.clear()
            app_pkg.devices.handle_node_event(TEST_MAC, '{"event":"checkin"}')

        try:
            # Paired + calibrated (normal) -> restore:normal re-pushed.
            fresh_checkin()
            check('checkin from paired+normal node -> restore:normal sent',
                  (TEST_MAC, 'restore:normal') in sent)

            # last_seen must be refreshed by the checkin.
            row = sql("SELECT last_seen FROM sensor_nodes WHERE mac_address = %s",
                      (TEST_MAC,), fetch='one')
            age = (datetime.now() - row[0]).total_seconds()
            check('checkin updates last_seen', age < 60)

            # Genuinely needs calibration -> NO restore:normal.
            sql("UPDATE appliances SET operational_status = 'offset_calibration_needed' WHERE id = %s",
                (APPLIANCE_ID,))
            fresh_checkin()
            check('checkin from need-calibration node -> no restore:normal',
                  (TEST_MAC, 'restore:normal') not in sent)

            # Unpaired node -> NO restore:normal.
            sql("UPDATE appliances SET operational_status = 'normal' WHERE id = %s", (APPLIANCE_ID,))
            sql("UPDATE sensor_nodes SET status = 'unpaired' WHERE mac_address = %s", (TEST_MAC,))
            fresh_checkin()
            check('checkin from unpaired node -> no restore:normal',
                  (TEST_MAC, 'restore:normal') not in sent)

            # Unknown MAC -> handled gracefully, no command.
            app_pkg.devices.EVENT_DEDUPE_CACHE.clear()
            sent.clear()
            app_pkg.devices.handle_node_event('AA:BB:CC:DD:EE:FF', '{"event":"checkin"}')
            check('checkin from unknown node -> no command, no crash', not sent)

            if failures:
                print(f'\n{len(failures)} check(s) FAILED')
                sys.exit(1)
            print('\nAll checkin-restore checks passed.')
        finally:
            cleanup_fixture()


if __name__ == '__main__':
    main()
