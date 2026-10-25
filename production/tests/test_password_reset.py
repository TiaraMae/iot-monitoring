"""Verify the self-service password reset (backported from main).

Flow under test (SMTP mocked; broker untouched):
1. forgot_password page renders the form in BOTH states: with SMTP unset the
   page says the link goes to the server log (fallback), with SMTP set it
   stays silent about delivery.
1b. SMTP unset: POST creates the 15-min single-use token for the known user
   AND prints the reset link to the server log (lockout recovery path).
2. POST /forgot_password answers uniformly for known AND unknown emails
   (no account enumeration), and when SMTP is "configured" a reset token is
   stored for the known user only and emailed.
2b. ProxyFix: with X-Forwarded-Proto: https present (as nginx sends it), the
   emailed reset link MUST be https — an http link reached a real user and
   timed out on a network blocking port 80. Also: the reset form enforces
   minlength client-side and carries the new "Create new password" copy.
3. GET /reset_password/<token> renders for a valid token, rejects garbage.
4. POST sets the new password, clears the token (single-use), bumps
   session_epoch (other sessions die), and the user can log in with the new
   password.

Self-contained fixture; cleans up.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("IOT_DISABLE_MQTT", "1")

import bcrypt

from app import create_app, config, models

TEST_EMAIL = 'test_reset@example.com'
TEST_PASSWORD = 'Test1234'
TEST_NEW_PASSWORD = 'NewPass987'

USER_ID = None


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
    global USER_ID
    password_hash = bcrypt.hashpw(TEST_PASSWORD.encode(), bcrypt.gensalt()).decode()
    USER_ID = models.create_user(TEST_EMAIL, password_hash, 'Reset Test')


def cleanup_fixture():
    sql("DELETE FROM users WHERE id = %s", (USER_ID,))


def main():
    app = create_app()
    failures = []

    def check(name, cond):
        print(('PASS' if cond else 'FAIL') + ': ' + name)
        if not cond:
            failures.append(name)

    setup_fixture()
    saved_smtp_user = config.SMTP_USER
    saved_smtp_pass = config.SMTP_APP_PASSWORD
    try:
        with app.test_client() as client:
            # --- 1. SMTP unset: page renders WITH the form (log-link fallback).
            config.SMTP_USER = None
            config.SMTP_APP_PASSWORD = None
            r = client.get('/forgot_password')
            check('forgot page renders with SMTP unset', r.status_code == 200)
            check('form still offered with SMTP unset', b'name="email"' in r.data)

            # --- 1b. SMTP unset: token still created + link printed to the log.
            import io, contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                r1 = client.post('/forgot_password', data={'email': TEST_EMAIL})
            r2 = client.post('/forgot_password', data={'email': 'nobody@example.com'})
            check('SMTP-unset known email -> uniform redirect', r1.status_code == 302)
            check('SMTP-unset unknown email -> same uniform redirect', r2.status_code == 302)
            token_row = sql(
                "SELECT reset_token FROM users WHERE id = %s", (USER_ID,), fetch='one')
            check('SMTP-unset: token stored for known user only',
                  bool(token_row and token_row[0]))
            check('SMTP-unset: reset link printed to server log',
                  token_row[0] in buf.getvalue())
            sql("UPDATE users SET reset_token = NULL, reset_token_expires = NULL "
                "WHERE id = %s", (USER_ID,))

            # --- 2. Uniform response (no enumeration), SMTP mocked on.
            sent = []
            import app.auth as auth_mod
            auth_mod._send_reset_email = lambda to, url: sent.append((to, url))

            config.SMTP_USER = 'smtp@example.com'
            config.SMTP_APP_PASSWORD = 'app-password'
            r1 = client.post('/forgot_password', data={'email': TEST_EMAIL})
            r2 = client.post('/forgot_password', data={'email': 'nobody@example.com'})
            check('known email -> uniform redirect', r1.status_code == 302)
            check('unknown email -> same uniform redirect', r2.status_code == 302)
            check('reset email dispatched for known user only',
                  len(sent) == 1 and sent[0][0] == TEST_EMAIL)
            token_row = sql(
                "SELECT reset_token FROM users WHERE id = %s", (USER_ID,), fetch='one')
            check('token stored', bool(token_row and token_row[0]))
            token = token_row[0]
            reset_url = sent[0][1]
            check('reset url carries the token', token in reset_url)

            # --- 2b. ProxyFix: behind nginx the request arrives as plain http,
            # but with X-Forwarded-Proto present (nginx always sends it) the
            # emailed link MUST be https. An http link reached a real user and
            # ERR_CONNECTION_TIMED_OUT on a network blocking port 80.
            sent.clear()
            r3 = client.post('/forgot_password', data={'email': TEST_EMAIL},
                             environ_overrides={
                                 'HTTP_X_FORWARDED_PROTO': 'https',
                                 'HTTP_X_FORWARDED_FOR': '203.0.113.9',
                                 'HTTP_X_FORWARDED_HOST':
                                     'iotmonitor.sgu.ac.id',
                             })
            check('forwarded forgot POST -> uniform redirect', r3.status_code == 302)
            check('forwarded reset email link is https',
                  len(sent) == 1 and sent[0][1].startswith('https://'))
            # The forwarded request rotated the token — follow the fresh one.
            token_row = sql(
                "SELECT reset_token FROM users WHERE id = %s", (USER_ID,), fetch='one')
            check('token rotated after second request',
                  bool(token_row and token_row[0] and token_row[0] in sent[0][1]))
            token = token_row[0]

            # --- 3. Reset page: valid token renders, garbage rejected.
            r = client.get(f'/reset_password/{token}')
            check('valid token page renders', r.status_code == 200)
            check('reset form enforces minlength client-side',
                  b'minlength="8"' in r.data and b'maxlength="64"' in r.data)
            check('reset page copy updated',
                  b'Create new password' in r.data
                  and b'Must be 8-64' not in r.data)
            r = client.get('/reset_password/bogustoken')
            check('bogus token redirected away', r.status_code == 302)

            # --- 4. Password rules enforced.
            r = client.post(f'/reset_password/{token}',
                            data={'password': 'short', 'confirm': 'short'})
            check('too-short password rejected', r.status_code == 302)

            # --- 5. Successful reset: login with NEW password works.
            epoch_before = sql(
                "SELECT session_epoch FROM users WHERE id = %s", (USER_ID,), fetch='one')[0]
            r = client.post(f'/reset_password/{token}',
                            data={'password': TEST_NEW_PASSWORD,
                                  'confirm': TEST_NEW_PASSWORD},
                            follow_redirects=False)
            check('reset accepted', r.status_code == 302)
            row = sql("SELECT password_hash, reset_token, session_epoch FROM users "
                      "WHERE id = %s", (USER_ID,), fetch='one')
            check('token cleared (single-use)', row[1] is None)
            check('session_epoch bumped', row[2] == epoch_before + 1)
            ok_new = bcrypt.checkpw(TEST_NEW_PASSWORD.encode(), row[0].encode())
            ok_old = bcrypt.checkpw(TEST_PASSWORD.encode(), row[0].encode())
            check('new password hash stored', ok_new and not ok_old)

            r = client.post('/login', data={'email': TEST_EMAIL,
                                            'password': TEST_NEW_PASSWORD},
                            follow_redirects=False)
            check('login works with new password', r.status_code == 302
                  and '/dashboard' in (r.headers.get('Location') or ''))

        if failures:
            print(f'\n{len(failures)} check(s) FAILED')
            sys.exit(1)
        print('\nAll password-reset checks passed.')
    finally:
        config.SMTP_USER, config.SMTP_APP_PASSWORD = saved_smtp_user, saved_smtp_pass
        cleanup_fixture()


if __name__ == '__main__':
    main()
