"""Verify /api/device/<id>/export_excel: Power column + daily granularity.

Covers both appliance types:
- points mode (default): a Power (kW) column (= current x appliance voltage)
  is appended to every row for HVAC and dryer exports.
- daily mode: one row per calendar day of RUNNING readings. The temperature
  averages skip the first 5 min of each run (same rule as the on-screen HVAC
  Daily Averages table), but Avg Current / Avg Power cover the WHOLE run
  (inrush included) so they reconcile with the Energy (kWh) column, which
  integrates the full run. The idle-data filter is ignored and the filename
  carries a _daily marker. The day-A fixtures use a stepped 5 A -> 3 A inrush
  profile precisely to prove the warmup is included in current/power.

Self-contained: creates its own user/appliance/node fixtures and cleans up.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Never let tests connect to the shared MQTT broker (see AGENTS.md).
os.environ.setdefault("IOT_DISABLE_MQTT", "1")

from datetime import datetime, timedelta
from io import BytesIO
import bcrypt
from openpyxl import load_workbook

from app import create_app, models

TEST_EMAIL = 'test_export_excel@example.com'
TEST_PASSWORD = 'Test1234'
HVAC_MAC = 'AA:BB:CC:DD:EE:10'
DRY_MAC = 'AA:BB:CC:DD:EE:11'

DAY_A = datetime(2026, 10, 8, 10, 0, 0)   # fixed past dates -> stable expectations
DAY_B = datetime(2026, 10, 9, 10, 0, 0)
RUN_POINTS = 60                            # 10 s cadence -> 590 s run span
CADENCE = timedelta(seconds=10)

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


def setup_fixture():
    global USER_ID, HVAC_ID, DRY_ID
    password_hash = bcrypt.hashpw(TEST_PASSWORD.encode(), bcrypt.gensalt()).decode()
    USER_ID = models.create_user(TEST_EMAIL, password_hash, 'Export Test')

    HVAC_ID = sql("""
        INSERT INTO appliances
        (user_id, name, type, sub_type, is_inverter, location, brand, voltage,
         operational_status, cf, deductor, alert_enabled)
        VALUES (%s, 'Export Test AC', 'HVAC', 'split_residential', FALSE, 'Home', 'Generic', 220.0,
                'normal', 11.0, 0.033, TRUE)
        RETURNING id
    """, (USER_ID,), fetch='one')[0]
    DRY_ID = sql("""
        INSERT INTO appliances
        (user_id, name, type, sub_type, is_inverter, location, brand, voltage,
         operational_status, cf, deductor, alert_enabled)
        VALUES (%s, 'Export Test Dryer', 'Gas Dryer', NULL, FALSE, 'Home', 'Generic', 220.0,
                'normal', 33.0, 0.111, TRUE)
        RETURNING id
    """, (USER_ID,), fetch='one')[0]

    sql("INSERT INTO sensor_nodes (mac_address, status, appliance_id, last_seen) VALUES (%s, 'paired', %s, NOW())",
        (HVAC_MAC, HVAC_ID))
    sql("INSERT INTO sensor_nodes (mac_address, status, appliance_id, last_seen) VALUES (%s, 'paired', %s, NOW())",
        (DRY_MAC, DRY_ID))

    # HVAC: day A run starts with 6 min of 5.0 A inrush then settles at 3.0 A
    # (the stepped profile proves the daily Avg Current/Power INCLUDE the
    # warmup, unlike the temperature averages); day B is a flat 2.0 A run.
    # One idle point on day A exercises the default idle exclusion.
    hvac_rows = []
    for i in range(RUN_POINTS):
        day_a_amps = 5.0 if i < 36 else 3.0
        hvac_rows.append((HVAC_MAC, DAY_A + i * CADENCE, 26.0, 16.0, day_a_amps))
        hvac_rows.append((HVAC_MAC, DAY_B + i * CADENCE, 25.0, 15.0, 2.0))
    hvac_rows.append((HVAC_MAC, DAY_A + timedelta(hours=1), 27.0, 26.0, 0.05))
    conn = models.get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM sensor_nodes WHERE mac_address = %s", (HVAC_MAC,))
    hvac_node_id = cur.fetchone()[0]
    cur.execute("SELECT id FROM sensor_nodes WHERE mac_address = %s", (DRY_MAC,))
    dry_node_id = cur.fetchone()[0]
    cur.executemany("""
        INSERT INTO hvac_readings (sensor_node_id, time, treturn, tsupply, icompressor)
        VALUES (%s, %s, %s, %s, %s)
    """, [(hvac_node_id, r[1], r[2], r[3], r[4]) for r in hvac_rows])

    # Dryer: a 5.0 A run on day A, a 4.0 A run on day B.
    dry_rows = []
    for i in range(RUN_POINTS):
        day_a_amps = 5.0 if i < 36 else 3.0
        dry_rows.append((dry_node_id, DAY_A + i * CADENCE, 60.0, 40.0, 2.5, 1010.0, day_a_amps))
        dry_rows.append((dry_node_id, DAY_B + i * CADENCE, 55.0, 35.0, 2.0, 1009.0, 4.0))
    cur.executemany("""
        INSERT INTO dryer_readings (sensor_node_id, time, texhaust, rh_exhaust, pressure, abs_pressure, imotor)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, dry_rows)
    conn.commit()
    cur.close()
    models.release_conn(conn)


