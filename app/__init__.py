"""Application factory.

Composition order matters and is the main thing this module encodes:

1. **Config** -- resolved and validated first, so a misconfigured environment
   fails at boot with a clear message rather than at the first request.
2. **Logging** -- second, so every later step's diagnostics are captured in the
   configured format instead of going out as bare prints.
3. **Infrastructure** (cache, AI client, task pool, Firebase) -- singletons are
   *reconfigured*, never replaced, because modules import them at module scope.
4. **Middleware, extensions, error handlers, blueprints.**
5. **Background work** -- last, and skipped entirely under ``TESTING`` so a test
   never starts a scheduler thread or reaches for the network.

Everything the factory does is idempotent and side-effect-free with respect to
the network when ``TESTING`` is set. That is what lets the test suite build the
real application instead of the hand-rolled partial app it needed before, where
the thing under test was not the thing that ships.
"""
from __future__ import annotations

import logging
import sys

from flask import Flask, redirect, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from whitenoise import WhiteNoise

from app.core.errors import register_error_handlers
from app.core.extensions import apply_csrf_exemptions, init_extensions
from app.core.logging import configure_logging
from app.core.security import (
    admin_required,
    api_admin_required,
    api_login_required,
    current_user,
    enforce_session_timeout,
    login_required,
    register_security_headers,
)
from config import get_config

logger = logging.getLogger(__name__)

# Re-exported so ``from app import admin_required`` keeps working for existing
# call sites while the canonical definition lives in app.core.security.
__all__ = [
    'create_app', 'admin_required', 'login_required',
    'api_login_required', 'api_admin_required', 'current_user',
]


