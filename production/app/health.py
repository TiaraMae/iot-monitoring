"""Health check for external uptime monitoring (e.g. UptimeRobot).

`GET /` only proves that nginx and Flask routing answer (it redirects even
when the database is dead — the 44 h "zombie backend" of 2026-10-18..20 passed
that check the whole time). /healthz checks the two things the system actually
needs: the database answers a query, and the backend is connected to the MQTT
broker (no broker = no telemetry, no alerts).

Unauthenticated on purpose (monitors cannot log in). It returns only booleans,
never error text, hostnames or counts.
"""

from flask import Blueprint, jsonify

from app import db

health_bp = Blueprint("health", __name__)


def _db_ok():
    conn = db.get_conn()  # runs its own SELECT 1 liveness probe
    if not conn:
        return False
    db.release_conn(conn)
    return True


def _mqtt_ok():
    try:
        from app import mqtt
        return bool(mqtt.mqtt_client.is_connected())
    except Exception:
        return False


@health_bp.route("/healthz")
def healthz():
    db_ok = _db_ok()
    mqtt_ok = _mqtt_ok()
    healthy = db_ok and mqtt_ok
    body = {"status": "ok" if healthy else "degraded", "db": db_ok, "mqtt": mqtt_ok}
    resp = jsonify(body)
    resp.status_code = 200 if healthy else 503
    resp.headers["Cache-Control"] = "no-store"
    return resp
