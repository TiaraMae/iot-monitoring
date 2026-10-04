"""Flask application factory."""

import os
from datetime import timedelta

from flask import Flask, request, jsonify, redirect, url_for, render_template, make_response
from flask_login import LoginManager, login_required, current_user
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from app import config

login_manager = LoginManager()
limiter = Limiter(key_func=get_remote_address, storage_uri="memory://")


@login_manager.user_loader
def load_user(user_id):
    from app import models
    from app.auth import User

    row = models.get_user_by_id(user_id)
    if row:
        return User(row["id"], row["email"], row["name"])
    return None


@login_manager.unauthorized_handler
def unauthorized():
    if request.path.startswith("/api/"):
        return jsonify({"error": "Unauthorized"}), 401
    return redirect(url_for("auth.login"))


def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["SECRET_KEY"] = config.FLASK_SECRET_KEY
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=12)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = os.getenv("FLASK_ENV") == "production"

    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    limiter.init_app(app)

    # All times in the system are WIB; the DB uses naive local NOW(), which is
    # only correct if the host clock is set to Jakarta time (UTC+7).
    print(f"Timezone: {config.TIMEZONE} — all times are WIB; "
          f"server clock must be set to Jakarta (UTC+7).")

    from app.auth import auth_bp
    from app.devices import devices_bp
    from app.calibration import calibration_bp
    from app.analytics import analytics_bp
    from app.api import api_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(devices_bp)
    app.register_blueprint(calibration_bp)
    app.register_blueprint(analytics_bp)
    app.register_blueprint(api_bp)

    @app.route("/")
    def landing():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard"))
        return redirect(url_for("auth.login"))

    @app.route("/dashboard")
    @login_required
    def dashboard():
        from app import models

        response = make_response(render_template(
            "dashboard.html",
            user=current_user,
            appliances=models.get_appliances_for_user(current_user.id),
            unpaired_nodes=models.get_unpaired_nodes(),
            all_nodes=models.get_all_nodes_for_user(current_user.id),
        ))
        # Never let the browser serve a stale dashboard build: status-dependent
        # JS (live polling, calibration UI) must always come from the current
        # backend version.
        response.headers["Cache-Control"] = "no-store"
        return response

    # Start MQTT only when the server actually starts, and only once per
    # process — importing app.mqtt alone no longer connects (GAP-16).
    from app import mqtt
    mqtt.start_mqtt()

    # Rebuild orphaned dryer cycles from DB readings (GAP-5). Never fatal:
    # the system runs fine even if rehydration has to be skipped.
    try:
        from app import alerts
        alerts.rehydrate_dryer_cycles()
    except Exception as e:
        print(f"Dryer cycle rehydration skipped: {e}")

    return app