def _force_utf8_streams():
    """Make stdout/stderr UTF-8 before anything can log.

    Agents log progress with emoji. On Windows the default console encoding is
    cp1252, which cannot encode them and raises UnicodeEncodeError *inside the
    logging call* -- killing a background job before any real work runs. A log
    line must never be able to fail a task.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass


def create_app(config_class=None):
    """Build and return a configured Flask application."""
    _force_utf8_streams()

    config_class = config_class or get_config()
    config_class.validate()

    app = Flask(__name__, static_folder='static', template_folder='templates')
    app.config.from_object(config_class)
    app.config['ENV_NAME'] = getattr(config_class, 'ENV_NAME', 'production')

    configure_logging(app)
    app.logger.info(
        'Starting Scriptly',
        extra={'environment': app.config['ENV_NAME'],
               'debug': bool(app.config.get('DEBUG'))},
    )

    _init_infrastructure(app)
    _init_middleware(app)
    init_extensions(app)
    register_error_handlers(app)
    register_security_headers(app)
    enforce_session_timeout(app)
    _register_template_helpers(app)
    _register_blueprints(app)
    _register_root_routes(app)
    # After the routes exist: CSRF exemptions are declared by endpoint name, so
    # they can only be resolved once app.view_functions is populated.
    apply_csrf_exemptions(app)
    _register_cache_policy(app)

    if not app.config.get('TESTING'):
        _start_background_work(app)

    app.logger.info('Scriptly ready')
    return app


# ---------------------------------------------------------------------------
# Infrastructure
# ---------------------------------------------------------------------------

def _init_infrastructure(app):
    """Configure the shared singletons and the Firebase connection."""
    from app.services.gemini_client import gemini
    from app.utils.cache import cache
    from app.utils.task_manager import task_manager

    cache.configure(
        redis_url=app.config.get('REDIS_URL'),
        default_ttl=app.config.get('CACHE_DEFAULT_TTL', 300),
        key_prefix=app.config.get('CACHE_KEY_PREFIX', 'scriptly'),
    )

    gemini.configure(
        api_key=app.config.get('GEMINI_API_KEY'),
        model=app.config.get('GEMINI_MODEL'),
        embedding_model=app.config.get('GEMINI_EMBEDDING_MODEL'),
        timeout=app.config.get('GEMINI_TIMEOUT_SECONDS', 180),
        max_retries=app.config.get('GEMINI_MAX_RETRIES', 2),
    )

    task_manager.configure(
        max_workers=app.config.get('TASK_MAX_WORKERS', 4),
        max_queue_depth=app.config.get('TASK_MAX_QUEUE_DEPTH', 20),
        retention_seconds=app.config.get('TASK_RETENTION_SECONDS', 1800),
    )

    if not app.config.get('TESTING'):
        from app.firebase.firebase_admin import FirebaseLoader
        FirebaseLoader.get_instance(app.config['FIREBASE_SERVICE_ACCOUNT'])

    # After Firebase, which the Firebase storage backend depends on.
    from app.services.storage_service import storage
    storage.configure(
        backend=app.config.get('UPLOAD_BACKEND', 'firebase'),
        max_bytes=app.config.get('UPLOAD_MAX_BYTES', 5 * 1024 * 1024),
    )


def _init_middleware(app):
    """WSGI middleware, outermost first."""
    # Behind a reverse proxy, without this every client address logs as the
    # proxy's, url_for builds http:// links, and request.is_secure is False --
    # which would suppress HSTS and break secure-cookie handling.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    debug_mode = bool(app.config.get('DEBUG'))
    # WhiteNoise builds an in-memory registry of the static folder at startup.
    # In production that is exactly right. In development the reloader only
    # restarts on .py changes, so an edited CSS file would keep being served
    # from the stale copy; autorefresh re-reads changed files per request.
    app.wsgi_app = WhiteNoise(
        app.wsgi_app,
        root='app/static/',
        prefix='static/',
        max_age=app.config.get('STATIC_MAX_AGE', 604800),
        autorefresh=debug_mode,
    )


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

def _register_template_helpers(app):
    """Jinja filters and the global context processor."""
    from app.core.sanitize import strip_all_html
    from app.utils.date_utils import format_date, format_datetime, format_time
    from app.utils.text_utils import excerpt as text_excerpt, strip_markdown

    @app.template_filter('localized_date')
    def localized_date_filter(value, settings=None):
        """Format a date in the viewer's timezone and preferred format."""
        if settings is None:
            return format_date(value)
        return format_date(
            value,
            settings.get('date_format', 'MMM DD, YYYY'),
            settings.get('timezone', 'UTC'),
        )

    @app.template_filter('localized_time')
    def localized_time_filter(value, settings=None):
        if settings is None:
            return format_time(value)
        return format_time(
            value,
            settings.get('time_format', '12h'),
            settings.get('timezone', 'UTC'),
        )

    @app.template_filter('localized_datetime')
    def localized_datetime_filter(value, settings=None):
        if settings is None:
            return format_datetime(value)
        return format_datetime(
            value,
            settings.get('date_format', 'MMM DD, YYYY'),
            settings.get('time_format', '12h'),
            settings.get('timezone', 'UTC'),
        )

    @app.template_filter('plain_text')
    def plain_text_filter(value):
        """Flatten Markdown/HTML content to readable prose."""
        return strip_markdown(value)

    @app.template_filter('excerpt')
    def excerpt_filter(value, length=180):
        """Word-boundary summary, free of Markdown markers."""
        return text_excerpt(value, length)

    @app.template_filter('no_html')
    def no_html_filter(value, length=None):
        """Strip every tag. For meta descriptions and social previews."""
        return strip_all_html(value, length)

    @app.context_processor
    def inject_globals():
        """App settings and identity available to every template.

        Wrapped because a template must still render when Firestore is
        unreachable -- an error page that itself fails to render leaves the user
        with a blank screen and no way to recover.
        """
        from datetime import datetime, timezone

        from app.firebase.firestore_service import FirestoreService

        year = datetime.now(timezone.utc).year
        try:
            app_settings = FirestoreService().get_app_settings()
        except Exception:
            logger.warning('Falling back to default app settings', exc_info=True)
            app_settings = {'app_name': 'Scriptly', 'tagline': ''}
        return {
            'app_config': app_settings,
            'current_year': year,
            'current_user': current_user(),
        }


# ---------------------------------------------------------------------------
# Blueprints
# ---------------------------------------------------------------------------

