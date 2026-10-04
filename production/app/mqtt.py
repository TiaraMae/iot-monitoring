"""MQTT client setup and message dispatch."""

import os

import paho.mqtt.client as mqtt

from app import config


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
    mqtt_client.publish(topic, command)
    print(f"Backend -> Node {mac}: {command}")


def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        print(f"MQTT Connected Successfully! ID: {client._client_id}")
        client.subscribe(f"{config.MQTT_TOPIC_PREFIX}/+/events")
        client.subscribe(f"{config.MQTT_TOPIC_PREFIX}/+/telemetry")
    else:
        print(f"MQTT Connection Failed with Code {rc}")


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
mqtt_client.on_message = on_message

_started = False


def start_mqtt():
    """Connect to the broker and start the network loop — once per process.

    Called from create_app(); importing this module alone must NOT connect
    (tests and side scripts would otherwise ghost-connect as duplicate
    backends and double-process every sensor message). Set IOT_DISABLE_MQTT=1
    to skip the connection entirely — every test does this so test runs never
    touch the shared broker (each ghost connection burns ~60 session-minutes
    of broker quota by leaving a 1-hour persistent session behind).
    """
    global _started
    if _started:
        return
    _started = True
    if os.getenv("IOT_DISABLE_MQTT"):
        print("MQTT disabled (IOT_DISABLE_MQTT=1) — not connecting.")
        return
    try:
        mqtt_client.connect(
            config.MQTT_HOST,
            config.MQTT_PORT,
            60,
            clean_start=False,
            properties=_mqtt_connect_props,
        )
        mqtt_client.loop_start()
    except Exception as e:
        print(f"MQTT connection error: {e}")
