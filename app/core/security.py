"""Authentication, authorisation and transport-level hardening.

Access control used to be an inline ``if session.get('user_role') != 'ADMIN'``
repeated across thirteen route modules, plus eight byte-identical copies of an
``admin_required`` decorator. That is the worst possible shape for a security
control: fixing a flaw means finding every copy, and a new route module quietly
starts with no protection at all.

Everything here is the single definition of those rules:

* :func:`login_required` / :func:`admin_required` / :func:`api_login_required`
  -- the only access-control decorators in the codebase;
* :func:`current_user` -- one typed accessor for session identity, so routes
  stop reaching into ``session[...]`` by hand;
* :func:`register_security_headers` -- CSP, HSTS, frame and MIME protections;
* :func:`enforce_session_timeout` -- the sliding inactivity window.

The 404-instead-of-403 choice for admin pages is deliberate and preserved from
the original code: it stops a signed-in non-admin from mapping which admin
routes exist. API calls still get a true 403 because the frontend needs to
distinguish "not allowed" from "gone".
"""
from __future__ import annotations

import hmac
import logging
from datetime import datetime, timezone
from functools import wraps

from flask import current_app, g, redirect, request, session, url_for

from app.core.errors import AuthenticationError, AuthorizationError, SessionExpiredError

logger = logging.getLogger(__name__)

ROLE_ADMIN = 'ADMIN'
ROLE_USER = 'USER'

# Endpoints reachable without a session. Kept as endpoint names rather than
# paths so a url_prefix change cannot silently open or close one.
PUBLIC_ENDPOINTS = frozenset((
    'static',
    'health.healthz',
    'health.livez',
    'health.readyz',
    'auth_bp.login',
    'auth_bp.signup',
    'auth_bp.verify_token',
    'auth_bp.logout',
    'auth_bp.forgot_password',
    'auth_bp.check_email',
))

# Blueprints whose every endpoint is public (the visitor-facing site).
PUBLIC_BLUEPRINTS = frozenset(('site_bp',))


class CurrentUser:
    """Read-only view of the signed-in identity for the current request.

    A small object rather than raw ``session`` access so a route reads
    ``current_user().is_admin`` instead of comparing a magic string, and so the
    session key names live in exactly one place.
    """

    __slots__ = ('id', 'name', 'email', 'role', 'profile_image', 'authenticated')

    def __init__(self, *, id=None, name=None, email=None, role=None,
                 profile_image=None, authenticated=False):
        self.id = id
        self.name = name
        self.email = email
        self.role = role
        self.profile_image = profile_image
        self.authenticated = authenticated

    @property
    def is_admin(self):
        return self.authenticated and self.role == ROLE_ADMIN

    @property
    def is_anonymous(self):
        return not self.authenticated

    def __bool__(self):
        return self.authenticated

    def __repr__(self):
        if not self.authenticated:
            return '<CurrentUser anonymous>'
        return f'<CurrentUser {self.id} role={self.role}>'


def current_user():
    """The signed-in user for this request, cached on ``g``.

    Cached because access-control decorators, templates and the logging hooks
    all ask for it within one request and the session cookie only needs
    decoding once.
    """
    cached = getattr(g, '_current_user', None)
    if cached is not None:
        return cached

    if not session.get('logged_in'):
        user = CurrentUser()
    else:
        user = CurrentUser(
            id=session.get('user_id'),
            name=session.get('user_name'),
            email=session.get('user_email'),
            role=session.get('user_role', ROLE_USER),
            profile_image=session.get('profile_image', ''),
            authenticated=True,
        )
    g._current_user = user
    return user


def _expects_json():
    """Whether this caller should get a JSON error rather than a redirect."""
    return (
        request.path.startswith('/api/')
        or request.is_json
        or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    )


def login_required(f):
    """Require any authenticated session.

    Browsers are redirected to the login page with ``?next=`` so the user
    lands back where they were; API callers get a 401 with a JSON body, which
    the frontend fetch layer turns into a session-expiry prompt.
    """
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user().authenticated:
            if _expects_json():
                raise AuthenticationError()
            return redirect(url_for('auth_bp.login', next=request.full_path))
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    """Require an authenticated session with the ADMIN role.

    A signed-in non-admin hitting an admin *page* gets a 404, not a 403, so the
    set of admin routes is not enumerable from a normal account. API callers
    get a real 403 because the frontend must tell "forbidden" apart from
    "does not exist".
    """
    @wraps(f)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user.authenticated:
            if _expects_json():
                raise AuthenticationError()
            return redirect(url_for('auth_bp.login', next=request.full_path))

        if not user.is_admin:
            logger.warning(
                'Non-admin attempted admin route',
                extra={'user_id': user.id, 'role': user.role,
                       'path': request.path, 'endpoint': request.endpoint},
            )
            if _expects_json():
                raise AuthorizationError()
            # Indistinguishable from a genuinely missing page.
            from app.core.errors import NotFoundError
            raise NotFoundError()
        return f(*args, **kwargs)
    return wrapper


