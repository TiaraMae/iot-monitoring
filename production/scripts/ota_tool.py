"""ota_tool.py — lean fleet OTA: push firmware to nodes, watch the campaign.

Run on the mini-PC (or anywhere with the backend .env and DB reachable):

    python scripts/ota_tool.py push node_20261016-1.bin --mac AA:BB:...   (canary)
    python scripts/ota_tool.py push node_20261016-1.bin --all
    python scripts/ota_tool.py status node_20261016-1.bin
    python scripts/ota_tool.py retry node_20261016-1.bin

Mechanics (see OTA.md): the node downloads the .bin directly from this
server over HTTPS; MQTT only carries the tiny trigger (URL + SHA-256) on the
node's control topic. Status is derived from sensor_events rows written by
the backend (ota_* events + checkin_fw version rows) — no schema changes.
"""
import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import paho.mqtt.client as mqtt

from app import config, models


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_url(filename):
    if not config.OTA_TOKEN or not config.OTA_PUBLIC_BASE:
        sys.exit("OTA_TOKEN / OTA_PUBLIC_BASE not set in .env — refusing.")
    return f"{config.OTA_PUBLIC_BASE}/firmware/{config.OTA_TOKEN}/{filename}"


def version_of(filename):
    # node_<version>.bin -> <version>
    name = filename[:-4] if filename.endswith(".bin") else filename
    return name[5:] if name.startswith("node_") else name


def publish_command(mac, command):
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"OTA-Tool-{int(time.time())}",
        protocol=mqtt.MQTTv5,
    )
    client.username_pw_set(config.MQTT_USER, config.MQTT_PASS)
    client.tls_set()
    try:
        client.connect(config.MQTT_HOST, config.MQTT_PORT, 60)
        client.loop_start()
        topic = f"{config.MQTT_TOPIC_PREFIX}/{mac}/control"
        info = client.publish(topic, command, qos=1)
        info.wait_for_publish(timeout=15)
        print(f"  -> {mac}: {command.split(':')[0]}... (mid={info.mid})")
        return True
    except Exception as e:
        print(f"  -> {mac}: FAILED to dispatch: {e}")
        return False
    finally:
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:
            pass


def find_node(mac):
    """Any registered node by MAC (paired or not)."""
    conn = models.get_conn()
    if not conn:
        return None
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT mac_address, status FROM sensor_nodes "
            "WHERE UPPER(mac_address) = UPPER(%s)", (mac,))
        r = cur.fetchone()
        return {"mac": r[0], "status": r[1]} if r else None
    finally:
        cur.close()
        models.release_conn(conn)


def get_paired_nodes():
    conn = models.get_conn()
    if not conn:
        sys.exit("No DB connection.")
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT sn.mac_address, a.name, a.operational_status, sn.last_seen
            FROM sensor_nodes sn
            LEFT JOIN appliances a ON a.id = sn.appliance_id
            WHERE sn.status = 'paired'
            ORDER BY a.name NULLS LAST, sn.mac_address
        """)
        return [
            {"mac": r[0], "name": r[1] or "(unassigned)", "status": r[2], "last_seen": r[3]}
            for r in cur.fetchall()
        ]
    finally:
        cur.close()
        models.release_conn(conn)


def get_latest_events():
    """Latest ota_* event and latest checkin_fw version per MAC."""
    conn = models.get_conn()
    if not conn:
        return {}, {}
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT DISTINCT ON (sensor_node_mac) sensor_node_mac, event_type, detail, timestamp
            FROM sensor_events
            WHERE event_type LIKE 'ota!_%' ESCAPE '!'
            ORDER BY sensor_node_mac, timestamp DESC
        """)
        ota = {r[0]: {"type": r[1], "detail": r[2], "at": r[3]} for r in cur.fetchall()}
        cur.execute("""
            SELECT DISTINCT ON (sensor_node_mac) sensor_node_mac, detail, timestamp
            FROM sensor_events
            WHERE event_type = 'checkin_fw'
            ORDER BY sensor_node_mac, timestamp DESC
        """)
        fw = {r[0]: {"fw": r[1], "at": r[2]} for r in cur.fetchall()}
        return ota, fw
    finally:
        cur.close()
        models.release_conn(conn)


def is_online(node):
    if not node["last_seen"]:
        return False
    calib_needed = (node["status"] or "") in ("offset_calibration_needed", "calibrating")
    timeout = (config.NEED_CALIBRATION_OFFLINE_TIMEOUT_SECONDS if calib_needed
               else config.OFFLINE_TIMEOUT_SECONDS)
    age = (datetime.now() - node["last_seen"]).total_seconds()
    return age <= timeout


def classify(node, ota_evt, fw_evt, want_version):
    mac = node["mac"]
    fw = (fw_evt or {}).get("fw")
    if fw == want_version:
        return "updated", fw
    if not is_online(node):
        return "offline", fw
    evt = (ota_evt or {}).get("type")
    # ota_confirmed is the self-test guard's success report — it arrives
    # AFTER ota_done and is the strongest "updated" signal we have.
    if evt in ("ota_done", "ota_confirmed"):
        return "updated", fw
    if evt == "ota_rollback":
        return "failed(rollback)", fw
    if evt == "ota_fail":
        code = ""
        try:
            code = json.loads((ota_evt or {}).get("detail") or "{}").get("code", "")
        except Exception:
            pass
        return f"failed({code})", fw
    if evt:
        return "pending", fw
    return "not triggered", fw


