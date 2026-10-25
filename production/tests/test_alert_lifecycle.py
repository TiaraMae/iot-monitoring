"""Alert lifecycle (acknowledge vs resolve) + async Discord dispatch.

1. /api/alerts/<id>/acknowledge sets the `acknowledged` flag ONLY (the alert
   stays unresolved / in the active list); /api/alerts/<id>/resolve sets
   `resolved_at`. (Before 2026-10-25 both endpoints just set resolved_at and
   the acknowledged column was dead.)
2. Ownership: another user's alert id -> 404 on both endpoints.
3. /api/device/<id>/alerts exposes the acknowledged flag.
4. Discord dispatch is ASYNCHRONOUS: send_discord_alert must NOT perform the
   HTTP POST on the calling thread — it enqueues for the background worker
   (a slow/dead webhook must never stall MQTT ingestion). The worker owns the
   only requests.post. (Before 2026-10-25 the POST was synchronous on the
   MQTT callback thread and the queue helper was wired to re-enqueue forever.)

Self-contained fixture; cleans up.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("IOT_DISABLE_MQTT", "1")

import bcrypt

from app import create_app, models
import app.alerts as alerts_mod

TEST_EMAIL = 'test_alertlifecycle@example.com'
TEST_PASSWORD = 'Test1234'
OTHER_EMAIL = 'test_alertlifecycle_other@example.com'
WEBHOOK = 'https://discord.com/api/webhooks/123456/abcdef'

USER_ID = None
OTHER_ID = None
APP_ID = None
ALERT_ID = None


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
    global USER_ID, OTHER_ID, APP_ID, ALERT_ID
    h = bcrypt.hashpw(TEST_PASSWORD.encode(), bcrypt.gensalt()).decode()
    USER_ID = models.create_user(TEST_EMAIL, h, 'Alert Lifecycle')
    h2 = bcrypt.hashpw(TEST_PASSWORD.encode(), bcrypt.gensalt()).decode()
    OTHER_ID = models.create_user(OTHER_EMAIL, h2, 'Alert Other')
    APP_ID = sql("""
        INSERT INTO appliances
        (user_id, name, type, sub_type, is_inverter, location, brand,
         operational_status, cf, deductor, alert_enabled)
        VALUES (%s, 'Lifecycle AC', 'HVAC', 'split_residential', FALSE, 'Home', 'Generic',
                'normal', 11.0, 0.033, TRUE)
        RETURNING id
    """, (USER_ID,), fetch='one')[0]
    ALERT_ID = sql("""
        INSERT INTO alerts (appliance_id, alert_type, message, severity)
        VALUES (%s, 'fault_hvac_low_delta_t', 'lifecycle test', 'warning')
        RETURNING id
    """, (APP_ID,), fetch='one')[0]


def cleanup_fixture():
    if ALERT_ID:
        sql("DELETE FROM alerts WHERE id = %s", (ALERT_ID,))
    if APP_ID:
        sql("DELETE FROM appliances WHERE id = %s", (APP_ID,))
    for uid in (USER_ID, OTHER_ID):
        if uid:
            sql("DELETE FROM users WHERE id = %s", (uid,))


def main():
    app = create_app()
    failures = []

    def check(name, cond):
        print(('PASS' if cond else 'FAIL') + ': ' + name)
        if not cond:
            failures.append(name)

    setup_fixture()
    try:
        with app.test_client() as client:
            client.post('/login', data={'email': TEST_EMAIL,
                                        'password': TEST_PASSWORD})

            # --- 1. Acknowledge only flags; resolve closes.
            r = client.post(f'/api/alerts/{ALERT_ID}/acknowledge')
            check('acknowledge -> success', r.status_code == 200)
            row = sql("SELECT acknowledged, resolved_at FROM alerts WHERE id = %s",
                      (ALERT_ID,), fetch='one')
            check('acknowledge sets flag only (still unresolved)',
                  row[0] is True and row[1] is None)

            r = client.post(f'/api/alerts/{ALERT_ID}/resolve')
            check('resolve -> success', r.status_code == 200)
            row = sql("SELECT acknowledged, resolved_at FROM alerts WHERE id = %s",
                      (ALERT_ID,), fetch='one')
            check('resolve sets resolved_at (flag preserved)',
                  row[0] is True and row[1] is not None)

            # --- 2. Alerts API exposes acknowledged.
            r = client.get(f'/api/device/{APP_ID}/alerts')
            alerts = r.get_json().get('alerts', [])
            mine = [a for a in alerts if a['id'] == ALERT_ID]
            check('alerts API exposes acknowledged flag',
                  len(mine) == 1 and mine[0]['acknowledged'] is True
                  and mine[0]['resolved_at'] is not None)

            # --- 3. Ownership: the other user cannot touch this alert.
            client.get('/logout')
            client.post('/login', data={'email': OTHER_EMAIL,
                                        'password': TEST_PASSWORD})
            r = client.post(f'/api/alerts/{ALERT_ID}/acknowledge')
            check('other user acknowledge -> 404', r.status_code == 404)
            r = client.post(f'/api/alerts/{ALERT_ID}/resolve')
            check('other user resolve -> 404', r.status_code == 404)
            client.get('/logout')
            client.post('/login', data={'email': TEST_EMAIL,
                                        'password': TEST_PASSWORD})

            # --- 4. Discord dispatch is asynchronous.
            sql("UPDATE users SET discord_webhook_url = %s WHERE id = %s",
                (WEBHOOK, USER_ID))
            posted = []
            real_post = alerts_mod.requests.post
            real_ensure = alerts_mod._ensure_discord_worker
            alerts_mod.requests.post = lambda *a, **k: posted.append((a, k))
            # Keep the real worker thread out of this assertion — it would
            # dequeue + POST asynchronously and race the check below. The
            # contract under test is: send_discord_alert itself never POSTs.
            alerts_mod._ensure_discord_worker = lambda: None
            try:
                # Drain any stale queue items so the assertion is exact.
                while True:
                    alerts_mod._discord_queue.get_nowait()
                    alerts_mod._discord_queue.task_done()
            except Exception:
                pass
            try:
                alerts_mod.send_discord_alert(
                    APP_ID, 'fault_hvac_low_delta_t', 'async test',
                    value=1.0, threshold=8.0, severity='warning')
                check('no synchronous HTTP POST from send_discord_alert',
                      len(posted) == 0)
                try:
                    item = alerts_mod._discord_queue.get_nowait()
                    alerts_mod._discord_queue.task_done()
                    check('embed enqueued for the background worker',
                          item[0] == WEBHOOK
                          and 'async test' in item[1].get('description', ''))
                except Exception:
                    check('embed enqueued for the background worker', False)
            finally:
                alerts_mod.requests.post = real_post
                alerts_mod._ensure_discord_worker = real_ensure
            sql("UPDATE users SET discord_webhook_url = NULL WHERE id = %s",
                (USER_ID,))

        if failures:
            print(f'\n{len(failures)} check(s) FAILED')
            sys.exit(1)
        print('\nAll alert-lifecycle checks passed.')
    finally:
        cleanup_fixture()


if __name__ == '__main__':
    main()
