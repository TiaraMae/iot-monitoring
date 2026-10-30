"""Verify the Monthly Energy Consumption report's Avg Delta-T column.

Covers both consumers of the report:
- GET /api/energy_summary?month=YYYY-MM JSON (dashboard appliance list):
  HVAC appliances carry "avg_delta_t" (average signed running delta-T for the
  month, skipping the first AVG_DELTA_T_WARMUP_MINUTES of each compressor run —
  same rule as the device card's Avg ΔT); dryers get null.
- GET /api/energy_summary/export: 4th column "Avg Delta-T (°C)"; numeric for
  HVAC rows, "--" for dryer rows.

The HVAC fixture uses a stepped delta-T profile (20.0 °C for the first 5 min
of the run = exactly the warmup window, then 10.0 °C) so the test proves the
warmup skip: the expected monthly average is exactly 10.0, not 15.0.

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

TEST_EMAIL = 'test_energy_summary@example.com'
TEST_PASSWORD = 'Test1234'
HVAC_MAC = 'AA:BB:CC:DD:EE:20'
DRY_MAC = 'AA:BB:CC:DD:EE:21'

DAY_A = datetime(2026, 10, 15, 10, 0, 0)   # fixed past dates -> stable expectations
DAY_B = datetime(2026, 10, 16, 10, 0, 0)
RUN_POINTS = 60                            # 10 s cadence -> 590 s run span
CADENCE = timedelta(seconds=10)
MONTH = '2026-10'

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
    USER_ID = models.create_user(TEST_EMAIL, password_hash, 'Energy Summary Test')

    # created_at pinned to the first of the fixture month so the endpoint's
    # max(month_start, created_at) window includes the fixture readings.
    HVAC_ID = sql("""
        INSERT INTO appliances
        (user_id, name, type, sub_type, is_inverter, location, brand, voltage,
         operational_status, cf, deductor, alert_enabled, created_at)
        VALUES (%s, 'Energy Test AC', 'HVAC', 'split_residential', FALSE, 'Home', 'Generic', 220.0,
                'normal', 11.0, 0.033, TRUE, %s)
        RETURNING id
    """, (USER_ID, datetime(2026, 10, 1)), fetch='one')[0]
    DRY_ID = sql("""
        INSERT INTO appliances
        (user_id, name, type, sub_type, is_inverter, location, brand, voltage,
         operational_status, cf, deductor, alert_enabled, created_at)
        VALUES (%s, 'Energy Test Dryer', 'Gas Dryer', NULL, FALSE, 'Home', 'Generic', 220.0,
                'normal', 33.0, 0.111, TRUE, %s)
        RETURNING id
    """, (USER_ID, datetime(2026, 10, 1)), fetch='one')[0]

    sql("INSERT INTO sensor_nodes (mac_address, status, appliance_id, last_seen) VALUES (%s, 'paired', %s, NOW())",
        (HVAC_MAC, HVAC_ID))
    sql("INSERT INTO sensor_nodes (mac_address, status, appliance_id, last_seen) VALUES (%s, 'paired', %s, NOW())",
        (DRY_MAC, DRY_ID))

    # HVAC day A: first 5 min (30 pts = the whole warmup window) delta-T =
    # 30-10 = 20.0 (must be skipped), remaining 30 pts delta-T = 25-15 = 10.0.
    # Day B is a flat 10.0. Expected monthly average: exactly 10.0.
    hvac_rows = []
    for i in range(RUN_POINTS):
        day_a = (30.0, 10.0) if i < 30 else (25.0, 15.0)
        hvac_rows.append((DAY_A + i * CADENCE, day_a[0], day_a[1], 3.0))
        hvac_rows.append((DAY_B + i * CADENCE, 25.0, 15.0, 2.0))
    hvac_node_id = sql("SELECT id FROM sensor_nodes WHERE mac_address = %s", (HVAC_MAC,), fetch='one')[0]
    conn = models.get_conn()
    cur = conn.cursor()
    cur.executemany("""
        INSERT INTO hvac_readings (sensor_node_id, time, treturn, tsupply, icompressor)
        VALUES (%s, %s, %s, %s, %s)
    """, [(hvac_node_id, r[0], r[1], r[2], r[3]) for r in hvac_rows])

    # Dryer: a 3.0 A run on day A and a 2.0 A run on day B.
    dry_node_id = sql("SELECT id FROM sensor_nodes WHERE mac_address = %s", (DRY_MAC,), fetch='one')[0]
    dry_rows = []
    for i in range(RUN_POINTS):
        dry_rows.append((dry_node_id, DAY_A + i * CADENCE, 60.0, 40.0, 2.5, 1010.0, 3.0))
        dry_rows.append((dry_node_id, DAY_B + i * CADENCE, 55.0, 35.0, 2.0, 1009.0, 2.0))
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


def main():
    app = create_app()
    failures = []
    with app.app_context():
        try:
            setup_fixture()
            client = app.test_client()
            client.post('/login', data={'email': TEST_EMAIL, 'password': TEST_PASSWORD})

            def check(name, cond):
                print(('PASS' if cond else 'FAIL') + ': ' + name)
                if not cond:
                    failures.append(name)

            # ---------- JSON summary ----------
            resp = client.get(f'/api/energy_summary?month={MONTH}')
            check('json: 200', resp.status_code == 200)
            data = resp.get_json()
            apps = {a['id']: a for a in data.get('appliances', [])}
            check('json: both appliances present', HVAC_ID in apps and DRY_ID in apps)
            hvac, dry = apps.get(HVAC_ID, {}), apps.get(DRY_ID, {})
            # Warmup-skipped monthly average: all post-warmup points are 10.0,
            # so the stepped 20.0 °C warmup must NOT leak into the result.
            check('json: hvac avg_delta_t skips warmup (== 10.0)',
                  hvac.get('avg_delta_t') == 10.0)
            check('json: hvac energy present', hvac.get('energy_kwh') is not None)
            check('json: dryer avg_delta_t is null', dry.get('avg_delta_t') is None)

            # ---------- Excel export ----------
            resp = client.get(f'/api/energy_summary/export?month={MONTH}')
            check('export: 200', resp.status_code == 200)
            wb = load_workbook(BytesIO(resp.data))
            ws = wb.active
            headers = [c.value for c in ws[5]][:4]
            check('export: 4-column header',
                  headers == ["Appliance Type", "Appliance Name",
                              "Avg Delta-T (°C)", "Energy Consumption (kWh)"])
            rows = [list(r) for r in ws.iter_rows(min_row=6, values_only=True) if r[0]]
            by_name = {r[1]: r for r in rows if r[1]}
            hvac_row = by_name.get('Energy Test AC', [])
            dry_row = by_name.get('Energy Test Dryer', [])
            check('export: hvac delta-t cell matches JSON', len(hvac_row) >= 4 and hvac_row[2] == 10.0)
            check('export: hvac energy cell present', len(hvac_row) >= 4 and hvac_row[3] is not None)
            check('export: dryer delta-t cell is "--"', len(dry_row) >= 4 and dry_row[2] == "--")
            total_rows = [r for r in rows if r[0] == 'Total']
            check('export: total row keeps kWh total',
                  len(total_rows) == 1 and total_rows[0][3] is not None)

            if failures:
                print(f'\n{len(failures)} check(s) FAILED')
                sys.exit(1)
            print('\nAll energy-summary checks passed.')
        finally:
            cleanup_fixture()


if __name__ == '__main__':
    main()
