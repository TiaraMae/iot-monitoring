"""Regression checks for the outage-resilience fixes (2026-11-17).

  DB-POOL   A backend that starts while Postgres is down builds its pool on a
            later get_conn() (was: db_pool stayed None for the process life,
            the 2026-10-20 44 h zombie).
  NO-UNPAIR While the DB is unavailable, a paired node's config request or
            telemetry must NOT register it or send settype:unpaired (that
            command wipes the node's pairing/CF/deductor from flash).
  BACKLOG   Readings the broker queued while the backend was offline (agoms=0,
            delivered as a burst after reconnect) are re-timed at the 10 s
            cadence after the node's last stored reading, flagged
            time_estimated, and none are dropped by the 1 s dedupe. Live and
            agoms>0 readings keep today's timing; no session -> no re-timing.

Self-contained fixture; cleans up. MQTT disabled.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("IOT_DISABLE_MQTT", "1")

import json
from datetime import timedelta

import bcrypt
import psycopg2.pool

from app import create_app, models, config
import app.db as db
import app.mqtt as mqtt
import app.devices as devices
import app.telemetry as telemetry

TEST_EMAIL = 'test_outage@example.com'
TEST_PASSWORD = 'Test1234'
HVAC_MAC = 'OR:00:00:00:00:31'
UNKNOWN_MAC = 'OR:00:00:00:00:32'

USER_ID = HVAC_ID = HVAC_NODE = None


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
    for mac in (HVAC_MAC, UNKNOWN_MAC):
        row = sql("SELECT id FROM sensor_nodes WHERE mac_address = %s", (mac,), fetch='one')
        if row:
            sql("DELETE FROM hvac_readings WHERE sensor_node_id = %s", (row[0],))
            sql("DELETE FROM dryer_readings WHERE sensor_node_id = %s", (row[0],))
            sql("DELETE FROM sensor_nodes WHERE id = %s", (row[0],))
    row = sql("SELECT id FROM users WHERE email = %s", (TEST_EMAIL,), fetch='one')
    if row:
        for (aid,) in sql("SELECT id FROM appliances WHERE user_id = %s", (row[0],), fetch='all'):
            sql("DELETE FROM alerts WHERE appliance_id = %s", (aid,))
        sql("DELETE FROM appliances WHERE user_id = %s", (row[0],))
        sql("DELETE FROM users WHERE id = %s", (row[0],))


def setup_fixture():
    global USER_ID, HVAC_ID, HVAC_NODE
    cleanup_fixture()
    h = bcrypt.hashpw(TEST_PASSWORD.encode(), bcrypt.gensalt()).decode()
    USER_ID = models.create_user(TEST_EMAIL, h, 'Outage Test')
    HVAC_ID = sql("""
        INSERT INTO appliances (user_id, name, type, operational_status, cf, deductor,
                                current_sensor, calibrated_at, initial_calibrated_at)
        VALUES (%s, 'OR AC', 'HVAC', 'normal', 11.0, 0.033, 'ZHT103C', NOW(), NOW())
        RETURNING id""", (USER_ID,), fetch='one')[0]
    HVAC_NODE = sql("INSERT INTO sensor_nodes (mac_address, status, appliance_id, last_seen) "
                    "VALUES (%s, 'paired', %s, NOW()) RETURNING id", (HVAC_MAC, HVAC_ID),
                    fetch='one')[0]


class FakeClock:
    """Drives both telemetry's monotonic clock and config.now_wib()."""

    def __init__(self, wall):
        self.wall = wall
        self.mono = 1000.0

    def advance(self, seconds):
        self.wall += timedelta(seconds=seconds)
        self.mono += seconds


def payload(agoms=0):
    return json.dumps({"mac": HVAC_MAC, "type": "HVAC", "DS1Temp": 25.0, "DS2Temp": 12.0,
                       "CurrentA": 3.0, "ago": 0, "agoms": agoms})


def rows():
    return sql("SELECT time, time_estimated FROM hvac_readings WHERE sensor_node_id = %s "
               "ORDER BY time, id", (HVAC_NODE,), fetch='all')


