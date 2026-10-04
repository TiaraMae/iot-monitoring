"""Allow running the package with `python -m app`.

FLASK_ENV=production serves with Waitress; otherwise the Flask dev server.
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
