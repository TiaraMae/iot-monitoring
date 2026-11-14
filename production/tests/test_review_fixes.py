"""Regression checks for the 2026-11-14 code-review fixes.

Each fix is behavior-preserving for normal operation; these checks pin the
corrected edge cases:
  S1  POST /api/send_command no longer exists.
  S2  Discord webhook URLs are allowlisted (save, test, and at send time).
  S6  Signup duplicate email shows a friendly message, never DB error text.
  S8  OTA token check still accepts the right token and refuses others.
  B1  Delta-T is null (not a fake difference vs 0 C) when a probe is missing.
  B2  Pre-calibration offsets survive a backend restart mid-session.
  B5  restore:normal <-> checkin ping-pong is cut; the stuck-node heal still fires.
  B7  Pairing a node that was claimed meanwhile is refused, nothing leaks.
  B8  A malformed calibration success payload is rejected cleanly.
  /healthz reports db/mqtt booleans (503 when MQTT is down, as in tests).
  N+1  get_appliances_for_user alert_status == get_appliance_alert_status.

Self-contained fixture; cleans up. MQTT is disabled and send_node_command is
captured, so nothing reaches the shared broker.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("IOT_DISABLE_MQTT", "1")

import time
from datetime import timedelta

import bcrypt

from app import create_app, models, config, limiter
from app import db as db_mod
import app.alerts as alerts_mod
import app.calibration as calibration
import app.devices as devices

TEST_EMAIL = 'test_reviewfixes@example.com'
OTHER_EMAIL = 'test_reviewfixes_other@example.com'
SIGNUP_EMAIL = 'test_reviewfixes_signup@example.com'
TEST_PASSWORD = 'Test1234'
HVAC_MAC = 'RF:00:00:00:00:01'
FREE_MAC = 'RF:00:00:00:00:02'
GOOD_HOOK = 'https://discord.com/api/webhooks/123456/abcdef'

USER_ID = None
OTHER_ID = None
APP_ID = None
NODE_ID = None
FREE_NODE_ID = None
COMMANDS = []


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
    global USER_ID, OTHER_ID, APP_ID, NODE_ID, FREE_NODE_ID
    cleanup_fixture()
    h = bcrypt.hashpw(TEST_PASSWORD.encode(), bcrypt.gensalt()).decode()
    USER_ID = models.create_user(TEST_EMAIL, h, 'Review Fixes')
    OTHER_ID = models.create_user(OTHER_EMAIL, h, 'Review Other')
    APP_ID = sql("""
        INSERT INTO appliances
        (user_id, name, type, is_inverter, location, brand, operational_status,
         cf, deductor, current_sensor, treturn_offset, tsupply_offset,
         calibrated_at, initial_calibrated_at)
        VALUES (%s, 'Review AC', 'HVAC', FALSE, 'Home', 'Generic', 'normal',
                11.0, 0.033, 'ZHT103C', 0.0, -0.4, NOW(), NOW())
        RETURNING id
    """, (USER_ID,), fetch='one')[0]
    NODE_ID = sql("""
        INSERT INTO sensor_nodes (mac_address, status, appliance_id, last_seen)
        VALUES (%s, 'paired', %s, NOW()) RETURNING id
    """, (HVAC_MAC, APP_ID), fetch='one')[0]
    FREE_NODE_ID = sql("""
        INSERT INTO sensor_nodes (mac_address, status, last_seen)
        VALUES (%s, 'unpaired', NOW()) RETURNING id
    """, (FREE_MAC,), fetch='one')[0]


def cleanup_fixture():
    for mac in (HVAC_MAC, FREE_MAC):
        row = sql("SELECT id, appliance_id FROM sensor_nodes WHERE mac_address = %s",
                  (mac,), fetch='one')
        if row:
            sql("DELETE FROM hvac_readings WHERE sensor_node_id = %s", (row[0],))
            sql("DELETE FROM dryer_readings WHERE sensor_node_id = %s", (row[0],))
            sql("DELETE FROM sensor_events WHERE sensor_node_mac = %s", (mac,))
            sql("DELETE FROM sensor_nodes WHERE id = %s", (row[0],))
    for email in (TEST_EMAIL, OTHER_EMAIL, SIGNUP_EMAIL):
        row = sql("SELECT id FROM users WHERE email = %s", (email,), fetch='one')
        if not row:
            continue
        uid = row[0]
        for (aid,) in sql("SELECT id FROM appliances WHERE user_id = %s", (uid,), fetch='all'):
            sql("DELETE FROM alerts WHERE appliance_id = %s", (aid,))
            sql("DELETE FROM spc_manual_baselines WHERE appliance_id = %s", (aid,))
            calibration.CALIBRATION_TRACKER.pop(aid, None)
        sql("DELETE FROM appliances WHERE user_id = %s", (uid,))
        sql("DELETE FROM users WHERE id = %s", (uid,))


def offsets():
    return sql("SELECT treturn_offset, tsupply_offset FROM appliances WHERE id = %s",
               (APP_ID,), fetch='one')


def saved_prev():
    return sql("SELECT calib_prev_treturn_offset, calib_prev_tsupply_offset "
               "FROM appliances WHERE id = %s", (APP_ID,), fetch='one')


def status():
    return sql("SELECT operational_status FROM appliances WHERE id = %s",
               (APP_ID,), fetch='one')[0]


def main():
    app = create_app()
    import app as app_pkg
    app_pkg.mqtt.send_node_command = lambda mac, cmd: COMMANDS.append((mac, cmd))
    failures = []

    def check(name, cond):
        print(('PASS' if cond else 'FAIL') + ': ' + name)
        if not cond:
            failures.append(name)

    setup_fixture()
    try:
        with app.test_client() as client:
            client.post('/login', data={'email': TEST_EMAIL, 'password': TEST_PASSWORD})

            # --- S1: the arbitrary-command passthrough is gone.
            r = client.post('/api/send_command', json={'mac': HVAC_MAC, 'command': 'settype:unpaired'})
            check('S1 /api/send_command -> 404', r.status_code == 404)
            check('S1 no command published', not COMMANDS)

            # --- S2: webhook allowlist on save + test.
            for bad in ('http://127.0.0.1:5000/x',
                        'http://discord.com/api/webhooks/1/a',
                        'https://discord.com.evil.example/api/webhooks/1/a',
                        'https://discord.com@evil.example/api/webhooks/1/a',
                        'https://discord.com/not-webhooks/1/a'):
                r = client.post('/api/user/discord_webhook', json={'url': bad})
                check(f'S2 save rejects {bad}', r.status_code == 400)
            r = client.post('/api/user/discord_webhook', json={'url': GOOD_HOOK})
            check('S2 save accepts a Discord webhook', r.status_code == 200)
            r = client.post('/api/user/discord_webhook', json={'url': ''})
            check('S2 empty URL still clears', r.status_code == 200
                  and models.get_user_webhook_by_user_id(USER_ID) is None)

            posted = []
            real_post = devices.requests.post
            devices.requests.post = lambda *a, **k: posted.append(a)
            try:
                r = client.post('/api/user/discord_webhook/test',
                                json={'url': 'http://169.254.169.254/latest'})
                check('S2 test endpoint rejects non-Discord URL', r.status_code == 400)
                check('S2 test endpoint made no outbound request', not posted)
            finally:
                devices.requests.post = real_post

            # Send-time guard for URLs stored before the allowlist existed.
            real_ensure = alerts_mod._ensure_discord_worker
            alerts_mod._ensure_discord_worker = lambda: None
            try:
                while True:
                    alerts_mod._discord_queue.get_nowait()
                    alerts_mod._discord_queue.task_done()
            except Exception:
                pass
            try:
                sql("UPDATE users SET discord_webhook_url = %s WHERE id = %s",
                    ('http://internal.example/hook', USER_ID))
                alerts_mod.send_discord_alert(APP_ID, 'fault_hvac_low_delta_t', 'x',
                                              value=5.0, threshold=8.0, delay_minutes=5)
                check('S2 non-Discord stored URL is never queued',
                      alerts_mod._discord_queue.empty())
                sql("UPDATE users SET discord_webhook_url = %s WHERE id = %s",
                    (GOOD_HOOK, USER_ID))
                alerts_mod.send_discord_alert(APP_ID, 'fault_hvac_low_delta_t', 'x',
                                              value=5.0, threshold=8.0, delay_minutes=5)
                item = alerts_mod._discord_queue.get_nowait()
                alerts_mod._discord_queue.task_done()
                check('S2 Discord stored URL still queued', item[0] == GOOD_HOOK)
            finally:
                alerts_mod._ensure_discord_worker = real_ensure
                sql("UPDATE users SET discord_webhook_url = NULL WHERE id = %s", (USER_ID,))

            # --- B1: Delta-T null when a probe is missing.
            sql("INSERT INTO hvac_readings (sensor_node_id, time, treturn, tsupply, icompressor) "
                "VALUES (%s, NOW() - INTERVAL '20 seconds', 25.0, 15.0, 3.0)", (NODE_ID,))
            sql("INSERT INTO hvac_readings (sensor_node_id, time, treturn, tsupply, icompressor) "
                "VALUES (%s, NOW(), 25.0, NULL, 3.0)", (NODE_ID,))
            data = client.get(f'/api/device/{APP_ID}/latest').get_json()
            check('B1 /latest DeltaT is null with a missing probe',
                  data.get('has_data') is True and data.get('DeltaT') is None)
            rows = client.get(f'/api/device/{APP_ID}/latest_n?filtered=false').get_json()
            check('B1 latest_n DeltaT null for the probe-missing row',
                  rows and rows[0]['DeltaT'] is None)
            check('B1 latest_n DeltaT unchanged when both probes exist',
                  len(rows) > 1 and rows[1]['DeltaT'] == 10.0)

            # --- B2: pre-calibration offsets survive a backend restart.
            sql("UPDATE appliances SET operational_status = 'offset_calibration_needed', "
                "treturn_offset = 0.0, tsupply_offset = -0.4 WHERE id = %s", (APP_ID,))
            COMMANDS.clear()
            calibration.handle_offset_calibration_event(HVAC_MAC)
            check('B2 session started, offsets zeroed',
                  status() == 'calibrating' and offsets() == (0.0, 0.0))
            check('B2 pre-session offsets persisted', saved_prev() == (0.0, -0.4))
            calibration.CALIBRATION_TRACKER.pop(APP_ID, None)  # simulated restart
            calibration.handle_offset_calibration_event(HVAC_MAC)  # re-press
            tracker = calibration.CALIBRATION_TRACKER.get(APP_ID, {})
            check('B2 re-approval keeps the REAL previous offsets (not zeros)',
                  tracker.get('prev_offsets') == (0.0, -0.4))
            calibration.CALIBRATION_TRACKER.pop(APP_ID, None)  # restart again
            r = client.post(f'/api/device/{APP_ID}/calibration/cancel')
            check('B2 cancel after restart -> 200', r.status_code == 200)
            check('B2 cancel after restart restores offsets', offsets() == (0.0, -0.4))
            check('B2 cancel clears the persisted copy', saved_prev() == (None, None))
            check('B2 cancel -> normal', status() == 'normal')

            sql("UPDATE appliances SET operational_status = 'offset_calibration_needed' "
                "WHERE id = %s", (APP_ID,))
            calibration.handle_offset_calibration_event(HVAC_MAC)
            calibration.CALIBRATION_TRACKER.pop(APP_ID, None)  # restart
            reverted = calibration.reconcile_calibration_state(models.get_appliance(APP_ID))
            check('B2 reconcile after restart reverts', reverted
                  and status() == 'offset_calibration_needed')
            check('B2 reconcile after restart restores offsets', offsets() == (0.0, -0.4))
            check('B2 reconcile clears the persisted copy', saved_prev() == (None, None))

            # --- B8: malformed success payload -> clean reject.
            calibration.handle_offset_calibration_event(HVAC_MAC)
            COMMANDS.clear()
            try:
                calibration.handle_calibration_success_request(
                    HVAC_MAC, models.get_appliance(APP_ID), {'base': 'garbage', 'final': None})
                raised = False
            except Exception as e:
                raised = True
                print('   raised:', repr(e))
            check('B8 malformed payload does not raise', not raised)
            check('B8 malformed payload -> failack',
                  (HVAC_MAC, 'offsetcalibrationfailack') in COMMANDS)
            check('B8 malformed payload -> back to needed + offsets restored',
                  status() == 'offset_calibration_needed' and offsets() == (0.0, -0.4))
            sql("UPDATE appliances SET operational_status = 'normal' WHERE id = %s", (APP_ID,))

            # --- B5: restore:normal / checkin ping-pong.
            devices.LAST_RESTORE_NORMAL.pop(HVAC_MAC, None)
            devices.EVENT_DEDUPE_CACHE.clear()
            COMMANDS.clear()
            devices.handle_node_event(HVAC_MAC, '{"event":"event_request_config"}')
            check('B5 config request still sends restore:normal',
                  (HVAC_MAC, 'restore:normal') in COMMANDS)
            devices.EVENT_DEDUPE_CACHE.clear()
            COMMANDS.clear()
            devices.handle_node_event(HVAC_MAC, '{"event":"checkin"}')
            check('B5 echo checkin right after restore -> no second restore',
                  (HVAC_MAC, 'restore:normal') not in COMMANDS)
            devices.LAST_RESTORE_NORMAL[HVAC_MAC] = (
                time.time() - devices.RESTORE_HEAL_MIN_INTERVAL_SECONDS - 1)
            devices.EVENT_DEDUPE_CACHE.clear()
            COMMANDS.clear()
            devices.handle_node_event(HVAC_MAC, '{"event":"checkin"}')
            check('B5 later checkin still self-heals with restore:normal',
                  (HVAC_MAC, 'restore:normal') in COMMANDS)

            # --- N+1: alert_status from the single query == per-row function.
            sql("INSERT INTO alerts (appliance_id, alert_type, message, severity) "
                "VALUES (%s, 't', 'w', 'warning')", (APP_ID,))
            crit = sql("INSERT INTO alerts (appliance_id, alert_type, message, severity) "
                       "VALUES (%s, 't', 'c', 'critical') RETURNING id", (APP_ID,), fetch='one')[0]
            sql("INSERT INTO alerts (appliance_id, alert_type, message, severity, resolved_at) "
                "VALUES (%s, 't', 'old', 'critical', NOW())", (APP_ID,))

            def listed():
                return [a for a in models.get_appliances_for_user(USER_ID)
                        if a['id'] == APP_ID][0]['alert_status']
            check('N+1 worst unresolved = critical',
                  listed() == 'critical' == models.get_appliance_alert_status(APP_ID))
            sql("UPDATE alerts SET resolved_at = NOW() WHERE id = %s", (crit,))
            check('N+1 after resolving critical = warning',
                  listed() == 'warning' == models.get_appliance_alert_status(APP_ID))
            sql("UPDATE alerts SET resolved_at = NOW() WHERE appliance_id = %s", (APP_ID,))
            check('N+1 nothing unresolved = normal',
                  listed() == 'normal' == models.get_appliance_alert_status(APP_ID))

            # --- /healthz
            r = client.get('/healthz')
            body = r.get_json() or {}
            check('healthz db true, mqtt false (MQTT disabled in tests) -> 503',
                  r.status_code == 503 and body.get('db') is True
                  and body.get('mqtt') is False and body.get('status') == 'degraded')

            # --- S8: OTA token comparison still works.
            real_token = config.OTA_TOKEN
            config.OTA_TOKEN = 'review-token-123'
            try:
                r = client.get('/firmware/wrong-token/node_x.bin')
                check('S8 wrong OTA token -> 401', r.status_code == 401)
                r = client.get('/firmware/review-token-123/node_does_not_exist.bin')
                check('S8 right OTA token passes the check (404 missing file)',
                      r.status_code == 404)
            finally:
                config.OTA_TOKEN = real_token

            # --- B7: claim race — a node taken meanwhile is refused cleanly.
            used_before = len(db_mod.db_pool._used)
            r = client.post('/devices/pair', data={
                'name': 'Race 1', 'type': 'HVAC', 'current_sensor': 'ZHT103C',
                'node_id': str(FREE_NODE_ID)})
            check('B7 first pair succeeds', r.status_code == 302 and sql(
                "SELECT status FROM sensor_nodes WHERE id = %s",
                (FREE_NODE_ID,), fetch='one')[0] == 'paired')
            client.get('/logout')
            client.post('/login', data={'email': OTHER_EMAIL, 'password': TEST_PASSWORD})
            real_get_node = models.get_node_by_id
            # Simulate the race: the pre-check still sees the node as unpaired.
            models.get_node_by_id = lambda nid: {**real_get_node(nid), 'status': 'unpaired'}
            try:
                client.post('/devices/pair', data={
                    'name': 'Race 2', 'type': 'HVAC', 'current_sensor': 'ZHT103C',
                    'node_id': str(FREE_NODE_ID)})
            finally:
                models.get_node_by_id = real_get_node
            other_apps = sql("SELECT COUNT(*) FROM appliances WHERE user_id = %s",
                             (OTHER_ID,), fetch='one')[0]
            check('B7 losing pair request leaves no appliance row', other_apps == 0)
            check('B7 no pool connection leaked', len(db_mod.db_pool._used) == used_before)
            client.get('/logout')

        # --- S6: signup duplicate email -> friendly message, no DB text.
        limiter.reset()
        with app.test_client() as c2:
            c2.post('/signup', data={'name': 'Dup', 'email': TEST_EMAIL,
                                     'password': TEST_PASSWORD, 'confirm': TEST_PASSWORD})
            page = c2.get('/signup').get_data(as_text=True)
            check('S6 duplicate signup shows friendly message',
                  'An account with this email already exists.' in page)
            check('S6 duplicate signup leaks no DB error text',
                  'duplicate key' not in page and 'UniqueViolation' not in page)
    finally:
        cleanup_fixture()

    if failures:
        print(f'\n{len(failures)} check(s) FAILED')
        sys.exit(1)
    print('\nAll review-fix checks passed.')


if __name__ == '__main__':
    main()