def cmd_push(args):
    path = args.file
    filename = os.path.basename(path)
    if not os.path.isfile(path):
        sys.exit(f"File not found: {path}")
    if not config.OTA_FILENAME_RE.match(filename):
        sys.exit(f"File name not allowed by OTA_FILENAME_RE: {filename}")
    url = build_url(filename)
    digest = sha256_of(path)
    command = f"ota:{url}:{digest}"

    nodes = get_paired_nodes()
    if args.mac:
        nodes = [n for n in nodes if n["mac"].upper() == args.mac.upper()]
        if not nodes:
            sys.exit(f"No paired node with MAC {args.mac}")
    elif not args.all:
        sys.exit("Specify --mac <MAC> (canary first!) or --all.")

    print(f"Triggering OTA of {filename} (sha256={digest[:12]}...) "
          f"on {len(nodes)} node(s).")
    ok = 0
    for n in nodes:
        if publish_command(n["mac"], command):
            ok += 1
    print(f"Dispatched {ok}/{len(nodes)}. Watch with: "
          f"python scripts/ota_tool.py status {filename}")


def cmd_status(args):
    filename = os.path.basename(args.file)
    want_version = version_of(filename)
    nodes = get_paired_nodes()
    ota_map, fw_map = get_latest_events()

    print(f"Campaign target: {filename} (version {want_version})\n")
    print(f"{'APPLIANCE':<22}{'MAC':<20}{'STATE':<18}{'FW VERSION':<16}{'LAST OTA EVENT'}")
    counts = {}
    for n in nodes:
        state, fw = classify(n, ota_map.get(n["mac"]), fw_map.get(n["mac"]), want_version)
        counts[state.split("(")[0]] = counts.get(state.split("(")[0], 0) + 1
        evt = ota_map.get(n["mac"])
        evt_txt = f"{evt['type']} {evt['at']:%m-%d %H:%M}" if evt else "-"
        print(f"{n['name'][:21]:<22}{n['mac']:<20}{state:<18}{(fw or '-')[:15]:<16}{evt_txt}")
    print()
    updated = counts.get("updated", 0)
    summary = ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
    print(f"{updated}/{len(nodes)} updated" + (f" ({summary})" if summary else ""))
    if updated < len(nodes):
        print(f"Re-send to stragglers: python scripts/ota_tool.py retry {filename}")


def cmd_retry(args):
    filename = os.path.basename(args.file)
    want_version = version_of(filename)
    path = args.file if os.path.isfile(args.file) else os.path.join(config.OTA_FIRMWARE_DIR, filename)
    if not os.path.isfile(path):
        sys.exit(f"File not found: {path} (or in {config.OTA_FIRMWARE_DIR})")
    url = build_url(filename)
    command = f"ota:{url}:{sha256_of(path)}"

    nodes = get_paired_nodes()
    ota_map, fw_map = get_latest_events()
    targets = []
    for n in nodes:
        state, fw = classify(n, ota_map.get(n["mac"]), fw_map.get(n["mac"]), want_version)
        if state.split("(")[0] in ("failed", "pending", "not triggered") and is_online(n):
            targets.append(n)
    if not targets:
        print("Nothing to retry: every node is updated or offline.")
        return
    print(f"Retrying {len(targets)} node(s).")
    ok = 0
    for n in targets:
        if publish_command(n["mac"], command):
            ok += 1
    print(f"Dispatched {ok}/{len(targets)}.")


def cmd_cmd(args):
    node = find_node(args.mac)
    if not node:
        sys.exit(f"No registered node with MAC {args.mac}")
    head = args.command.split(':', 1)[0]
    if head in ("setwifi", "setmqtt") and args.command.count(":") < 2:
        sys.exit(f"{head} needs <value>:<value> — and SSID/password may not contain ':'")
    print(f"Sending to {node['mac']} ({node['status']}):")
    ok = publish_command(node["mac"], args.command)
    sys.exit(0 if ok else 1)


def main():
    parser = argparse.ArgumentParser(description="Fleet OTA tool (see OTA.md)")
    sub = parser.add_subparsers(dest="mode", required=True)

    p_push = sub.add_parser("push", help="Trigger OTA on one (--mac, canary) or all nodes")
    p_push.add_argument("file", help=".bin file (local path)")
    p_push.add_argument("--mac", help="Single node MAC (canary)")
    p_push.add_argument("--all", action="store_true", help="All paired nodes")
    p_push.set_defaults(func=cmd_push)

    p_status = sub.add_parser("status", help="Campaign status table")
    p_status.add_argument("file", help=".bin file name (version source)")
    p_status.set_defaults(func=cmd_status)

    p_retry = sub.add_parser("retry", help="Re-send only to failed/pending online nodes")
    p_retry.add_argument("file", help=".bin file name or path")
    p_retry.set_defaults(func=cmd_retry)

    p_cmd = sub.add_parser("cmd", help="Send a raw control command to one node "
                                         "(setwifi:<ssid>:<pass>, setmqtt:<user>:<pass>, ...)")
    p_cmd.add_argument("mac", help="Node MAC")
    p_cmd.add_argument("command", help="Control payload (no 'ota:' prefix)")
    p_cmd.set_defaults(func=cmd_cmd)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
