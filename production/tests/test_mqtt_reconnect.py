"""Verify start_mqtt() survives an unreachable broker at process start.

Regression test: connect() used to be attempted exactly once, so a DNS or
campus-network outage at boot left the backend deaf for its whole lifetime
(every node "offline" while the web UI kept working). Now a failed initial
connect spawns a background retry loop that keeps trying until the broker is
reachable, and connection loss is logged via on_disconnect.

Self-contained: mocks the paho client; never touches the shared broker
(IOT_DISABLE_MQTT stays set for every other assertion path).
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("IOT_DISABLE_MQTT", "1")

from unittest import mock

import app.mqtt as mq


def main():
    failures = []

    def check(name, cond):
        print(('PASS' if cond else 'FAIL') + ': ' + name)
        if not cond:
            failures.append(name)

    # --- 1. Initial connect failure -> background retry -> eventual success.
    attempts = {"n": 0}
    def flaky_connect(*args, **kwargs):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise OSError("getaddrinfo failed")

    captured = {}
    def fake_thread(target=None, daemon=None, **kwargs):
        captured["run"] = target
        captured["daemon"] = daemon
        return mock.Mock()

    mq._started = False
    env = os.environ.copy()
    env.pop("IOT_DISABLE_MQTT", None)
    with mock.patch.dict(os.environ, env, clear=True), \
         mock.patch.object(mq.mqtt_client, "connect", side_effect=flaky_connect), \
         mock.patch.object(mq.mqtt_client, "loop_start") as loop_start, \
         mock.patch.object(mq.mqtt_client, "is_connected", return_value=False), \
         mock.patch.object(mq.time, "sleep", lambda s: None), \
         mock.patch.object(mq.threading, "Thread", side_effect=fake_thread):
        mq.start_mqtt()
        check("background retry thread spawned on failed initial connect",
              "run" in captured)
        check("retry thread is a daemon", captured.get("daemon") is True)
        captured["run"]()  # run the retry loop synchronously
        check("retry loop kept trying until connect succeeded",
              attempts["n"] == 3)
        check("network loop started after retry success",
              loop_start.call_count == 1)

    # --- 2. Happy path: initial connect works, no retry thread.
    mq._started = False
    with mock.patch.dict(os.environ, env, clear=True), \
         mock.patch.object(mq.mqtt_client, "connect") as connect, \
         mock.patch.object(mq.mqtt_client, "loop_start"), \
         mock.patch.object(mq.threading, "Thread") as thread:
        mq.start_mqtt()
        check("initial connect attempted once on happy path",
              connect.call_count == 1)
        check("no retry thread on happy path", not thread.called)

    # --- 3. start_mqtt still no-ops entirely when MQTT is disabled.
    mq._started = False
    with mock.patch.dict(os.environ, {"IOT_DISABLE_MQTT": "1"}, clear=True), \
         mock.patch.object(mq.mqtt_client, "connect") as connect:
        mq.start_mqtt()
        check("IOT_DISABLE_MQTT=1 never connects", not connect.called)

    # --- 4. on_disconnect logs unexpected drops (rc != 0) only.
    with mock.patch("builtins.print") as pr:
        mq.on_disconnect(mq.mqtt_client, None, None, 7)
        check("on_disconnect logs unexpected drop (rc=7)",
              any("connection lost" in str(c) for c in pr.call_args_list))
        pr.reset_mock()
        mq.on_disconnect(mq.mqtt_client, None, None, 0)
        check("on_disconnect silent on clean close (rc=0)",
              not pr.called)

    if failures:
        print(f'\n{len(failures)} check(s) FAILED')
        sys.exit(1)
    print('\nAll MQTT reconnect checks passed.')


if __name__ == '__main__':
    main()
