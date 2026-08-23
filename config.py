"""Environment-aware application configuration.

One class per deployment target, all deriving from :class:`BaseConfig`, so a
setting is declared exactly once and specialised only where it genuinely
differs. ``get_config()`` is the single entry point; ``create_app()`` calls it
so nothing else has to know which environment it is running in.

Every value is read from the environment. Secrets are never defaulted in
:class:`ProductionConfig` -- ``validate()`` refuses to boot without them rather
than silently running with a guessable ``SECRET_KEY``, which is the failure
mode that turns a config mistake into a session-forgery vulnerability.
"""
from __future__ import annotations

import os
import secrets
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()


def _env_bool(name, default=False):
    """Read a boolean env var, accepting the usual spellings of true/false."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ('1', 'true', 'yes', 'on')


def _env_int(name, default):
    """Read an int env var, falling back to ``default`` on anything unparsable.

    A malformed ``TASK_MAX_WORKERS=four`` should degrade to the default rather
    than crash on import -- that happens before logging is configured, so the
    operator would get a bare traceback with no context.
    """
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class ConfigurationError(RuntimeError):
    """Raised when the environment cannot support the requested config."""


class BaseConfig:
    """Settings shared by every environment."""

    ENV_NAME = 'base'

    # --- Core Flask -------------------------------------------------------
    SECRET_KEY = os.getenv('SECRET_KEY')

    # Werkzeug rejects a body larger than this at the WSGI layer, before any
    # byte reaches a view. Without it, an upload route that calls
    # ``file.read()`` buffers the whole request in RAM and a single oversized
    # POST can exhaust the instance.
    MAX_CONTENT_LENGTH = _env_int('MAX_CONTENT_LENGTH_MB', 8) * 1024 * 1024

    # --- Sessions ---------------------------------------------------------
    # Sliding window: SESSION_REFRESH_EACH_REQUEST re-stamps the cookie on
    # every request, so this is an *inactivity* timeout, not a hard cap. Eight
    # hours suits a content-authoring tool where a user may draft for a long
    # stretch without issuing a request.
    PERMANENT_SESSION_LIFETIME = timedelta(
        minutes=_env_int('SESSION_TIMEOUT_MINUTES', 480)
    )
    SESSION_REFRESH_EACH_REQUEST = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_NAME = 'scriptly_session'

    # --- CSRF (Flask-WTF) -------------------------------------------------
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = None       # tie token lifetime to the session
    WTF_CSRF_SSL_STRICT = True

    # --- Firebase ---------------------------------------------------------
    FIREBASE_SERVICE_ACCOUNT = os.getenv('FIREBASE_SERVICE_ACCOUNT')
    FIREBASE_STORAGE_BUCKET = os.getenv('FB_STORAGE_BUCKET')
    FIREBASE_CONFIG = {
        'apiKey': os.getenv('FB_API_KEY'),
        'authDomain': os.getenv('FB_AUTH_DOMAIN'),
        'projectId': os.getenv('FB_PROJECT_ID'),
        'storageBucket': os.getenv('FB_STORAGE_BUCKET'),
        'messagingSenderId': os.getenv('FB_SENDER_ID'),
        'appId': os.getenv('FB_APP_ID'),
        'measurementId': os.getenv('FB_MEASUREMENT_ID'),
    }

    # --- AI ---------------------------------------------------------------
    GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
    # Every agent resolves its model through this one setting. The project has
    # already had to move models twice over quota errors; one knob means the
    # next move is an env change, not a sweep through nine files.
    GEMINI_MODEL = os.getenv('GEMINI_MODEL', 'gemini-flash-lite-latest')
    GEMINI_EMBEDDING_MODEL = os.getenv(
        'GEMINI_EMBEDDING_MODEL', 'models/gemini-embedding-001'
    )
    GEMINI_TIMEOUT_SECONDS = _env_int('GEMINI_TIMEOUT_SECONDS', 180)
    GEMINI_MAX_RETRIES = _env_int('GEMINI_MAX_RETRIES', 2)

    # --- Third-party APIs -------------------------------------------------
    RAPIDAPI_KEY = os.getenv('RAPIDAPI_KEY')
    AHREFS_RAPIDAPI_KEY = os.getenv('AHREFS_RAPIDAPI_KEY')
    SITE_AUDIT_RAPIDAPI_KEY = os.getenv('SITE_AUDIT_RAPIDAPI_KEY')
    GOOGLE_OAUTH_CLIENT_ID = os.getenv('GOOGLE_OAUTH_CLIENT_ID')
    GOOGLE_OAUTH_CLIENT_SECRET = os.getenv('GOOGLE_OAUTH_CLIENT_SECRET')

    # --- Email ------------------------------------------------------------
    GMAIL_USER = os.getenv('GMAIL_USER')
    GMAIL_APP_PASSWORD = os.getenv('GMAIL_APP_PASSWORD')
    FROM_NAME = os.getenv('FROM_NAME', 'Scriptly')

    # --- Cache / Redis ----------------------------------------------------
    # With REDIS_URL absent the cache falls back to a per-process in-memory
    # store. That is correct for one worker and *incorrect* for several, so
    # /healthz reports which backend is actually live.
    REDIS_URL = os.getenv('REDIS_URL')
    CACHE_DEFAULT_TTL = _env_int('CACHE_DEFAULT_TTL', 300)
    CACHE_KEY_PREFIX = os.getenv('CACHE_KEY_PREFIX', 'scriptly')

    # --- Rate limiting ----------------------------------------------------
    RATELIMIT_ENABLED = _env_bool('RATELIMIT_ENABLED', True)
    RATELIMIT_DEFAULT = os.getenv('RATELIMIT_DEFAULT', '1200 per hour')
    RATELIMIT_HEADERS_ENABLED = True
    # Public, unauthenticated endpoints that spend money per call (Gemini) get
    # their own tighter budgets, applied per route.
    RATELIMIT_PUBLIC_AI = os.getenv('RATELIMIT_PUBLIC_AI', '10 per minute')
    RATELIMIT_PUBLIC_WRITE = os.getenv('RATELIMIT_PUBLIC_WRITE', '5 per minute')
    RATELIMIT_AUTH = os.getenv('RATELIMIT_AUTH', '20 per minute')
    RATELIMIT_AI_GENERATE = os.getenv('RATELIMIT_AI_GENERATE', '30 per hour')

    # --- Background work --------------------------------------------------
    TASK_MAX_WORKERS = _env_int('TASK_MAX_WORKERS', 4)
    TASK_MAX_QUEUE_DEPTH = _env_int('TASK_MAX_QUEUE_DEPTH', 20)
    TASK_RETENTION_SECONDS = _env_int('TASK_RETENTION_SECONDS', 1800)

    # --- Scheduler --------------------------------------------------------
    # Only one process may run the auto-publisher; several would double-publish.
    # See app/scheduler.py for how single-runner ownership is established.
    SCHEDULER_ENABLED = _env_bool('SCHEDULER_ENABLED', True)
    SCHEDULER_INTERVAL_SECONDS = _env_int('SCHEDULER_INTERVAL_SECONDS', 60)
    SCHEDULER_LOCK_TTL_SECONDS = _env_int('SCHEDULER_LOCK_TTL_SECONDS', 90)

    # --- Uploads ----------------------------------------------------------
    UPLOAD_MAX_BYTES = _env_int('UPLOAD_MAX_MB', 5) * 1024 * 1024
    # 'firebase' survives deploys and works across instances; 'local' is the
    # legacy on-disk path, kept for offline development only.
    UPLOAD_BACKEND = os.getenv('UPLOAD_BACKEND', 'firebase')

    # --- Logging / observability ------------------------------------------
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    LOG_FORMAT = os.getenv('LOG_FORMAT', 'json')   # 'json' or 'console'
    SLOW_REQUEST_MS = _env_int('SLOW_REQUEST_MS', 2000)
    SENTRY_DSN = os.getenv('SENTRY_DSN')

    # --- Security headers -------------------------------------------------
    SECURITY_HEADERS_ENABLED = _env_bool('SECURITY_HEADERS_ENABLED', True)
    HSTS_MAX_AGE = _env_int('HSTS_MAX_AGE', 31536000)

    # --- Static assets ----------------------------------------------------
    STATIC_MAX_AGE = _env_int('STATIC_MAX_AGE', 604800)

    @classmethod
    def validate(cls):
        """Fail fast on a configuration that cannot serve traffic safely."""
        missing = [
            name for name in ('SECRET_KEY', 'FIREBASE_SERVICE_ACCOUNT')
            if not getattr(cls, name, None)
        ]
        if missing:
            raise ConfigurationError(
                'Missing required configuration: '
                + ', '.join(missing)
                + '. Set them in the environment (or .env for local dev).'
            )


class DevelopmentConfig(BaseConfig):
    """Local development: reloader on, plaintext HTTP, verbose logs."""

    ENV_NAME = 'development'
    DEBUG = True
    TESTING = False

    # Localhost is served over http, so a Secure-only cookie would never come
    # back and every login would appear to silently fail.
    SESSION_COOKIE_SECURE = False
    WTF_CSRF_SSL_STRICT = False

    LOG_FORMAT = os.getenv('LOG_FORMAT', 'console')
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'DEBUG')
    STATIC_MAX_AGE = 0

    # A dev box has no Redis by default and one developer generates no load,
    # so rate limiting only gets in the way of manual testing.
    RATELIMIT_ENABLED = _env_bool('RATELIMIT_ENABLED', False)

    SECRET_KEY = os.getenv('SECRET_KEY') or 'dev-only-insecure-key'

    @classmethod
    def validate(cls):
        if not cls.FIREBASE_SERVICE_ACCOUNT:
            raise ConfigurationError(
                'FIREBASE_SERVICE_ACCOUNT is required even in development; '
                'point it at serviceAccountKey.json or paste the JSON.'
            )


class TestingConfig(BaseConfig):
    """Unit/integration tests: no network, no background threads, no CSRF."""

    ENV_NAME = 'testing'
    DEBUG = False
    TESTING = True

    SECRET_KEY = 'testing-secret-key'
    FIREBASE_SERVICE_ACCOUNT = '{}'

    SESSION_COOKIE_SECURE = False
    WTF_CSRF_ENABLED = False
    WTF_CSRF_SSL_STRICT = False

    RATELIMIT_ENABLED = False
    SCHEDULER_ENABLED = False
    REDIS_URL = None
    LOG_LEVEL = 'CRITICAL'
    LOG_FORMAT = 'console'
    SECURITY_HEADERS_ENABLED = True

    @classmethod
    def validate(cls):
        """Tests supply their own doubles; nothing to require."""
        return


class ProductionConfig(BaseConfig):
    """Production: every secret mandatory, every cookie hardened."""

    ENV_NAME = 'production'
    DEBUG = False
    TESTING = False

    PREFERRED_URL_SCHEME = 'https'

    @classmethod
    def validate(cls):
        super().validate()

        weak = {'', 'change-me-to-a-long-random-string', 'dev-only-insecure-key',
                'secret', 'changeme', 'testing-secret-key'}
        if (cls.SECRET_KEY or '').strip().lower() in weak:
            raise ConfigurationError(
                'SECRET_KEY is a placeholder value. Generate one with '
                'python -c "import secrets; print(secrets.token_urlsafe(64))"'
            )
        if len(cls.SECRET_KEY or '') < 32:
            raise ConfigurationError(
                'SECRET_KEY must be at least 32 characters in production.'
            )
        if not cls.GEMINI_API_KEY:
            raise ConfigurationError(
                'GEMINI_API_KEY is required; every AI feature depends on it.'
            )


_CONFIGS = {
    'development': DevelopmentConfig,
    'dev': DevelopmentConfig,
    'testing': TestingConfig,
    'test': TestingConfig,
    'production': ProductionConfig,
    'prod': ProductionConfig,
}


def get_config(name=None):
    """Resolve the config class for ``name``, defaulting to ``$FLASK_ENV``.

    Defaults to production: an unset or misspelled ``FLASK_ENV`` should select
    the *safe* configuration, not the one with an insecure cookie and a
    hardcoded secret key.
    """
    key = (name or os.getenv('FLASK_ENV') or 'production').strip().lower()
    return _CONFIGS.get(key, ProductionConfig)


def generate_secret_key():
    """Convenience helper for operators bootstrapping a new environment."""
    return secrets.token_urlsafe(64)


# Backwards compatibility: existing code and tests import ``Config`` directly.
Config = get_config()