# (module path, attribute, url_prefix). One table instead of thirteen import
# lines and thirteen register calls, so adding a blueprint is a one-line change
# and a missing registration is visible at a glance.
_BLUEPRINTS = (
    ('app.routes.health_routes', 'health_bp', None),
    ('app.routes.auth', 'auth_bp', None),
    ('app.routes.blog_routes', 'blog_bp', None),
    ('app.routes.site_routes', 'site_bp', None),
    ('app.routes.newsletter_routes', 'newsletter_bp', None),
    ('app.routes.settings_routes', 'settings_bp', None),
    ('app.routes.activity_routes', 'activity_bp', None),
    ('app.routes.blogs_listing_routes', 'blogs_bp', None),
    ('app.routes.analytics_routes', 'analytics_bp', None),
    ('app.routes.schedule_routes', 'schedule_bp', None),
    ('app.routes.leads_routes', 'leads_bp', None),
    ('app.routes.gallery_routes', 'gallery_bp', None),
    ('app.routes.optimization_routes', 'optimization_bp', None),
    ('app.routes.user_mgmt', 'user_bp', '/users'),
)


def _register_blueprints(app):
    """Import and register every blueprint in :data:`_BLUEPRINTS`."""
    from importlib import import_module

    for module_path, attribute, url_prefix in _BLUEPRINTS:
        module = import_module(module_path)
        blueprint = getattr(module, attribute)
        app.register_blueprint(blueprint, url_prefix=url_prefix)

    app.logger.debug('Registered %s blueprints', len(_BLUEPRINTS))


def _register_root_routes(app):
    """The single route that does not belong to a blueprint."""

    @app.route('/')
    def index():
        if not session.get('logged_in'):
            return redirect(url_for('auth_bp.login'))
        return redirect(url_for('blog.home'))


# ---------------------------------------------------------------------------
# Caching policy
# ---------------------------------------------------------------------------

def _register_cache_policy(app):
    """Set ``Cache-Control`` per response class.

    The previous blanket ``no-store`` on every dynamic response was correct for
    the authenticated dashboard -- a cached page there hands the next viewer
    another user's data, and the browser's back/forward cache would restore a
    page whose buttons act on stale state.

    It was wrong for the public site. Those pages are identical for every
    visitor and are the highest-traffic part of the application, so forbidding
    caching meant every view re-queried Firestore against a 50k reads/day quota.
    Public pages set their own headers in the site blueprint; this hook only
    applies the strict policy where a session is involved, and never overwrites
    a header a view has already chosen.
    """

    @app.after_request
    def apply_cache_policy(response):
        if request.endpoint == 'static':
            return response
        if 'Cache-Control' in response.headers:
            return response

        endpoint = request.endpoint or ''
        is_public_site = endpoint.startswith('site_bp.')
        is_health = endpoint.startswith('health.')

        if is_public_site and response.status_code == 200 and request.method == 'GET':
            response.headers['Cache-Control'] = (
                'public, max-age=60, stale-while-revalidate=120'
            )
        elif is_health:
            response.headers['Cache-Control'] = 'no-store'
        else:
            response.headers['Cache-Control'] = (
                'no-store, no-cache, must-revalidate, max-age=0'
            )
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
        return response


# ---------------------------------------------------------------------------
# Background work
# ---------------------------------------------------------------------------

def _start_background_work(app):
    """Start the scheduler and warm the slow first-request paths."""
    from app.scheduler import init_scheduler

    init_scheduler(app)
    _warm_dependencies(app)


def _warm_dependencies(app):
    """Pre-open the connections the first real request would otherwise pay for.

    Firebase Auth fetches and caches Google's token-signing certificates on
    first verification, and Firestore opens a gRPC channel on first query. Cold,
    that adds a second or more to whichever unlucky user arrives first -- which
    on a platform that spins down when idle is a common experience.

    The certificate fetch is triggered by verifying a deliberately invalid
    token: the network fetch and cache population happen before the signature
    check fails, so the failure is the mechanism, not an error to fix.
    """
    import threading

    def warm():
        try:
            from firebase_admin import auth
            auth.verify_id_token('warmup.invalid.token', check_revoked=False)
        except Exception:
            pass  # expected: the point is the certificate fetch it performs

        try:
            from app.firebase.firestore_service import FirestoreService
            FirestoreService().get_app_settings()
            logger.debug('Firestore connection warmed')
        except Exception:
            logger.warning('Dependency warm-up failed', exc_info=True)

    threading.Thread(target=warm, name='warmup', daemon=True).start()