def cleanup_fixture():
    for mac in (HVAC_MAC, DRY_MAC):
        row = sql("SELECT id FROM sensor_nodes WHERE mac_address = %s", (mac,), fetch='one')
        if row:
            sql("DELETE FROM hvac_readings WHERE sensor_node_id = %s", (row[0],))
            sql("DELETE FROM dryer_readings WHERE sensor_node_id = %s", (row[0],))
            sql("DELETE FROM sensor_nodes WHERE id = %s", (row[0],))
    for app_id in (HVAC_ID, DRY_ID):
        if app_id:
            sql("DELETE FROM appliances WHERE id = %s", (app_id,))
    if USER_ID:
        sql("DELETE FROM users WHERE id = %s", (USER_ID,))


def sheet_rows(resp):
    wb = load_workbook(BytesIO(resp.data))
    ws = wb.active
    headers = [c.value for c in ws[6]]
    data = []
    for row in ws.iter_rows(min_row=7, values_only=True):
        if row[0] is None or (isinstance(row[0], str) and row[0].startswith('No data')):
            continue
        data.append(list(row))
    return headers, data


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

            # ---------- HVAC, points mode ----------
            url = f'/api/device/{HVAC_ID}/export_excel'
            resp = client.get(url)
            check('hvac points: 200', resp.status_code == 200)
            headers, data = sheet_rows(resp)
            check('hvac points: Power column after Current',
                  headers == ["Timestamp", "Return Temp (°C)", "Supply Temp (°C)",
                              "Current (A)", "Power (kW)", "Delta-T (°C)"])
            # default filtered=true drops the single idle point: 2 runs x 60
            check('hvac points: idle excluded by default', len(data) == 2 * RUN_POINTS)
            row_a = data[0]
            check('hvac points: power = I x V', abs(row_a[4] - 5.0 * 220.0) < 1e-6)
            check('hvac points: delta-t present', abs(row_a[5] - 10.0) < 1e-6)

            resp = client.get(url + '?filtered=false')
            _, data = sheet_rows(resp)
            check('hvac points: include-idle adds the idle row', len(data) == 2 * RUN_POINTS + 1)

            # ---------- HVAC, daily mode ----------
            resp = client.get(url + '?granularity=daily')
            check('hvac daily: 200', resp.status_code == 200)
            headers, data = sheet_rows(resp)
            check('hvac daily: headers',
                  headers == ["Date", "Avg Return Temp (°C)", "Avg Supply Temp (°C)",
                              "Avg Delta-T (°C)", "Avg Current (A)", "Avg Power (kW)", "Energy (kWh)"])
            check('hvac daily: one row per day, oldest first',
                  len(data) == 2 and data[0][0] == '2026-10-08' and data[1][0] == '2026-10-09')
            if len(data) == 2:
                a, b = data
                # Temps are post-warmup averages (constant fixture -> 26/16,
                # delta 10.0). Avg current MUST include the 6-min 5.0 A inrush:
                # (36 x 5 + 24 x 3) / 60 = 4.2 A -> 924 W. Excluding warmup
                # would wrongly give 3.4 A -> 748 W.
                check('hvac daily: day A averages',
                      a[1] == 26.0 and a[2] == 16.0 and a[3] == 10.0 and a[4] == 4.2 and a[5] == 924.0)
                # Energy integrates the full run (left-Riemann, prev current):
                # (36 gaps x 5 A + 23 gaps x 3 A) x 220 V x 10 s.
                check('hvac daily: day A energy', abs(a[6] - 0.1522) < 1e-9)
                check('hvac daily: day B averages',
                      b[1] == 25.0 and b[2] == 15.0 and b[4] == 2.0 and b[5] == 440.0)
                check('hvac daily: day B energy', abs(b[6] - 0.0721) < 1e-9)
            check('hvac daily: _daily filename marker',
                  '_daily' in resp.headers.get('Content-Disposition', ''))

            # ---------- Dryer, points + daily ----------
            url = f'/api/device/{DRY_ID}/export_excel'
            resp = client.get(url)
            headers, data = sheet_rows(resp)
            check('dryer points: Power column',
                  headers == ["Timestamp", "Exhaust Temp (°C)", "Exhaust RH (%)", "Gauge Pressure (hPa)",
                              "Raw Absolute Pressure (hPa)", "Current (A)", "Power (kW)"])
            check('dryer points: power = I x V', data and abs(data[0][6] - 5.0 * 220.0) < 1e-6)

            resp = client.get(url + '?granularity=daily')
            headers, data = sheet_rows(resp)
            check('dryer daily: headers',
                  headers == ["Date", "Avg Exhaust Temp (°C)", "Avg Exhaust RH (%)",
                              "Avg Gauge Pressure (hPa)", "Avg Current (A)", "Avg Power (kW)", "Energy (kWh)"])
            check('dryer daily: one row per day, oldest first',
                  len(data) == 2 and data[0][0] == '2026-10-08' and data[1][0] == '2026-10-09')
            if len(data) == 2:
                a, b = data
                check('dryer daily: day A averages',
                      a[1] == 60.0 and a[2] == 40.0 and a[3] == 2.5 and a[4] == 4.2 and a[5] == 924.0)
                check('dryer daily: day A energy', abs(a[6] - 0.1522) < 1e-9)
                check('dryer daily: day B energy', abs(b[6] - 0.1442) < 1e-9)

            if failures:
                print(f'\n{len(failures)} check(s) FAILED')
                sys.exit(1)
            print('\nAll export-excel checks passed.')
        finally:
            cleanup_fixture()


if __name__ == '__main__':
    main()
