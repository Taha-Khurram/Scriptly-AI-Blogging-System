"""Flask extension singletons and their initialisation.

Extensions are constructed unbound at import time and attached to the app in
:func:`init_extensions`, which is the standard factory pattern: it keeps a
route module free to ``from app.core.extensions import limiter`` for a
decorator without importing the app and creating a cycle.

Two extensions carry real weight here.

**Rate limiting.** The public site exposes endpoints that each spend money on a
Gemini call per request -- semantic search, comment moderation -- with no
authentication in front of them. A trivial loop drains the API quota. This is
not hypothetical for this project: it already had to change models once because
of 429s under single-developer load. Limits are per-IP, backed by Redis so they
hold across workers.

**CSRF.** Authentication is cookie-based. ``SameSite=Lax`` blocks the common
cross-site form POST, but that is one control, browser-dependent, and it does
not cover same-site subdomain attacks. Flask-WTF adds a token check to every
state-changing request; the token is injected into every page and read back
from the ``X-CSRFToken`` header by the frontend fetch layer.
"""
from __future__ import annotations

import logging

from flask import jsonify, request, session
from flask_compress import Compress
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFError, CSRFProtect, generate_csrf

from app.core.errors import RateLimitError

logger = logging.getLogger(__name__)

compress = Compress()
csrf = CSRFProtect()

# Exempt from CSRF, with a reason for each. Nothing goes on this list because
# adding the header was inconvenient.
CSRF_EXEMPT_ENDPOINTS = frozenset((
    # Login: the caller has no session yet, so there is no token to present.
    # The protection here is the Firebase ID token itself -- a signed bearer
    # credential that an attacker's page cannot obtain or forge.
    'auth_bp.verify_token',
    # Public visitor-facing writes on the published site. These are
    # unauthenticated by design (anyone may comment or subscribe), so a CSRF
    # token protects nothing -- there is no ambient authority to abuse. They
    # are rate-limited per IP instead.
    'site_bp.site_contact_submit',
    'site_bp.site_subscribe',
    'site_bp.site_semantic_search',
    'site_bp.site_submit_comment',
    # Public one-click unsubscribe, reached from a link in an email. There is
    # no session and therefore no ambient authority for a CSRF token to
    # protect; it is rate-limited instead.
    'newsletter.unsubscribe',
))


def _rate_limit_key():
    """Rate-limit bucket: the session for signed-in callers, else the IP.

    Keying an authenticated request by user id rather than address means one
    user on a shared NAT cannot exhaust everyone else's budget, and a single
    account cannot multiply its allowance by rotating addresses.
    """
    user_id = session.get('user_id')
    if user_id:
        return f'user:{user_id}'
    return f'ip:{get_remote_address()}'


limiter = Limiter(
    key_func=_rate_limit_key,
    # `default_limits` is deliberately NOT passed here. flask-limiter only
    # falls back to RATELIMIT_DEFAULT from config when the constructor did not
    # supply a value (_extension.py: `if not self.limit_manager._default_limits
    # and conf_limits`), so passing a literal here made the documented
    # RATELIMIT_DEFAULT environment variable silently dead -- the limit was
    # always the hardcoded one no matter what was configured. The default is
    # applied in _init_limiter from config instead, which is also what makes it
    # tunable per environment.
    headers_enabled=True,
    strategy='fixed-window',
    swallow_errors=True,   # a storage outage must not 500 every request
)


def init_extensions(app):
    """Bind every extension to ``app``. Called once from the factory."""
    _init_compression(app)
    _init_csrf(app)
    _init_limiter(app)


def _init_compression(app):
    """gzip/brotli for text responses.

    Explicit mimetype list: the default includes everything, and compressing
    an already-compressed image or font burns CPU for no size win -- which
    matters on a shared-CPU instance.
    """
    app.config.setdefault('COMPRESS_MIMETYPES', [
        'text/html', 'text/css', 'text/xml', 'text/plain',
        'application/json', 'application/javascript',
        'application/rss+xml', 'image/svg+xml',
    ])
    app.config.setdefault('COMPRESS_LEVEL', 6)
    app.config.setdefault('COMPRESS_MIN_SIZE', 512)
    compress.init_app(app)


