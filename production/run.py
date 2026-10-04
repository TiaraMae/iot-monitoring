"""Entry point for local development and standalone production runs.

FLASK_ENV=production  -> serve with Waitress (production WSGI server)
otherwise             -> Flask development server
The Windows-service install (deployment/windows/setup-nssm-service.ps1) runs
Waitress directly via `python -m waitress run:app`.
"""

import os

from app.singleinstance import acquire_lock

# Deny a second backend BEFORE anything connects to MQTT or the DB.
_release_lock = acquire_lock()

from app import create_app

app = create_app()

if __name__ == "__main__":
    try:
        if os.getenv("FLASK_ENV") == "production":
            from waitress import serve
            print("Serving with Waitress on 0.0.0.0:5000 (production mode)")
            serve(app, host="0.0.0.0", port=5000, threads=4)
        else:
            app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
    finally:
        _release_lock()
