"""MQTT client setup and message dispatch."""

import random

import paho.mqtt.client as mqtt

from app import config


CLIENT_ID = f"ProductionBackend_{random.randint(10000, 99999)}"

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
