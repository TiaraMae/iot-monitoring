"""Content-Security-Policy for every response.

nginx already sends HSTS / X-Frame-Options / nosniff / Referrer-Policy; the
CSP is set here so it travels with the app (also in local dev).

'unsafe-inline' is required for scripts and styles because the templates use
inline <script> blocks and onclick= handlers. Scripts, styles and fonts
come only from this site (Chart.js is self-hosted in app/static/vendor since
2026-11-17), framing by other sites is forbidden, and <base> and form
targets are pinned to this origin.
"""

CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline'",
    "style-src 'self' 'unsafe-inline'",
    "font-src 'self'",
    "img-src 'self' data:",
    "connect-src 'self'",
    "frame-ancestors 'self'",
    "base-uri 'self'",
    "form-action 'self'",
])


def init_app(app):
    @app.after_request
    def _set_csp(response):
        response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        return response
