"""Authentication blueprint: login/signup, session epoch revocation,
change-password, and email self-service password reset."""

import bcrypt
import secrets
import smtplib
from email.message import EmailMessage
from datetime import timedelta

from flask import (Blueprint, request, render_template, redirect, url_for,
                   flash, session, jsonify)
from flask_login import (UserMixin, login_user, logout_user, login_required,
                         current_user)

from app import config, limiter
from app import models

auth_bp = Blueprint("auth", __name__)


class User(UserMixin):
    def __init__(self, id, email, name, is_admin=False, session_epoch=0):
        self.id = id
        self.email = email
        self.name = name
        self.is_admin = bool(is_admin)
        self.session_epoch = session_epoch


def _password_ok(password):
    """NIST 800-63B-aligned: min 8 chars, max 64."""
    return 8 <= len(password) <= 64


def _build_user(row):
    return User(row["id"], row["email"], row["name"],
                row.get("is_admin"), row.get("session_epoch", 0))


def _epoch_matches(row):
    """Session revocation: the session records the epoch at login; a password
    change/reset bumps the DB epoch, which invalidates the session."""
    return session.get("_epoch") == row.get("session_epoch", 0)


@auth_bp.route("/signup", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def signup():
    if request.method == "GET":
        return render_template("signup.html")

    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    confirm = request.form.get("confirm", "")

    if not name or not email or not password:
        flash("All fields are required", "error")
        return redirect(url_for("auth.signup"))
    if password != confirm:
        flash("Passwords do not match", "error")
        return redirect(url_for("auth.signup"))
    if not _password_ok(password):
        flash("Password must be 8-64 characters", "error")
        return redirect(url_for("auth.signup"))

    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    try:
        user_id = models.create_user(email, hashed, name)
        if user_id:
            row = models.get_user_by_id(user_id)
            login_user(_build_user(row))
            session["_epoch"] = row.get("session_epoch", 0)
            return redirect(url_for("dashboard"))
        flash("Could not create account", "error")
    except Exception as e:
        flash(f"Error: {e}", "error")
    return redirect(url_for("auth.signup"))


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("30 per minute")
def login():
    if request.method == "GET":
        return render_template("login.html")

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    user = models.get_user_by_email(email)
    if user and bcrypt.checkpw(
        password.encode("utf-8"), user["password_hash"].encode("utf-8")
    ):
        login_user(_build_user(user))
        session["_epoch"] = user.get("session_epoch", 0)
        return redirect(url_for("dashboard"))

    flash("Invalid credentials", "error")
    return redirect(url_for("auth.login"))


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    session.pop("_epoch", None)
    return redirect(url_for("auth.login"))


@auth_bp.route("/api/me")
@login_required
def api_me():
    return jsonify({
        "id": current_user.id,
        "email": current_user.email,
        "name": current_user.name,
        "is_admin": current_user.is_admin,
    })


@auth_bp.route("/api/user/change_password", methods=["POST"])
@login_required
@limiter.limit("10 per minute")
def api_change_password():
    data = request.get_json() or {}
    current = data.get("current", "")
    new = data.get("new", "")
    if not _password_ok(new):
        return jsonify({"error": "Password must be 8-64 characters"}), 400

    row = models.get_user_by_email(current_user.email)
    if not row or not bcrypt.checkpw(
        current.encode("utf-8"), row["password_hash"].encode("utf-8")
    ):
        return jsonify({"error": "Current password is incorrect"}), 403

    hashed = bcrypt.hashpw(new.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    if models.set_user_password(current_user.id, hashed):
        # Keep THIS session alive: adopt the new epoch.
        fresh = models.get_user_by_id(current_user.id)
        session["_epoch"] = fresh.get("session_epoch", 0)
        return jsonify({"success": True})
    return jsonify({"error": "Failed to update password"}), 500


# ---------------------------------------------------------------------------
# Email self-service password reset
# ---------------------------------------------------------------------------

def _send_reset_email(to_email, reset_url):
    msg = EmailMessage()
    msg["Subject"] = "IoT Monitoring - password reset"
    msg["From"] = config.SMTP_USER
    msg["To"] = to_email
    msg.set_content(
        "A password reset was requested for your IoT Monitoring account.\n\n"
        f"Open this link within {config.PASSWORD_RESET_TOKEN_TTL_MINUTES} minutes:\n"
        f"{reset_url}\n\n"
        "If you did not request this, ignore this email; your password is unchanged.\n"
    )
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as smtp:
        smtp.login(config.SMTP_USER, config.SMTP_APP_PASSWORD)
        smtp.send_message(msg)


@auth_bp.route("/forgot_password", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def forgot_password():
    if request.method == "GET":
        return render_template("forgot_password.html",
                               smtp_configured=bool(config.SMTP_USER))

    email = request.form.get("email", "").strip().lower()
    # Uniform response — never reveal whether the account exists.
    flash("If that email is registered, a reset link is on its way.", "info")

    user = models.get_user_by_email(email)
    if user:
        token = secrets.token_urlsafe(32)
        expires = config.now_wib() + timedelta(
            minutes=config.PASSWORD_RESET_TOKEN_TTL_MINUTES)
        models.set_reset_token(user["id"], token, expires)
        reset_url = url_for("auth.reset_password", token=token,
                            _external=True)
        if config.SMTP_USER and config.SMTP_APP_PASSWORD:
            try:
                _send_reset_email(email, reset_url)
            except Exception as e:
                print(f"Reset email to {email} failed: {e}")
                flash("Could not send the email right now. Please try again "
                      "or contact your administrator.", "error")
        else:
            # SMTP not configured: deliver the link via the server log instead
            # of email (fallback ported from main). Token is still 15-min,
            # single-use; an operator on the host reads the log and hands the
            # link to the user. This is the lockout recovery path that saved
            # us nothing on 2026-10-20 — now it exists.
            print(f"PASSWORD RESET LINK ({email}, SMTP not configured, "
                  f"valid {config.PASSWORD_RESET_TOKEN_TTL_MINUTES} min): "
                  f"{reset_url}")
    return redirect(url_for("auth.forgot_password"))


@auth_bp.route("/reset_password/<token>", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def reset_password(token):
    user = models.get_user_by_reset_token(token)
    if not user:
        flash("This reset link is invalid or has expired.", "error")
        return redirect(url_for("auth.forgot_password"))

    if request.method == "GET":
        return render_template("reset_password.html", token=token)

    password = request.form.get("password", "")
    confirm = request.form.get("confirm", "")
    if password != confirm:
        flash("Passwords do not match", "error")
        return redirect(url_for("auth.reset_password", token=token))
    if not _password_ok(password):
        flash("Password must be 8-64 characters", "error")
        return redirect(url_for("auth.reset_password", token=token))

    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    if models.set_user_password(user["id"], hashed):
        flash("Password updated. You can log in now.", "success")
        return redirect(url_for("auth.login"))
    flash("Failed to update password. Please try again.", "error")
    return redirect(url_for("auth.reset_password", token=token))
