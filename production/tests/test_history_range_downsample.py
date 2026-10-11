"""Verify /api/device/<id>/latest_n History-Range downsampling.

Regression test for the "history range only shows the last ~3 hours" bug:
latest_n always capped at MAX_CHART_POINTS (1080, ~3 h at the 10 s cadence)
even when start+end were given, silently dropping everything older in the
selected window. Now a range that overflows the cap is evenly stride-sampled
across the WHOLE window; ranges that fit are returned raw, byte-identical to
the old behavior. Live mode (no start/end) is untouched.

Covers both appliance types:
- wide HVAC range (25 h, 9001 points) -> <=1080 points spanning start..end
- short HVAC range (1 h, 361 points) -> every raw row, 10 s cadence intact
- wide dryer range with a mixed idle/running profile -> Running-only filter is
  applied BEFORE the stride is computed (COUNT uses the same filters), so no
  sampled point ever violates the current filter
- empty range -> []; live mode -> capped newest slice; start-only -> ignored

Self-contained: creates its own user/appliance/node fixtures and cleans up.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Never let tests connect to the shared MQTT broker (see AGENTS.md).
os.environ.setdefault("IOT_DISABLE_MQTT", "1")

from datetime import datetime, timedelta
import bcrypt

from app import create_app, config, models

TEST_EMAIL = 'test_history_range@example.com'
TEST_PASSWORD = 'Test1234'
HVAC_MAC = 'AA:BB:CC:DD:EE:20'
DRY_MAC = 'AA:BB:CC:DD:EE:21'

# The user's original scenario: 10/10/2026 10:00:00 -> 11/10/2026 11:00:00.
RANGE_START = datetime(2026, 10, 10, 10, 0, 0)
RANGE_END = datetime(2026, 10, 11, 11, 0, 0)     # 25 h later, inclusive
CADENCE = timedelta(seconds=10)
N_POINTS = int((RANGE_END - RANGE_START).total_seconds() // 10) + 1  # 9001

SHORT_START = datetime(2026, 10, 11, 10, 0, 0)   # last hour: 361 points
SHORT_END = RANGE_END

USER_ID = None
HVAC_ID = None
DRY_ID = None


def sql(query, params=(), fetch=None):
    conn = models.get_conn()
    cur = conn.cursor()
    cur.execute(query, params)
    row = cur.fetchone() if fetch == 'one' else (cur.fetchall() if fetch == 'all' else None)
    conn.commit()
    cur.close()
    models.release_conn(conn)
    return row


def node_id(mac):
    return sql("SELECT id FROM sensor_nodes WHERE mac_address = %s", (mac,), fetch='one')[0]


def setup_fixture():
    global USER_ID, HVAC_ID, DRY_ID
    password_hash = bcrypt.hashpw(TEST_PASSWORD.encode(), bcrypt.gensalt()).decode()
    USER_ID = models.create_user(TEST_EMAIL, password_hash, 'History Range Test')

    HVAC_ID = sql("""
        INSERT INTO appliances
        (user_id, name, type, sub_type, is_inverter, location, brand, voltage,
         operational_status, cf, deductor, alert_enabled)
        VALUES (%s, 'History Test AC', 'HVAC', 'split_residential', FALSE, 'Home', 'Generic', 220.0,
                'normal', 11.0, 0.033, TRUE)
        RETURNING id
    """, (USER_ID,), fetch='one')[0]
    DRY_ID = sql("""
        INSERT INTO appliances
        (user_id, name, type, sub_type, is_inverter, location, brand, voltage,
         operational_status, cf, deductor, alert_enabled)
        VALUES (%s, 'History Test Dryer', 'Gas Dryer', NULL, FALSE, 'Home', 'Generic', 220.0,
                'normal', 33.0, 0.111, TRUE)
        RETURNING id
    """, (USER_ID,), fetch='one')[0]

    sql("INSERT INTO sensor_nodes (mac_address, status, appliance_id, last_seen) VALUES (%s, 'paired', %s, NOW())",
        (HVAC_MAC, HVAC_ID))
    sql("INSERT INTO sensor_nodes (mac_address, status, appliance_id, last_seen) VALUES (%s, 'paired', %s, NOW())",
        (DRY_MAC, DRY_ID))

    # HVAC: 25 h all-running (2.5 A). treturn carries the index so we can prove
    # the samples come from across the whole range, not one slice.
    hvac_rows = [
        (node_id(HVAC_MAC), RANGE_START + i * CADENCE, 25.0 + i * 0.001, 16.0, 2.5)
        for i in range(N_POINTS)
    ]
    # Dryer: 25 h where every 10th point is idle (0.1 A). The Running-only
    # COUNT must exclude the idle rows BEFORE the stride is derived.
    dry_rows = [
        (node_id(DRY_MAC), RANGE_START + i * CADENCE, 60.0, 40.0, 2.5, 1010.0,
         0.1 if i % 10 == 9 else 4.0)
        for i in range(N_POINTS)
    ]
    conn = models.get_conn()
    cur = conn.cursor()
    cur.executemany("""
        INSERT INTO hvac_readings (sensor_node_id, time, treturn, tsupply, icompressor)
        VALUES (%s, %s, %s, %s, %s)
    """, hvac_rows)
    cur.executemany("""
        INSERT INTO dryer_readings (sensor_node_id, time, texhaust, rh_exhaust, pressure, abs_pressure, imotor)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, dry_rows)
    conn.commit()
    cur.close()
    models.release_conn(conn)