def api_login_required(f):
    """Require a session, always answering with JSON.

    For endpoints that are only ever called by ``fetch()``: a redirect there
    produces an HTML login page inside a JSON parse, which surfaces to the user
    as an unexplained failure.
    """
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user().authenticated:
            raise AuthenticationError()
        return f(*args, **kwargs)
    return wrapper


def api_admin_required(f):
    """Require the ADMIN role, always answering with JSON."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user.authenticated:
            raise AuthenticationError()
        if not user.is_admin:
            logger.warning(
                'Non-admin attempted admin API',
                extra={'user_id': user.id, 'role': user.role, 'path': request.path},
            )
            raise AuthorizationError()
        return f(*args, **kwargs)
    return wrapper


def roles_required(*roles):
    """Require the session role to be one of ``roles``.

    The general form of :func:`admin_required`, for when a third role appears
    and ``ADMIN``/``USER`` is no longer the whole vocabulary.
    """
    allowed = frozenset(roles)

    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            user = current_user()
            if not user.authenticated:
                if _expects_json():
                    raise AuthenticationError()
                return redirect(url_for('auth_bp.login', next=request.full_path))
            if user.role not in allowed:
                raise AuthorizationError()
            return f(*args, **kwargs)
        return wrapper
    return decorator


def owns_resource_or_admin(resource_owner_id):
    """Raise unless the caller owns ``resource_owner_id`` or is an admin.

    Called at the top of any route that mutates a specific record. The check
    has to happen *before* the mutation -- the original gallery delete removed
    the document first and compared owners afterwards, which meant any
    signed-in account could destroy another account's metadata and only then be
    told "403".
    """
    user = current_user()
    if not user.authenticated:
        raise AuthenticationError()
    if user.is_admin:
        return
    # Constant-time compare: resource ids are not secrets, but this costs
    # nothing and keeps the habit consistent across the codebase.
    if not resource_owner_id or not hmac.compare_digest(
        str(resource_owner_id), str(user.id or '')
    ):
        logger.warning(
            'Ownership check failed',
            extra={'user_id': user.id, 'owner_id': resource_owner_id,
                   'path': request.path},
        )
        raise AuthorizationError()


def enforce_session_timeout(app):
    """Sliding inactivity timeout, applied before every non-public request.

    ``PERMANENT_SESSION_LIFETIME`` alone expires the *cookie*; this also
    invalidates a session whose last request is older than the window even if
    the cookie is still technically valid, and gives API callers a structured
    ``session_expired`` response instead of an HTML redirect.
    """
    @app.before_request
    def _check_session_timeout():
        endpoint = request.endpoint
        if not endpoint:
            return None
        if endpoint.split('.', 1)[0] in PUBLIC_BLUEPRINTS:
            return None
        if not session.get('logged_in'):
            return None

        if endpoint in PUBLIC_ENDPOINTS:
            # Exempt from being *blocked*, but not from being evaluated. The
            # login page redirects a signed-in visitor to the dashboard, so a
            # session that is stale-but-still-marked-logged-in would bounce the
            # user login -> dashboard -> (expire) -> login. Clearing it here
            # means the login page they asked for is the page they get.
            if _is_stale(session.get('last_activity'),
                         current_app.config['PERMANENT_SESSION_LIFETIME']):
                session.clear()
            return None

        timeout = current_app.config['PERMANENT_SESSION_LIFETIME']
        raw_last_activity = session.get('last_activity')
        now = datetime.now(timezone.utc)

        # An absent timestamp is the first request of a new session and is
        # fine. A *present but unusable* one is not: it means the session
        # cannot be shown to be fresh, and the only safe reading of "cannot
        # prove" is "expired". Trusting it instead would make a session with a
        # corrupted or tampered timestamp immortal.
        last_activity = None
        if raw_last_activity is not None:
            last_activity = _parse_last_activity(raw_last_activity)
            if last_activity is None:
                return _expire_session('unparsable last_activity')
            if now - last_activity > timeout:
                return _expire_session('inactivity timeout')

        # Re-stamp, but not on every request. Writing this marks the session
        # modified, and with server-side sessions a modified session is a write
        # to the store -- so an unthrottled re-stamp means a disk write per
        # request purely to move a timestamp by milliseconds. The same interval
        # the session interface uses for its own touch, so the two agree.
        #
        # The idle timeout is not weakened by the throttle: the store's own
        # ``expires_at`` is the authority and slides on the same schedule, and
        # the slack is at most SESSION_TOUCH_SECONDS against a window measured
        # in hours. What this value still buys is the structured
        # ``session_expired`` response above, which an anonymous session
        # arriving from the store cannot produce.
        touch_seconds = current_app.config.get('SESSION_TOUCH_SECONDS', 60)
        if (
            last_activity is None
            or (now - last_activity).total_seconds() >= touch_seconds
        ):
            session['last_activity'] = now.isoformat()
        return None


def _is_stale(raw_last_activity, timeout):
    """Whether a session's last activity is outside ``timeout``.

    An absent timestamp is not stale -- that is a session's first request. An
    unparsable one is, because freshness cannot be established.
    """
    if raw_last_activity is None:
        return False
    last_activity = _parse_last_activity(raw_last_activity)
    if last_activity is None:
        return True
    return datetime.now(timezone.utc) - last_activity > timeout


def _parse_last_activity(value):
    """The session's last-activity time as an aware datetime, or ``None``."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except (ValueError, TypeError):
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _expire_session(reason):
    """Clear the session and raise, so the caller gets a structured response."""
    user_id = session.get('user_id')
    session.clear()
    logger.info('Session expired', extra={'user_id': user_id, 'reason': reason})
    raise SessionExpiredError()