def _init_csrf(app):
    """Enable CSRF protection and expose the token to templates and JS."""
    if not app.config.get('WTF_CSRF_ENABLED', True):
        app.logger.warning('CSRF protection is DISABLED by configuration')
        return

    csrf.init_app(app)

    @app.after_request
    def _set_csrf_cookie(response):
        """Publish the token in a readable cookie for the fetch layer.

        Not HttpOnly on purpose -- JavaScript has to read it to set the
        ``X-CSRFToken`` header. That is safe: the value is only useful when
        sent *back* with the session cookie, and same-origin policy stops
        another origin's script from reading it. Secure/SameSite still apply.
        """
        try:
            response.set_cookie(
                'csrf_token',
                generate_csrf(),
                secure=app.config.get('SESSION_COOKIE_SECURE', True),
                httponly=False,
                samesite=app.config.get('SESSION_COOKIE_SAMESITE', 'Lax'),
                max_age=int(app.config['PERMANENT_SESSION_LIFETIME'].total_seconds()),
            )
        except Exception:
            # generate_csrf needs a session; a request that never establishes
            # one (a health probe) simply does not get the cookie.
            pass
        return response

    @app.context_processor
    def _inject_csrf_token():
        """``{{ csrf_token() }}`` for forms and the meta tag in base.html."""
        return {'csrf_token': generate_csrf}

    @app.errorhandler(CSRFError)
    def _handle_csrf_error(error):
        logger.warning(
            'CSRF validation failed',
            extra={'path': request.path, 'reason': error.description},
        )
        if request.path.startswith('/api/') or request.is_json:
            return jsonify({
                'success': False,
                'error': 'Your session security token expired. Reload the page and try again.',
                'code': 'csrf_invalid',
            }), 400
        from app.core.errors import _render_error_page
        return _render_error_page(400, 'Security token expired. Please reload the page.')

    # The exemptions themselves are applied by apply_csrf_exemptions(), which
    # the factory calls *after* the blueprints exist. They cannot be applied
    # here: resolving an endpoint name needs app.view_functions to be
    # populated, and extensions are initialised before blueprints register.


def apply_csrf_exemptions(app):
    """Exempt the endpoints in :data:`CSRF_EXEMPT_ENDPOINTS` from CSRF.

    Must run after blueprint registration.

    ``CSRFProtect.exempt`` accepts a view function or a *dotted import path*
    (``module.function``); given any other string it stores the value verbatim
    and silently matches nothing. It was previously called with Flask
    **endpoint** names (``auth_bp.verify_token``), which are a different
    namespace -- ``blueprint_name.function_name`` -- so every exemption on the
    list was a no-op and login itself was CSRF-blocked: the browser has no
    token to present before a session exists, so ``/api/auth/verify`` answered
    400 and the sign-in never completed.

    Endpoint names are kept as the declaration format because they are what the
    rest of the app uses and they survive a module move. They are resolved to
    view functions here, which is also what makes a stale entry loud: an
    endpoint that no longer exists is logged as an error rather than quietly
    protecting nothing.
    """
    for endpoint in CSRF_EXEMPT_ENDPOINTS:
        view = app.view_functions.get(endpoint)
        if view is None:
            logger.error(
                'CSRF exemption names an unknown endpoint: %s. Either the '
                'endpoint was renamed or its blueprint is not registered.',
                endpoint,
            )
            continue
        csrf.exempt(view)

    app.logger.debug(
        'CSRF exemptions applied: %d of %d resolved',
        sum(1 for e in CSRF_EXEMPT_ENDPOINTS if e in app.view_functions),
        len(CSRF_EXEMPT_ENDPOINTS),
    )


def _init_limiter(app):
    """Bind the limiter, using Redis when available for cross-worker counts."""
    if not app.config.get('RATELIMIT_ENABLED', True):
        app.logger.warning(
            'Rate limiting is DISABLED. Public AI endpoints are unprotected.'
        )
        return

    redis_url = app.config.get('REDIS_URL')
    if redis_url:
        app.config['RATELIMIT_STORAGE_URI'] = redis_url
        app.logger.info('Rate limit storage: Redis')
    else:
        # SQLite, not memory://. Importing the module registers the `sqlite`
        # scheme with `limits` via Storage's metaclass, which is what lets the
        # URI below resolve. In-memory counters are per *process*, so with N
        # gunicorn workers they permit N times the configured rate and reset on
        # every reload -- a limit that does not hold is worse than none,
        # because it reads as protection that is not there.
        from app.core import ratelimit_store  # noqa: F401  (registers 'sqlite')

        app.config['RATELIMIT_STORAGE_URI'] = 'sqlite://'
        app.logger.info(
            'Rate limit storage: SQLite (shared across this instance\'s '
            'workers and threads)'
        )

    # Applied to every route that does not declare its own limit, so a new
    # endpoint is protected by default rather than by remembering a decorator.
    # Read from config here, which is the only place flask-limiter will honour
    # it -- see the note on the Limiter constructor above.
    app.config['RATELIMIT_DEFAULT'] = app.config.get(
        'RATELIMIT_DEFAULT', '1200 per hour'
    )
    limiter.init_app(app)
    app.logger.info(
        'Rate limiting enabled. Default: %s', app.config['RATELIMIT_DEFAULT']
    )

    @app.errorhandler(429)
    def _handle_rate_limit(error):
        # Route it through AppError so the response shape matches every other
        # error the frontend has to handle.
        retry_after = getattr(error, 'retry_after', None)
        logger.warning(
            'Rate limit exceeded',
            extra={'path': request.path, 'key': _rate_limit_key(),
                   'limit': str(getattr(error, 'description', ''))},
        )
        from app.core.errors import _wants_json, _render_error_page
        if _wants_json():
            response = jsonify(RateLimitError().to_dict())
            response.status_code = 429
            response.headers['Retry-After'] = str(retry_after or 30)
            return response
        return _render_error_page(429, RateLimitError.message)


def exempt_from_limits(view):
    """Mark a view as never rate-limited (health probes, static-ish routes)."""
    return limiter.exempt(view)