def main():
    create_app()
    failures = []

    def check(name, cond):
        print(('PASS' if cond else 'FAIL') + ': ' + name)
        if not cond:
            failures.append(name)

    # ---------------- DB-POOL: lazy build after a failed start ----------------
    real_pool, real_cls = db.db_pool, psycopg2.pool.ThreadedConnectionPool
    attempts = {"n": 0}

    def failing_pool(*a, **k):
        attempts["n"] += 1
        raise psycopg2.OperationalError("the database system is starting up")

    try:
        db.db_pool = None
        psycopg2.pool.ThreadedConnectionPool = failing_pool
        check('DB-POOL get_conn() is None while Postgres is starting', db.get_conn() is None)
        check('DB-POOL is_available() False while Postgres is starting', db.is_available() is False)
        check('DB-POOL build was attempted (not given up at import)', attempts["n"] >= 1)
        psycopg2.pool.ThreadedConnectionPool = real_cls
        conn = db.get_conn()
        check('DB-POOL next get_conn() builds the pool once Postgres is up',
              conn is not None and db.db_pool is not None)
        if conn is not None:
            db.release_conn(conn)
        if db.db_pool is not None and db.db_pool is not real_pool:
            db.db_pool.closeall()
    finally:
        psycopg2.pool.ThreadedConnectionPool = real_cls
        db.db_pool = real_pool

    setup_fixture()
    real_send = mqtt.send_node_command
    real_get_node = models.get_sensor_node_by_mac
    real_register = models.register_unpaired_node
    real_available = db.is_available
    real_now = config.now_wib
    real_monotonic = telemetry.time.monotonic
    sent = []
    try:
        mqtt.send_node_command = lambda mac, cmd: sent.append((mac, cmd))

        # ---------------- NO-UNPAIR while the DB is unavailable ----------------
        registered = []
        models.get_sensor_node_by_mac = lambda mac: None
        models.register_unpaired_node = lambda mac: registered.append(mac)
        db.is_available = lambda: False
        devices.EVENT_DEDUPE_CACHE.clear()
        devices.handle_node_event(HVAC_MAC, json.dumps({"event": "event_request_config"}))
        check('NO-UNPAIR config request during DB outage sends nothing', sent == [])
        check('NO-UNPAIR config request during DB outage registers nothing', registered == [])
        telemetry.DEDUPE_CACHE.clear()
        telemetry.handle_telemetry(HVAC_MAC, payload())
        check('NO-UNPAIR telemetry during DB outage registers nothing', registered == [])
        models.get_sensor_node_by_mac = real_get_node
        models.register_unpaired_node = real_register
        db.is_available = real_available

        # A genuinely unknown node with the DB up is still registered + unpaired.
        devices.EVENT_DEDUPE_CACHE.clear()
        devices.handle_node_event(UNKNOWN_MAC, json.dumps({"event": "event_request_config"}))
        check('NO-UNPAIR unknown node with DB up still gets settype:unpaired',
              (UNKNOWN_MAC, 'settype:unpaired') in sent)

        # ---------------- BACKLOG re-timing ----------------
        clock = FakeClock(real_now() - timedelta(hours=3))
        config.now_wib = lambda: clock.wall
        telemetry.time.monotonic = lambda: clock.mono
        telemetry.DEDUPE_CACHE.clear()
        telemetry._LAST_ARRIVAL.clear()
        telemetry._SEEN_AFTER_CONNECT.clear()
        mqtt._last_connect.update({"mono": clock.mono - 3600, "session_present": True})

        for _ in range(6):                      # live, 10 s cadence
            clock.advance(10)
            telemetry.handle_telemetry(HVAC_MAC, payload())
        live = rows()
        last_live = live[-1][0]
        check('BACKLOG live readings stored at arrival time, not estimated',
              len(live) == 6 and not any(r[1] for r in live) and last_live == clock.wall)

        clock.advance(1800)                     # 30 min outage, then reconnect
        mqtt._last_connect.update({"mono": clock.mono, "session_present": True})
        for _ in range(180):                    # queued burst, 0.2 s apart
            clock.advance(0.2)
            telemetry.handle_telemetry(HVAC_MAC, payload())
        after = rows()[6:]
        est = [r for r in after if r[1]]
        gaps = {(b[0] - a[0]).total_seconds() for a, b in zip(est, est[1:])}
        check('BACKLOG all 180 queued readings stored (none deduped)', len(after) == 180)
        check('BACKLOG all 180 flagged time_estimated', len(est) == 180)
        check('BACKLOG first queued reading = last live + 10 s',
              bool(est) and (est[0][0] - last_live).total_seconds() == 10)
        check('BACKLOG queued readings spaced exactly 10 s', gaps == {10.0})
        check('BACKLOG no estimated time in the future', bool(est) and est[-1][0] <= clock.wall)

        clock.advance(10)                       # next live reading
        telemetry.handle_telemetry(HVAC_MAC, payload())
        last = rows()[-1]
        check('BACKLOG live reading after catch-up stamped normally',
              last[0] == clock.wall and not last[1])

        clock.advance(10)                       # node's own buffer: agoms > 0
        telemetry.handle_telemetry(HVAC_MAC, payload(agoms=4000))
        last = rows()[-1]
        check('BACKLOG agoms>0 keeps arrival - agoms and is not estimated',
              last[0] == clock.wall - timedelta(seconds=4) and not last[1])

        # Broker did NOT keep the session: nothing was queued -> no re-timing.
        before = len(rows())
        clock.advance(600)
        mqtt._last_connect.update({"mono": clock.mono, "session_present": False})
        clock.advance(0.5)
        telemetry.handle_telemetry(HVAC_MAC, payload())
        last = rows()[-1]
        check('BACKLOG no session -> first message stamped at arrival',
              len(rows()) == before + 1 and last[0] == clock.wall and not last[1])
    finally:
        mqtt.send_node_command = real_send
        models.get_sensor_node_by_mac = real_get_node
        models.register_unpaired_node = real_register
        db.is_available = real_available
        config.now_wib = real_now
        telemetry.time.monotonic = real_monotonic
        mqtt._last_connect.update({"mono": None, "session_present": False})
        telemetry.DEDUPE_CACHE.clear()
        cleanup_fixture()

    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("All outage-resilience checks passed.")


if __name__ == '__main__':
    main()