# Content-Security-Policy. Built as a dict so a directive can be adjusted in
# one obvious place. 'unsafe-inline' for styles and scripts is required by the
# current templates (inline <script> blocks and style attributes throughout);
# removing it needs a nonce pass over ~40 templates, so it is called out here
# rather than silently accepted.
_CSP_DIRECTIVES = {
    'default-src': ["'self'"],
    'script-src': [
        "'self'", "'unsafe-inline'", "'unsafe-eval'",
        'https://www.gstatic.com', 'https://apis.google.com',
        'https://www.googletagmanager.com', 'https://cdn.jsdelivr.net',
        'https://cdn.tiny.cloud',
    ],
    'style-src': [
        "'self'", "'unsafe-inline'",
        'https://fonts.googleapis.com', 'https://cdn.jsdelivr.net',
        'https://cdn.tiny.cloud',
    ],
    'font-src': ["'self'", 'https://fonts.gstatic.com', 'data:'],
    'img-src': ["'self'", 'data:', 'blob:', 'https:'],
    'connect-src': [
        "'self'",
        'https://identitytoolkit.googleapis.com',
        'https://securetoken.googleapis.com',
        'https://firestore.googleapis.com',
        'https://firebasestorage.googleapis.com',
        'https://www.googleapis.com',
        'https://cdn.tiny.cloud',
    ],
    # The Firebase Auth project domain is appended at build time from
    # FB_AUTH_DOMAIN -- see _build_csp().
    'frame-src': ["'self'", 'https://accounts.google.com'],
    'frame-ancestors': ["'none'"],
    'object-src': ["'none'"],
    'base-uri': ["'self'"],
    'form-action': ["'self'"],
}


def _build_csp(auth_domain=None):
    """Render the policy, folding in the Firebase Auth domain.

    Firebase Auth runs a hidden iframe on the project's ``authDomain``
    (``https://<project>.firebaseapp.com/__/auth/iframe``) to receive the
    result of a popup or redirect sign-in. Without that origin in
    ``frame-src`` the iframe is blocked and the sign-in result never arrives.

    Derived from config rather than hardcoded so pointing FB_AUTH_DOMAIN at a
    different project -- or a custom auth domain -- does not silently break
    sign-in.
    """
    directives = dict(_CSP_DIRECTIVES)

    if auth_domain:
        origin = auth_domain if '://' in auth_domain else f'https://{auth_domain}'
        if origin not in directives['frame-src']:
            directives['frame-src'] = directives['frame-src'] + [origin]

    return '; '.join(
        f'{name} {" ".join(values)}' for name, values in directives.items()
    )


def register_security_headers(app):
    """Attach defence-in-depth response headers to every response."""
    if not app.config.get('SECURITY_HEADERS_ENABLED', True):
        app.logger.warning('Security headers are DISABLED by configuration')
        return

    csp = _build_csp((app.config.get('FIREBASE_CONFIG') or {}).get('authDomain'))
    hsts_max_age = app.config.get('HSTS_MAX_AGE', 31536000)
    is_production = app.config.get('ENV_NAME') == 'production'

    @app.after_request
    def _apply_security_headers(response):
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('X-Frame-Options', 'DENY')
        response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        response.headers.setdefault('Content-Security-Policy', csp)
        response.headers.setdefault(
            'Permissions-Policy',
            'geolocation=(), microphone=(), camera=(), payment=()'
        )
        # 'same-origin-allow-popups', not 'same-origin'. The strict value puts
        # any window we open into a separate browsing context group and nulls
        # its window.opener -- which silently breaks Firebase's
        # signInWithPopup: the popup authenticates, then has no channel to hand
        # the credential back, so the SDK sees it close empty and reports
        # 'auth/popup-closed-by-user'. Sign-in never completed and no request
        # ever reached /api/auth/verify.
        #
        # This still isolates us from windows that open *us* (the cross-window
        # XS-Leaks protection COOP exists for); it only keeps the opener link
        # for popups this page opens itself.
        response.headers.setdefault(
            'Cross-Origin-Opener-Policy', 'same-origin-allow-popups'
        )
        # HSTS only over TLS: sent on a plaintext response it is ignored by
        # spec, and in development it would pin localhost to https in the
        # browser's preload cache for a year.
        if is_production and request.is_secure:
            response.headers.setdefault(
                'Strict-Transport-Security',
                f'max-age={hsts_max_age}; includeSubDomains'
            )
        return response
