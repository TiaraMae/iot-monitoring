"""API blueprint aggregator."""

from flask import Blueprint

api_bp = Blueprint("api", __name__)

# Import route modules so their blueprints are registered with the application.
from app import auth, devices, calibration, analytics
