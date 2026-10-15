"""MQTT client setup and message dispatch."""

import os
import threading
import time

import paho.mqtt.client as mqtt

from app import config


# Delay between background reconnect attempts when the initial connect fails
# at process start (broker/DNS/campus network not reachable yet). Without
# this, a single failed connect() left the backend deaf for its whole
# lifetime — every node showed "offline" while the web UI kept working.
CONNECT_RETRY_SECONDS = 30


# Stable ID: restarts RESUME this one durable session instead of minting a new
# client (+ a lingering session) per process start; if two backends ever
# connected at once, the broker's same-client-ID takeover kicks the older one
# (MQTT-level split-brain protection on top of the local single-instance lock).
CLIENT_ID = "ProductionBackend"

mqtt_client = mqtt.Client(
    mqtt.CallbackAPIVersion.VERSION2,
    client_id=CLIENT_ID,
    protocol=mqtt.MQTTv5,
)
mqtt_client.username_pw_set(config.MQTT_USER, config.MQTT_PASS)
mqtt_client.tls_set()


try:
    from paho.mqtt.properties import Properties
    from paho.mqtt.packettypes import PacketTypes

    _mqtt_connect_props = Properties(PacketTypes.CONNECT)
    _mqtt_connect_props.SessionExpiryInterval = 3600
except Exception:
    _mqtt_connect_props = None


def send_node_command(mac, command):
    if not mqtt_client.is_connected():
        print(f"Cannot send command {command} to {mac}, MQTT disconnected.")
        return
    topic = f"{config.MQTT_TOPIC_PREFIX}/{mac}/control"
    # QoS 1 so control delivery survives brief node/broker hiccups
    # (delivery QoS = min(publish, subscribe); firmware subscribes at 1).
    mqtt_client.publish(topic, command, qos=1)
    print(f"Backend -> Node {mac}: {command}")


def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        print(f"MQTT Connected Successfully! ID: {client._client_id}")
        # QoS 1 so the broker can queue for the durable backend session
        # (clean_start=False, expiry 3600) while the backend is down —
        # delivery QoS is min(publish, subscribe), so nodes must also
        # publish at QoS 1 for this to matter.
        client.subscribe(f"{config.MQTT_TOPIC_PREFIX}/+/events", qos=1)
        client.subscribe(f"{config.MQTT_TOPIC_PREFIX}/+/telemetry", qos=1)
    else:
        print(f"MQTT Connection Failed with Code {rc}")


def on_disconnect(client, userdata, disconnect_flags, rc, properties=None):
    # rc != 0 means the drop was NOT a clean client disconnect — the broker
    # connection was lost. paho's network loop reconnects automatically; this
    # log makes the silent-deaf-window visible in service.out.log.
    if rc != 0:
        print(f"MQTT connection lost (rc={rc}); auto-reconnecting...")


def on_message(client, userdata, msg):
    try:
        topic = msg.topic
        payload = msg.payload.decode()
        parts = topic.split("/")
        if len(parts) < 5:
            return
        mac = parts[3]
        channel = parts[4]

        # Local imports avoid circular dependencies at module load time.
        if channel == "events":
            from app import devices

            devices.handle_node_event(mac, payload)
        elif channel == "telemetry":
            from app import telemetry

            telemetry.handle_telemetry(mac, payload)
    except Exception as e:
        print(f"MQTT dispatch error: {e}")


mqtt_client.on_connect = on_connect
mqtt_client.on_disconnect = on_disconnect
mqtt_client.on_message = on_message

_started = False


def _connect_once():
    mqtt_client.connect(
        config.MQTT_HOST,
        config.MQTT_PORT,
        60,
        clean_start=False,
        properties=_mqtt_connect_props,
    )
    mqtt_client.loop_start()


def _connect_retry_loop():
    """Background: keep trying the initial connect until the broker is
    reachable, then hand over to paho's network loop (which handles all
    later drops with its own backoff)."""
    while not mqtt_client.is_connected():
        try:
            _connect_once()
            print("MQTT connected (background retry).")
            return
        except Exception as e:
            print(f"MQTT connection error: {e} — retrying in "
                  f"{CONNECT_RETRY_SECONDS}s.")
        time.sleep(CONNECT_RETRY_SECONDS)


def start_mqtt():
    """Connect to the broker and start the network loop — once per process.

    Called from create_app(); importing this module alone must NOT connect
    (tests and side scripts would otherwise ghost-connect as duplicate
    backends and double-process every sensor message). Set IOT_DISABLE_MQTT=1
    to skip the connection entirely — every test does this so test runs never
    touch the shared broker (each ghost connection burns ~60 session-minutes
    of broker quota by leaving a 1-hour persistent session behind).

    A failed initial connect is NOT fatal: DNS/campus-network outages at boot
    used to leave the backend deaf for its whole lifetime (nodes offline, web
    fine), so the connect is retried in a background thread until it succeeds.
    """
    global _started
    if _started:
        return
    _started = True
    if os.getenv("IOT_DISABLE_MQTT"):
        print("MQTT disabled (IOT_DISABLE_MQTT=1) — not connecting.")
        return
    try:
        _connect_once()
    except Exception as e:
        print(f"MQTT connection error at startup: {e} — will retry every "
              f"{CONNECT_RETRY_SECONDS}s in the background.")
        threading.Thread(target=_connect_retry_loop, daemon=True).start()
