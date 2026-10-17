"""Firmware binary hosting for OTA downloads.

The node downloads its update directly from THIS server over HTTPS — the
bytes never transit MQTT, so OTA costs nothing on the free EMQX tier. The
route is guarded by the random OTA_TOKEN (sent to the node inside the
MQTT-trigger message, which itself rides the authenticated, TLS-protected
control topic).
"""

import os

from flask import Blueprint, send_file, jsonify

from app import config

ota_bp = Blueprint("ota", __name__)


@ota_bp.route("/firmware/<token>/<path:filename>")
def serve_firmware(token, filename):
    if not config.OTA_TOKEN or token != config.OTA_TOKEN:
        return jsonify({"error": "unauthorized"}), 401
    if not config.OTA_FILENAME_RE.match(filename):
        return jsonify({"error": "invalid file"}), 400

    path = os.path.join(config.OTA_FIRMWARE_DIR, filename)
    if not os.path.isfile(path):
        return jsonify({"error": "not found"}), 404
    return send_file(path, mimetype="application/octet-stream",
                     as_attachment=False)
