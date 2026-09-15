"""Authentication blueprint."""

import bcrypt
from flask import Blueprint, request, render_template, redirect, url_for, flash
from flask_login import UserMixin, login_user, logout_user, login_required, current_user

from app import limiter
from app import models

auth_bp = Blueprint("auth", __name__)


class User(UserMixin):
    def __init__(self, id, email, name):
        self.id = id
        self.email = email
        self.name = name


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
    if len(password) < 6:
        flash("Password must be at least 6 characters", "error")
        return redirect(url_for("auth.signup"))

    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    try:
        user_id = models.create_user(email, hashed, name)
        if user_id:
            login_user(User(user_id, email, name))
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
        login_user(User(user["id"], user["email"], user["name"]))
        return redirect(url_for("dashboard"))

    flash("Invalid credentials", "error")
    return redirect(url_for("auth.login"))


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
