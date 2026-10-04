"""Sensor-profile pairing + settings tests.

Covers the sensor-aware pairing flow:
  - get_cf_deductor() maps each current-sensor model to its calibrated
    CF/deductor and falls back to the default profile for unknown/legacy values
  - POST /devices/pair validates device type and current_sensor, stores the
    sensor profile on the appliance, and pushes setcf/setdeductor to the node
  - current_sensor is fixed at pairing: POST /api/device/<id>/settings cannot
    change it (rejected, no MQTT re-push)

Self-contained: creates its own user/node fixture and cleans up.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Never let tests connect to the shared MQTT broker: each connection leaves a
# 1-hour persistent session behind (~60 wasted session-minutes per test run).
os.environ.setdefault("IOT_DISABLE_MQTT", "1")

import bcrypt

from app import create_app, config
from app import models
from app.devices import get_cf_deductor

TEST_EMAIL = 'test_sensor_profiles@example.com'
TEST_PASSWORD = 'Test1234'
TEST_MAC = 'AA:BB:CC:DD:EE:05'

USER_ID = None
NODE_ID = None
MQTT_SENT = []


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
    global USER_ID, NODE_ID
    password_hash = bcrypt.hashpw(TEST_PASSWORD.encode(), bcrypt.gensalt()).decode()
    USER_ID = models.create_user(TEST_EMAIL, password_hash, 'Sensor Test')
    NODE_ID = sql(
        "INSERT INTO sensor_nodes (mac_address, status) VALUES (%s, 'unpaired') RETURNING id",
        (TEST_MAC,), fetch='one',
    )[0]


def cleanup_fixture():
    sql("DELETE FROM sensor_nodes WHERE mac_address = %s", (TEST_MAC,))
    sql("DELETE FROM appliances WHERE user_id = %s", (USER_ID,))
    sql("DELETE FROM users WHERE id = %s", (USER_ID,))


def appliance_count():
    return sql("SELECT COUNT(*) FROM appliances WHERE user_id = %s", (USER_ID,), fetch='one')[0]


def main():
    app = create_app()
    with app.app_context():
        import app as app_pkg
        app_pkg.mqtt.send_node_command = lambda mac, cmd: MQTT_SENT.append((mac, cmd))
        setup_fixture()
        failures = []

        def check(name, cond):
            print(('PASS' if cond else 'FAIL') + ': ' + name)
            if not cond:
                failures.append(name)

        try:
            # --- pure mapping checks (no DB) ---
            check('SCT013-015 -> CF 33.0 / deductor 0.111',
                  get_cf_deductor('SCT013-015') == (33.0, 0.111))
            check('ZHT103C -> CF 11.0 / deductor 0.033',
                  get_cf_deductor('ZHT103C') == (11.0, 0.033))
            check('unknown sensor -> default profile',
                  get_cf_deductor('NOPE') == get_cf_deductor(config.DEFAULT_CURRENT_SENSOR))
            check('legacy NULL sensor -> default profile',
                  get_cf_deductor(None) == get_cf_deductor(config.DEFAULT_CURRENT_SENSOR))

            client = app.test_client()
            client.post('/login', data={'email': TEST_EMAIL, 'password': TEST_PASSWORD})

            # --- pair validation: bad sensor rejected ---
            MQTT_SENT.clear()
            client.post('/devices/pair', data={
                'name': 'Bad Sensor AC', 'type': 'HVAC',
                'current_sensor': 'SCT-013-000', 'node_id': str(NODE_ID),
            })
            check('pair with unknown sensor rejected', appliance_count() == 0)

            # --- pair validation: bad type rejected ---
            client.post('/devices/pair', data={
                'name': 'Bad Type', 'type': 'Air Conditioner',
                'current_sensor': 'ZHT103C', 'node_id': str(NODE_ID),
            })
            check('pair with legacy type string rejected', appliance_count() == 0)

            # --- valid HVAC + ZHT103C pair ---
            MQTT_SENT.clear()
            client.post('/devices/pair', data={
                'name': 'Bedroom AC', 'type': 'HVAC', 'is_inverter': '1',
                'current_sensor': 'ZHT103C', 'node_id': str(NODE_ID),
            })
            check('valid HVAC pair created appliance', appliance_count() == 1)
            row = sql(
                "SELECT type, current_sensor, cf, deductor, is_inverter, operational_status "
                "FROM appliances WHERE user_id = %s", (USER_ID,), fetch='one')
            check('paired row: type=HVAC, sensor=ZHT103C, cf=11.0, deductor=0.033, inverter, calib needed',
                  row == ('HVAC', 'ZHT103C', 11.0, 0.033, True, 'offset_calibration_needed'))
            cmds = [c for _, c in MQTT_SENT]
            check('pair pushed settype:hvac', 'settype:hvac' in cmds)
            check('pair pushed setcf:11.0', 'setcf:11.0' in cmds)
            check('pair pushed setdeductor:0.033', 'setdeductor:0.033' in cmds)
            check('pair pushed restore:offsetcalibrationneeded',
                  'restore:offsetcalibrationneeded' in cmds)

            app_id = sql("SELECT id FROM appliances WHERE user_id = %s", (USER_ID,), fetch='one')[0]

            # --- settings: GET exposes current_sensor (read-only info) ---
            s = client.get(f'/api/device/{app_id}/settings').get_json()
            check('settings GET returns current_sensor', s.get('current_sensor') == 'ZHT103C')

            # --- settings: sensor is fixed at pairing, POST cannot change it ---
            MQTT_SENT.clear()
            r = client.post(f'/api/device/{app_id}/settings', json={'current_sensor': 'SCT013-015'})
            check('settings POST current_sensor rejected (400)', r.status_code == 400)
            row = sql("SELECT current_sensor, cf, deductor FROM appliances WHERE id = %s",
                      (app_id,), fetch='one')
            check('sensor/cf/deductor unchanged after rejected POST',
                  row == ('ZHT103C', 11.0, 0.033))
            check('rejected POST pushes nothing to node', len(MQTT_SENT) == 0)

            # --- dryer pair uses sensor profile too (fresh node) ---
            node2 = sql(
                "INSERT INTO sensor_nodes (mac_address, status) VALUES ('AA:BB:CC:DD:EE:06', 'unpaired') RETURNING id",
                fetch='one')[0]
            MQTT_SENT.clear()
            client.post('/devices/pair', data={
                'name': 'Laundry Dryer', 'type': 'Gas Dryer',
                'current_sensor': 'SCT013-015', 'node_id': str(node2),
            })
            row = sql(
                "SELECT type, current_sensor, cf, deductor, operational_status "
                "FROM appliances WHERE name = 'Laundry Dryer'", fetch='one')
            check('dryer pair: type=Gas Dryer, SCT013-015, cf=33.0, status normal',
                  row == ('Gas Dryer', 'SCT013-015', 33.0, 0.111, 'normal'))
            cmds = [c for _, c in MQTT_SENT]
            check('dryer pair pushed settype:dryer + setcf:33.0',
                  'settype:dryer' in cmds and 'setcf:33.0' in cmds)
            sql("DELETE FROM sensor_nodes WHERE mac_address = 'AA:BB:CC:DD:EE:06'")

            if failures:
                print(f'\n{len(failures)} check(s) FAILED')
                sys.exit(1)
            print('\nAll sensor-profile checks passed.')
        finally:
            cleanup_fixture()


if __name__ == '__main__':
    main()
