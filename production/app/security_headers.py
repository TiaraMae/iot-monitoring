"""Content-Security-Policy for every response.

nginx already sends HSTS / X-Frame-Options / nosniff / Referrer-Policy; the
CSP is set here so it travels with the app (also in local dev).

'unsafe-inline' is required for scripts and styles because the templates use
inline <script> blocks and onclick= handlers. The policy still restricts
script/style/font sources to this site + the two CDNs actually used
(jsDelivr for Chart.js, Google Fonts), forbids framing by other sites, and
pins <base> and form targets to this origin.
"""

CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
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
