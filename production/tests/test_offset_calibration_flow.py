"""Verify the v4 drop-based HVAC offset-calibration protocol.

Firmware-driven flow: Button 2 -> `event_button2_offset_calibration_request` ->
backend approves with the `startcalibration` control command. The firmware
captures a baseline (AC off), then monitors the supply probe (DS2) and publishes
throttled `calibration_progress` events; on an >= 8 C drop it publishes
`calibration_success_request` with base/final readings, otherwise a
`calibration_fail_request` on the 10-minute timeout. The backend sanity-gates
the drop (supply >= 7.5 C, return >= 2.5 C), stores additive offsets that zero
the inter-probe error at the captured baseline, and acks the node.

Self-contained: creates its own user/appliance/node fixtures and cleans up.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Never let tests connect to the shared MQTT broker: each connection leaves a
# 1-hour persistent session behind (~60 wasted session-minutes per test run).
os.environ.setdefault("IOT_DISABLE_MQTT", "1")

import json
from datetime import datetime, timezone, timedelta
import bcrypt

from app import create_app
from app import models, calibration, devices

TEST_EMAIL = 'test_calib_flow@example.com'
TEST_PASSWORD = 'Test1234'
TEST_MAC = 'AA:BB:CC:DD:EE:03'
DRYER_MAC = 'AA:BB:CC:DD:EE:04'
UNKNOWN_MAC = 'AA:BB:CC:DD:EE:99'

USER_ID = None
APPLIANCE_ID = None
DRYER_APPLIANCE_ID = None
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
    global USER_ID, APPLIANCE_ID, DRYER_APPLIANCE_ID
    password_hash = bcrypt.hashpw(TEST_PASSWORD.encode(), bcrypt.gensalt()).decode()
    USER_ID = models.create_user(TEST_EMAIL, password_hash, 'Calib Flow Test')
    APPLIANCE_ID = sql("""
        INSERT INTO appliances
        (user_id, name, type, sub_type, is_inverter, location, brand,
         operational_status, cf, deductor, alert_enabled,
         treturn_offset, tsupply_offset)
        VALUES (%s, 'Calib Flow HVAC', 'HVAC', 'split_residential', FALSE, 'Home', 'Generic',
                'offset_calibration_needed', 11.0, 0.033, TRUE, 1.5, -1.5)
        RETURNING id
    """, (USER_ID,), fetch='one')[0]
    sql("""
        INSERT INTO sensor_nodes (mac_address, status, appliance_id, last_seen)
        VALUES (%s, 'paired', %s, NOW())
    """, (TEST_MAC, APPLIANCE_ID))

    DRYER_APPLIANCE_ID = sql("""
        INSERT INTO appliances
        (user_id, name, type, sub_type, is_inverter, location, brand,
         operational_status, cf, deductor, alert_enabled)
        VALUES (%s, 'Calib Flow Dryer', 'Clothes Dryer', 'dryer_standard', FALSE, 'Home', 'Generic',
                'normal', 33.0, 0.111, TRUE)
        RETURNING id
    """, (USER_ID,), fetch='one')[0]
    sql("""
        INSERT INTO sensor_nodes (mac_address, status, appliance_id, last_seen)
        VALUES (%s, 'paired', %s, NOW())
    """, (DRYER_MAC, DRYER_APPLIANCE_ID))


def cleanup_fixture():
    for appliance_id in (APPLIANCE_ID, DRYER_APPLIANCE_ID):
        if not appliance_id:
            continue
        sql("DELETE FROM hvac_readings WHERE sensor_node_id IN (SELECT id FROM sensor_nodes WHERE appliance_id = %s)", (appliance_id,))
        sql("DELETE FROM sensor_nodes WHERE appliance_id = %s", (appliance_id,))
        sql("DELETE FROM appliances WHERE id = %s", (appliance_id,))
    if USER_ID:
        sql("DELETE FROM users WHERE id = %s", (USER_ID,))
    for appliance_id in (APPLIANCE_ID, DRYER_APPLIANCE_ID):
        calibration.CALIBRATION_TRACKER.pop(appliance_id, None)


def send_event(mac, event_type, **fields):
    payload = json.dumps({"mac": mac, "event": event_type, **fields})
    devices.handle_node_event(mac, payload)


def get_status(appliance_id=None):
    appliance_id = appliance_id or APPLIANCE_ID
    return sql("SELECT operational_status FROM appliances WHERE id = %s", (appliance_id,), fetch='one')[0]


def get_offsets():
    return sql("SELECT treturn_offset, tsupply_offset FROM appliances WHERE id = %s", (APPLIANCE_ID,), fetch='one')


def begin_session():
    # Mirror production _begin_session: zero offsets, remember previous ones.
    prev = get_offsets()
    models.update_offsets(APPLIANCE_ID, 0.0, 0.0)
    models.update_operational_status(APPLIANCE_ID, 'calibrating')
    calibration.CALIBRATION_TRACKER[APPLIANCE_ID] = {
        'start_time': calibration.config.now_wib(),
        'ds1': None, 'base_ds1': None, 'delta': 0.0,
        'prev_offsets': (prev[0] or 0.0, prev[1] or 0.0),
        'suspected_dead_at': None,
    }


def main():
    app = create_app()
    with app.app_context():
        import app as app_pkg
        app_pkg.mqtt.send_node_command = lambda mac, cmd: COMMANDS.append(cmd)
        setup_fixture()
        failures = []

        def check(name, cond):
            print(('PASS' if cond else 'FAIL') + ': ' + name)
            if not cond:
                failures.append(name)

        try:
            # 1. Button 2 in offset_calibration_needed -> approve + raw telemetry.
            calibration.handle_offset_calibration_event(TEST_MAC)
            check('status -> calibrating', get_status() == 'calibrating')
            check('startcalibration command sent', COMMANDS == ['startcalibration'])
            check('offsets zeroed at session start', get_offsets() == (0.0, 0.0))
            check('tracker entry created', APPLIANCE_ID in calibration.CALIBRATION_TRACKER)

            # 2. Re-press while a session is active -> busy denial.
            COMMANDS.clear()
            calibration.handle_offset_calibration_event(TEST_MAC)
            check('re-press -> actiondenied:busy', 'actiondenied:busy' in COMMANDS)
            check('still calibrating after busy denial', get_status() == 'calibrating')

            # 3. Backend restart mid-session (tracker lost) -> fresh approval.
            calibration.CALIBRATION_TRACKER.pop(APPLIANCE_ID, None)
            COMMANDS.clear()
            calibration.handle_offset_calibration_event(TEST_MAC)
            check('restart heal -> startcalibration resent', COMMANDS == ['startcalibration'])
            check('tracker recreated', APPLIANCE_ID in calibration.CALIBRATION_TRACKER)

            # 3b. Stale session (firmware rebooted, 10-min timeout elapsed) -> fresh approval.
            begin_session()
            stale_start = datetime.now(timezone.utc) - timedelta(seconds=calibration.TIMEOUT_SECONDS + 10)
            calibration.CALIBRATION_TRACKER[APPLIANCE_ID]['start_time'] = stale_start
            COMMANDS.clear()
            calibration.handle_offset_calibration_event(TEST_MAC)
            check('stale session -> startcalibration resent (not busy)', COMMANDS == ['startcalibration'])

            # 4. calibration_progress events bypass the 5 s event dedupe.
            # Progress now reports the REFERENCE probe (ds1, Port 1).
            send_event(TEST_MAC, 'calibration_progress', ds1=27.5, base_ds1=28.3, delta=0.8)
            send_event(TEST_MAC, 'calibration_progress', ds1=26.3, base_ds1=28.3, delta=2.0)
            tracker = calibration.CALIBRATION_TRACKER.get(APPLIANCE_ID, {})
            check('both progress events processed (dedupe exemption)',
                  tracker.get('ds1') == 26.3 and tracker.get('delta') == 2.0)

            # 5. Progress endpoint reports the live drop.
            client = app.test_client()
            client.post('/login', data={'email': TEST_EMAIL, 'password': TEST_PASSWORD})
            r = client.get(f'/api/device/{APPLIANCE_ID}/calibration_progress')
            data = r.get_json()
            check('progress endpoint 200', r.status_code == 200)
            check('progress reports drop fields',
                  data.get('state') == 'calibrating' and data.get('required') == 8.0
                  and data.get('ds1') == 26.3 and data.get('base_ds1') == 28.3
                  and data.get('delta') == 2.0 and data.get('progress_pct') == 25)

            # 5b. Stale session detected by the progress endpoint -> status reverted.
            begin_session()
            calibration.CALIBRATION_TRACKER[APPLIANCE_ID]['start_time'] = stale_start
            r = client.get(f'/api/device/{APPLIANCE_ID}/calibration_progress')
            check('stale session via endpoint -> progress_pct 100', r.get_json().get('progress_pct') == 100)
            check('stale session via endpoint -> status reverted', get_status() == 'offset_calibration_needed')

            # 5c. Initial calibration is mandatory: cancel is rejected before any
            # successful calibration (both pending and calibrating states).
            COMMANDS.clear()
            r = client.post(f'/api/device/{APPLIANCE_ID}/calibration/cancel')
            check('cancel rejected before first calibration (pending)', r.status_code == 409)
            check('status unchanged after rejected cancel (pending)',
                  get_status() == 'offset_calibration_needed')
            begin_session()
            r = client.post(f'/api/device/{APPLIANCE_ID}/calibration/cancel')
            check('cancel rejected before first calibration (calibrating)', r.status_code == 409)
            check('status unchanged after rejected cancel (calibrating)',
                  get_status() == 'calibrating')
            calibration.CALIBRATION_TRACKER.pop(APPLIANCE_ID, None)
            models.update_operational_status(APPLIANCE_ID, 'offset_calibration_needed')

            # 6. Success request with a valid drop -> offsets + normal + ack.
            # Gates: DS1 (reference) drop >= 7.5 signed, DS2 (tracking) >= 2.5.
            node_id = sql("SELECT id FROM sensor_nodes WHERE appliance_id = %s", (APPLIANCE_ID,), fetch='one')[0]
            # Raw calibration-session reading recorded before the success; the
            # initial calibration must delete it (display window starts here).
            sql("INSERT INTO hvac_readings (sensor_node_id, time, treturn, tsupply, icompressor) VALUES (%s, NOW() - INTERVAL '5 minutes', 25.0, 24.0, 0.5)", (node_id,))
            begin_session()
            COMMANDS.clear()
            send_event(TEST_MAC, 'calibration_success_request',
                       elapsedms=95000, deltaT=8.5,
                       base={'ds1': 28.3, 'ds2': 28.6},
                       final={'ds1': 19.8, 'ds2': 20.5})
            check('success -> status normal', get_status() == 'normal')
            treturn_offset, tsupply_offset = get_offsets()
            check('treturn offset = 0 (sensor 1 is the reference)', treturn_offset == 0.0)
            check('tsupply offset = base_ds1 - base_ds2 = -0.3',
                  abs(tsupply_offset - (-0.3)) < 0.001)
            check('success ack sent', 'offsetcalibrationsuccessack' in COMMANDS)
            check('tracker cleared on success', APPLIANCE_ID not in calibration.CALIBRATION_TRACKER)
            check('calibrated_at set on success',
                  sql('SELECT calibrated_at FROM appliances WHERE id = %s',
                      (APPLIANCE_ID,), fetch='one')[0] is not None)
            check('initial_calibrated_at set on first success',
                  sql('SELECT initial_calibrated_at FROM appliances WHERE id = %s',
                      (APPLIANCE_ID,), fetch='one')[0] is not None)
            check('initial calibration deleted pre-calibration raw readings',
                  sql('SELECT COUNT(*) FROM hvac_readings WHERE sensor_node_id = %s',
                      (node_id,), fetch='one')[0] == 0)
            sql("INSERT INTO hvac_readings (sensor_node_id, time, treturn, tsupply, icompressor) VALUES (%s, NOW(), 25.0, 24.0, 0.5)", (node_id,))
            check('post-calibration readings kept',
                  sql('SELECT COUNT(*) FROM hvac_readings WHERE sensor_node_id = %s',
                      (node_id,), fetch='one')[0] == 1)

            # 6d. Re-calibration success: initial timestamp unchanged, no deletion.
            initial_ts = sql('SELECT initial_calibrated_at FROM appliances WHERE id = %s',
                             (APPLIANCE_ID,), fetch='one')[0]
            devices.EVENT_DEDUPE_CACHE.clear()
            begin_session()
            send_event(TEST_MAC, 'calibration_success_request',
                       elapsedms=90000, deltaT=8.5,
                       base={'ds1': 28.3, 'ds2': 28.6},
                       final={'ds1': 19.8, 'ds2': 20.5})
            check('re-calibration keeps initial_calibrated_at',
                  sql('SELECT initial_calibrated_at FROM appliances WHERE id = %s',
                      (APPLIANCE_ID,), fetch='one')[0] == initial_ts)
            check('re-calibration keeps history',
                  sql('SELECT COUNT(*) FROM hvac_readings WHERE sensor_node_id = %s',
                      (node_id,), fetch='one')[0] == 1)

            # 6b. Rejected drop restores the previous offsets (state recovery).
            begin_session()  # zeroes (0.0, -0.3), stores them as prev
            devices.EVENT_DEDUPE_CACHE.clear()
            COMMANDS.clear()
            send_event(TEST_MAC, 'calibration_success_request',
                       elapsedms=60000, deltaT=5.0,
                       base={'ds1': 28.3, 'ds2': 28.6},
                       final={'ds1': 23.3, 'ds2': 23.9})
            check('rejected drop -> previous offsets restored',
                  get_offsets() == (0.0, -0.3))

            # 7. Reference (DS1) drop too small -> rejected, back to needed.
            devices.EVENT_DEDUPE_CACHE.clear()  # distinct attempt, not a duplicate
            begin_session()
            COMMANDS.clear()
            send_event(TEST_MAC, 'calibration_success_request',
                       elapsedms=60000, deltaT=5.0,
                       base={'ds1': 28.3, 'ds2': 28.6},
                       final={'ds1': 23.3, 'ds2': 23.9})
            check('small reference drop rejected -> offset_calibration_needed',
                  get_status() == 'offset_calibration_needed')
            check('fail ack sent on rejected drop', 'offsetcalibrationfailack' in COMMANDS)

            # 7b. Temperature RISE on the reference must never complete (signed drop).
            devices.EVENT_DEDUPE_CACHE.clear()
            begin_session()
            COMMANDS.clear()
            send_event(TEST_MAC, 'calibration_success_request',
                       elapsedms=120000, deltaT=8.3,
                       base={'ds1': 28.3, 'ds2': 28.6},
                       final={'ds1': 36.3, 'ds2': 37.0})
            check('warming rejected -> offset_calibration_needed',
                  get_status() == 'offset_calibration_needed')
            check('fail ack sent on warming', 'offsetcalibrationfailack' in COMMANDS)

            # 7d. Tracking gate: DS1 dropped enough but DS2 did not follow -> reject.
            devices.EVENT_DEDUPE_CACHE.clear()
            begin_session()
            COMMANDS.clear()
            send_event(TEST_MAC, 'calibration_success_request',
                       elapsedms=120000, deltaT=8.5,
                       base={'ds1': 28.3, 'ds2': 28.6},
                       final={'ds1': 19.8, 'ds2': 27.6})
            check('tracking drop < 2.5 rejected -> offset_calibration_needed',
                  get_status() == 'offset_calibration_needed')
            check('fail ack sent on tracking failure', 'offsetcalibrationfailack' in COMMANDS)

            # 7c. Negative delta clamps the progress bar at 0 (temp rising).
            begin_session()
            send_event(TEST_MAC, 'calibration_progress', ds1=30.2, base_ds1=28.3, delta=-1.9)
            r = client.get(f'/api/device/{APPLIANCE_ID}/calibration_progress')
            data = r.get_json()
            check('negative delta clamped to progress_pct 0',
                  data.get('delta') == -1.9 and data.get('progress_pct') == 0)
            models.update_offsets(APPLIANCE_ID, 0.0, -0.3)
            models.update_operational_status(APPLIANCE_ID, 'offset_calibration_needed')
            calibration.CALIBRATION_TRACKER.pop(APPLIANCE_ID, None)

            # 8. Success request while not calibrating -> ignored.
            devices.EVENT_DEDUPE_CACHE.clear()
            COMMANDS.clear()
            send_event(TEST_MAC, 'calibration_success_request',
                       base={'ds1': 28.3, 'ds2': 28.7}, final={'ds1': 25.3, 'ds2': 20.4})
            check('success ignored when not calibrating', not COMMANDS and get_status() == 'offset_calibration_needed')

            # 9. Firmware timeout (fail_request) -> revert + fail ack + offsets restored.
            begin_session()
            COMMANDS.clear()
            send_event(TEST_MAC, 'calibration_fail_request', elapsedms=600000, reason='timeout10min')
            check('fail_request -> offset_calibration_needed', get_status() == 'offset_calibration_needed')
            check('fail ack sent on fail_request', 'offsetcalibrationfailack' in COMMANDS)
            check('tracker cleared on fail', APPLIANCE_ID not in calibration.CALIBRATION_TRACKER)
            check('fail_request -> previous offsets restored',
                  get_offsets() == (0.0, -0.3))

            # 10. fail_request while not calibrating (approval timeout) -> no ack.
            devices.EVENT_DEDUPE_CACHE.clear()
            COMMANDS.clear()
            send_event(TEST_MAC, 'calibration_fail_request', elapsedms=10000, reason='approval_timeout')
            check('approval-timeout fail_request not acked', not COMMANDS)

            # 11. Dryer node -> calibration not supported.
            COMMANDS.clear()
            calibration.handle_offset_calibration_event(DRYER_MAC)
            check('dryer denied with failack',
                  COMMANDS == ['offsetcalibrationfailack'] and get_status(DRYER_APPLIANCE_ID) == 'normal')

            # 12. Unknown / unpaired node -> failack.
            COMMANDS.clear()
            calibration.handle_offset_calibration_event(UNKNOWN_MAC)
            check('unknown node denied with failack', COMMANDS == ['offsetcalibrationfailack'])

            # 13. Recalibrate endpoint: normal -> needed, node notified immediately.
            models.update_operational_status(APPLIANCE_ID, 'normal')
            COMMANDS.clear()
            r = client.post(f'/api/device/{APPLIANCE_ID}/recalibrate')
            check('recalibrate 200 from normal', r.status_code == 200)
            check('recalibrate -> offset_calibration_needed', get_status() == 'offset_calibration_needed')
            check('recalibrate sends restore:offsetcalibrationneeded',
                  COMMANDS == ['restore:offsetcalibrationneeded'])

            # 13b. Recalibrate rejected while calibrating.
            begin_session()
            r = client.post(f'/api/device/{APPLIANCE_ID}/recalibrate')
            check('recalibrate 409 while calibrating', r.status_code == 409)
            calibration.CALIBRATION_TRACKER.pop(APPLIANCE_ID, None)
            models.update_offsets(APPLIANCE_ID, 0.0, -0.3)
            models.update_operational_status(APPLIANCE_ID, 'offset_calibration_needed')

            # 13c. Recalibrate rejected for dryer.
            r = client.post(f'/api/device/{DRYER_APPLIANCE_ID}/recalibrate')
            check('recalibrate 400 for dryer', r.status_code == 400)

            # 14. Cancel from pending (offset_calibration_needed): no failack needed.
            COMMANDS.clear()
            r = client.post(f'/api/device/{APPLIANCE_ID}/calibration/cancel')
            check('cancel from needed 200', r.status_code == 200)
            check('cancel from needed -> normal', get_status() == 'normal')
            check('cancel from needed sends restore:normal only',
                  COMMANDS == ['restore:normal'])

            # 14b. Cancel while normal -> nothing to cancel.
            r = client.post(f'/api/device/{APPLIANCE_ID}/calibration/cancel')
            check('cancel 409 while normal', r.status_code == 409)

            # 15. Cancel from calibrating: failack + restore:normal + offsets restored.
            begin_session()  # zeroes (0.0, -0.3), stores them as prev
            COMMANDS.clear()
            r = client.post(f'/api/device/{APPLIANCE_ID}/calibration/cancel')
            check('cancel from calibrating 200', r.status_code == 200)
            check('cancel from calibrating -> normal', get_status() == 'normal')
            check('cancel sends failack then restore:normal',
                  COMMANDS == ['offsetcalibrationfailack', 'restore:normal'])
            check('cancel restores previous offsets', get_offsets() == (0.0, -0.3))
            check('tracker cleared on cancel', APPLIANCE_ID not in calibration.CALIBRATION_TRACKER)

            # 15b. Cancel rejected for dryer.
            r = client.post(f'/api/device/{DRYER_APPLIANCE_ID}/calibration/cancel')
            check('cancel 400 for dryer', r.status_code == 400)

            # 16. Node reconnects mid-calibration (event_request_config):
            # stale/missing tracker -> revert to needed; live tracker -> keep
            # (with a prove-alive suspicion window, see 17).
            devices.EVENT_DEDUPE_CACHE.clear()
            begin_session()  # zeroes (0.0, -0.3), stores them as prev
            calibration.CALIBRATION_TRACKER[APPLIANCE_ID]['start_time'] = stale_start
            COMMANDS.clear()
            send_event(TEST_MAC, 'event_request_config')
            check('reconnect with stale tracker -> reverted to needed',
                  get_status() == 'offset_calibration_needed')
            check('reconnect with stale tracker -> previous offsets restored',
                  get_offsets() == (0.0, -0.3))
            check('config still sent after revert',
                  'settype:hvac' in COMMANDS and 'restore:offsetcalibrationneeded' in COMMANDS)

            # 16b. No tracker at all (backend restarted, node rebooted) -> revert.
            devices.EVENT_DEDUPE_CACHE.clear()
            models.update_operational_status(APPLIANCE_ID, 'calibrating')
            models.update_offsets(APPLIANCE_ID, 0.0, 0.0)
            calibration.CALIBRATION_TRACKER.pop(APPLIANCE_ID, None)
            COMMANDS.clear()
            send_event(TEST_MAC, 'event_request_config')
            check('reconnect with no tracker -> reverted to needed',
                  get_status() == 'offset_calibration_needed')

            # 16c. Live tracker (WiFi blip mid-session) -> session preserved for now.
            devices.EVENT_DEDUPE_CACHE.clear()
            begin_session()  # live tracker
            COMMANDS.clear()
            send_event(TEST_MAC, 'event_request_config')
            check('reconnect with live tracker -> still calibrating (suspicion armed)',
                  get_status() == 'calibrating')
            calibration.CALIBRATION_TRACKER.pop(APPLIANCE_ID, None)
            models.update_operational_status(APPLIANCE_ID, 'offset_calibration_needed')

            # 17. Prove-alive deadline: a reconnect arms a 30 s suspicion window;
            # a calibration_progress event clears it, otherwise the next
            # reconcile reverts the dead session (power-cut mid-calibration).
            models.update_offsets(APPLIANCE_ID, 0.0, -0.3)
            begin_session()
            calibration.reconcile_on_node_connect(APPLIANCE_ID)
            tracker = calibration.CALIBRATION_TRACKER.get(APPLIANCE_ID, {})
            check('reconnect arms suspicion, session kept',
                  get_status() == 'calibrating' and tracker.get('suspected_dead_at') is not None)

            # 17b. Progress event proves the session alive -> no revert.
            send_event(TEST_MAC, 'calibration_progress', ds1=27.0, base_ds1=28.3, delta=1.3)
            tracker = calibration.CALIBRATION_TRACKER.get(APPLIANCE_ID, {})
            check('progress clears suspicion', tracker.get('suspected_dead_at') is None)
            appliance = models.get_appliance(APPLIANCE_ID)
            check('alive session not reverted',
                  not calibration.reconcile_calibration_state(appliance)
                  and get_status() == 'calibrating')

            # 17c. No progress before the deadline (node died) -> revert + restore.
            calibration.reconcile_on_node_connect(APPLIANCE_ID)
            tracker = calibration.CALIBRATION_TRACKER[APPLIANCE_ID]
            past = calibration.config.now_wib() - timedelta(seconds=calibration.PROGRESS_GRACE_SECONDS + 5)
            tracker['suspected_dead_at'] = past
            appliance = models.get_appliance(APPLIANCE_ID)
            check('dead session reverted by reconcile',
                  calibration.reconcile_calibration_state(appliance))
            check('dead session -> offset_calibration_needed',
                  get_status() == 'offset_calibration_needed')
            check('dead session -> previous offsets restored',
                  get_offsets() == (0.0, -0.3))
            calibration.CALIBRATION_TRACKER.pop(APPLIANCE_ID, None)

            if failures:
                print(f'\n{len(failures)} check(s) FAILED')
                sys.exit(1)
            print('\nAll offset-calibration flow checks passed.')
        finally:
            cleanup_fixture()


if __name__ == '__main__':
    main()
