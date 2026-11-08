"""Verify the PostgreSQL pool is thread-safe.

Regression test: app/db.py used psycopg2's SimpleConnectionPool, whose
getconn/putconn bookkeeping is unlocked, while the pool is shared by the
Waitress worker threads, the paho MQTT thread and background workers.
Concurrent checkouts could hand two threads the same key, lose slots and end
in "connection pool exhausted" (every model call silently returning None).

Self-contained: the stress check uses fake connections (no database needed);
never touches the shared broker (IOT_DISABLE_MQTT=1).
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("IOT_DISABLE_MQTT", "1")

import threading
from unittest import mock

import psycopg2
import psycopg2.extensions as ext
import psycopg2.pool

from app import db


class _FakeInfo:
    transaction_status = ext.TRANSACTION_STATUS_IDLE


class _FakeConn:
    """Minimal stand-in for a psycopg2 connection (what the pool touches)."""

    def __init__(self, *args, **kwargs):
        self.closed = 0
        self.info = _FakeInfo()

    def close(self):
        self.closed = 1

    def rollback(self):
        pass


def main():
    failures = []

    def check(name, cond):
        print(('PASS' if cond else 'FAIL') + ': ' + name)
        if not cond:
            failures.append(name)

    # --- 1. The app's pool must be the thread-safe class.
    src = open(db.__file__, encoding="utf-8").read()
    check("db.py builds a ThreadedConnectionPool",
          "psycopg2.pool.ThreadedConnectionPool(" in src)
    check("db.py no longer builds a SimpleConnectionPool",
          "psycopg2.pool.SimpleConnectionPool(" not in src)
    if db.db_pool is not None:
        check("live db_pool is a ThreadedConnectionPool",
              isinstance(db.db_pool, psycopg2.pool.ThreadedConnectionPool))
    else:
        print("SKIP: no local database, live db_pool not created")

    # --- 2. Stress: many threads borrowing/returning concurrently must never
    # corrupt the bookkeeping (no exceptions, nothing left checked out).
    old_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)   # force frequent thread switches
    errors = []
    try:
        with mock.patch.object(psycopg2, "connect", _FakeConn):
            pool = psycopg2.pool.ThreadedConnectionPool(1, 10)

            def worker():
                try:
                    for _ in range(500):
                        conn = pool.getconn()
                        pool.putconn(conn)
                except Exception as e:  # PoolError on corruption/exhaustion
                    errors.append(repr(e))

            threads = [threading.Thread(target=worker) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            check("8 threads x 500 checkouts raised no pool errors",
                  not errors)
            check("no connection left marked in use", not pool._used)
            check("no stale reverse mapping left", not pool._rused)
            check("pool still usable afterwards",
                  pool.getconn() is not None)
            pool.closeall()
    finally:
        sys.setswitchinterval(old_interval)
    if errors:
        print("  first error:", errors[0])

    if failures:
        print(f'\n{len(failures)} check(s) FAILED')
        sys.exit(1)
    print('\nAll DB pool thread-safety checks passed.')


if __name__ == '__main__':
    main()