def cleanup_fixture():
    for mac in (HVAC_MAC, DRY_MAC):
        nid = node_id(mac)
        sql("DELETE FROM hvac_readings WHERE sensor_node_id = %s", (nid,))
        sql("DELETE FROM dryer_readings WHERE sensor_node_id = %s", (nid,))
        sql("DELETE FROM sensor_nodes WHERE id = %s", (nid,))
    for app_id in (HVAC_ID, DRY_ID):
        if app_id:
            sql("DELETE FROM appliances WHERE id = %s", (app_id,))
    if USER_ID:
        sql("DELETE FROM users WHERE id = %s", (USER_ID,))


def q(start, end, **kw):
    # The dashboard always sends limit=MAX_CHART_POINTS on this endpoint.
    kw.setdefault('limit', config.MAX_CHART_POINTS)
    return f"?start={start.isoformat()}&end={end.isoformat()}" + "".join(
        f"&{k}={v}" for k, v in kw.items())


def main():
    app = create_app()
    failures = []
    with app.app_context():
        setup_fixture()
        try:
            client = app.test_client()
            client.post('/login', data={'email': TEST_EMAIL, 'password': TEST_PASSWORD})

            def check(name, cond):
                print(('PASS' if cond else 'FAIL') + ': ' + name)
                if not cond:
                    failures.append(name)

            # ---------- wide HVAC range: full-window downsampling ----------
            resp = client.get(f"/api/device/{HVAC_ID}/latest_n" + q(RANGE_START, RANGE_END))
            check('hvac wide: 200', resp.status_code == 200)
            data = resp.get_json()
            stride = -(-N_POINTS // config.MAX_CHART_POINTS)  # ceil(9001/1080) = 9
            check('hvac wide: point count <= cap', len(data) <= config.MAX_CHART_POINTS)
            check('hvac wide: sampled (not truncated)', 1 < len(data) < N_POINTS)
            # newest-first contract preserved
            check('hvac wide: newest first', data[0]['time'] == RANGE_END.isoformat())
            # (rn-1)%stride==0 with rn=1 -> the range's FIRST reading is included
            check('hvac wide: oldest sample == range start',
                  data[-1]['time'] == RANGE_START.isoformat())
            # 9001 % 9 == 0 -> the very LAST reading is included too
            check('hvac wide: newest sample == range end',
                  data[0]['time'] == RANGE_END.isoformat())
            asc = list(reversed(data))
            gaps = {(datetime.fromisoformat(asc[i + 1]['time'])
                     - datetime.fromisoformat(asc[i]['time'])).total_seconds()
                    for i in range(len(asc) - 1)}
            check('hvac wide: even stride across the whole 25 h',
                  gaps == {stride * 10})
            check('hvac wide: samples span the range (first idx value)',
                  abs(asc[0]['Treturn'] - 25.0) < 1e-6
                  and asc[-1]['Treturn'] > 25.0 + 8000 * 0.001)
            check('hvac wide: payload shape unchanged',
                  set(data[0].keys()) == {"time", "Treturn", "Tsupply", "DeltaT",
                                          "Icompressor", "Tcoil", "RHreturn", "RHsupply"})

            # ---------- short HVAC range: raw, byte-identical to old behavior ----------
            resp = client.get(f"/api/device/{HVAC_ID}/latest_n" + q(SHORT_START, SHORT_END))
            data = resp.get_json()
            n_short = int((SHORT_END - SHORT_START).total_seconds() // 10) + 1  # 361
            check('hvac short: every raw row (stride 1)', len(data) == n_short)
            asc = list(reversed(data))
            check('hvac short: exact 10 s cadence intact',
                  all((datetime.fromisoformat(asc[i + 1]['time'])
                       - datetime.fromisoformat(asc[i]['time'])).total_seconds() == 10
                      for i in range(len(asc) - 1)))
            check('hvac short: covers the full hour',
                  asc[0]['time'] == SHORT_START.isoformat()
                  and asc[-1]['time'] == SHORT_END.isoformat())

            # ---------- running-only with downsampling (dryer, mixed profile) ----------
            # Default filtered=true = Running-only (idle rows excluded BEFORE
            # the COUNT that derives the stride).
            resp = client.get(f"/api/device/{DRY_ID}/latest_n"
                              + q(RANGE_START, RANGE_END, filtered='false'))
            data_all = resp.get_json()
            resp = client.get(f"/api/device/{DRY_ID}/latest_n"
                              + q(RANGE_START, RANGE_END, filtered='true'))
            data_run = resp.get_json()
            check('dryer wide: 200', resp.status_code == 200)
            check('dryer wide: both sampled', 1 < len(data_run) <= config.MAX_CHART_POINTS
                  and 1 < len(data_all) <= config.MAX_CHART_POINTS)
            check('dryer wide: Running-only excludes idle BEFORE striding',
                  all(r['Imotor'] >= config.RUNNING_CURRENT_THRESHOLD for r in data_run))
            check('dryer wide: running set covers the whole window',
                  data_run[-1]['time'] == RANGE_START.isoformat()
                  and RANGE_END - timedelta(seconds=8 * 10)
                  <= datetime.fromisoformat(data_run[0]['time']) <= RANGE_END)
            # (the last sampled point can sit up to one stride before the
            # range end when the filtered count isn't stride-aligned)
            check('dryer wide: all-data set covers the whole window',
                  data_all[-1]['time'] == RANGE_START.isoformat()
                  and data_all[0]['time'] == RANGE_END.isoformat())
            check('dryer wide: payload shape unchanged',
                  set(data_run[0].keys()) == {"time", "Texhaust", "RHexhaust",
                                              "Pressure", "AbsPressure", "Imotor"})
            resp = client.get(f"/api/device/{DRY_ID}/latest_n"
                              + q(SHORT_START, SHORT_END, filtered='true'))
            d_short = resp.get_json()
            n_idle_short = sum(1 for i in range(8640, 9001) if i % 10 == 9)  # 36
            check('dryer short filtered: raw rows, no idle leaks',
                  len(d_short) == (9000 - 8640 + 1) - n_idle_short
                  and all(r['Imotor'] >= config.RUNNING_CURRENT_THRESHOLD for r in d_short))

            # ---------- edge cases: empty range, live mode, start-only ----------
            resp = client.get(f"/api/device/{HVAC_ID}/latest_n"
                              + q(datetime(2020, 1, 1), datetime(2020, 1, 2)))
            check('empty range: []', resp.status_code == 200 and resp.get_json() == [])

            resp = client.get(f"/api/device/{HVAC_ID}/latest_n")
            data_live = resp.get_json()
            check('live mode: newest 120 (default limit), unchanged',
                  len(data_live) == 120
                  and data_live[0]['time'] == RANGE_END.isoformat())

            resp = client.get(f"/api/device/{HVAC_ID}/latest_n"
                              + f"?start={RANGE_START.isoformat()}")
            data_start_only = resp.get_json()
            check('start without end: ignored, live behavior',
                  len(data_start_only) == 120)

            if failures:
                print(f'\n{len(failures)} check(s) FAILED')
                sys.exit(1)
            print('\nAll history-range downsampling checks passed.')
        finally:
            cleanup_fixture()


if __name__ == '__main__':
    main()
