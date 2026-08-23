"""Structured logging with per-request correlation IDs.

The application previously wrote diagnostics with ``print()``. On a hosted
platform that produces an undifferentiated stdout stream: no levels, no
timestamps, no way to tie the three lines a failing request emitted to each
other or to the error the user saw. This module replaces that with:

* a JSON formatter for production (one object per line, ingestible by any log
  platform) and a readable console formatter for development;
* a ``request_id`` attached to every log record for the lifetime of a request,
  also returned to the client in the ``X-Request-ID`` header and in error
  bodies, so a user-reported failure can be found in the logs directly;
* automatic access logging with duration, including a ``WARNING`` for any
  request slower than ``SLOW_REQUEST_MS``;
* redaction of secret-looking values so an API key logged by accident inside
  an exception message does not persist in the log store.

Nothing here depends on Flask's own logger configuration, so library logs
(werkzeug, gunicorn, google-*) end up in the same stream and the same format.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
import uuid
from contextvars import ContextVar

from flask import g, has_request_context, request

# A ContextVar rather than ``flask.g`` because background tasks (blog
# generation, humanization) run outside a request context but should still
# carry the id of the request that started them.
_request_id_var: ContextVar[str] = ContextVar('request_id', default='-')

# Fields already present on every LogRecord; anything else a caller passes via
# ``extra=`` is user context and gets merged into the JSON payload.
_RESERVED_ATTRS = frozenset((
    'args', 'asctime', 'created', 'exc_info', 'exc_text', 'filename',
    'funcName', 'levelname', 'levelno', 'lineno', 'module', 'msecs',
    'message', 'msg', 'name', 'pathname', 'process', 'processName',
    'relativeCreated', 'stack_info', 'thread', 'threadName', 'taskName',
    'request_id',
))

# Substrings that mark a value as sensitive. Matched against the *key* when
# redacting structured context, and against ``key=value`` runs in messages.
_SECRET_HINTS = ('password', 'secret', 'token', 'api_key', 'apikey',
                 'authorization', 'credential', 'private_key', 'cookie')

_SECRET_IN_TEXT = re.compile(
    r'(?i)\b(' + '|'.join(_SECRET_HINTS) + r')["\']?\s*[:=]\s*["\']?([^\s,;"\'}]{4,})'
)

REDACTED = '***redacted***'


def get_request_id() -> str:
    """The correlation id for the current request, or ``'-'`` outside one."""
    return _request_id_var.get()


def set_request_id(request_id: str) -> None:
    """Bind ``request_id`` to the current context (and any thread it spawns)."""
    _request_id_var.set(request_id)


def _redact_text(value: str) -> str:
    return _SECRET_IN_TEXT.sub(lambda m: f'{m.group(1)}={REDACTED}', value)


def _redact_mapping(payload: dict) -> dict:
    out = {}
    for key, value in payload.items():
        lowered = str(key).lower()
        if any(hint in lowered for hint in _SECRET_HINTS):
            out[key] = REDACTED
        elif isinstance(value, dict):
            out[key] = _redact_mapping(value)
        elif isinstance(value, str):
            out[key] = _redact_text(value)
        else:
            out[key] = value
    return out


class RequestIdFilter(logging.Filter):
    """Stamp every record with the current request id."""

    def filter(self, record):
        if not hasattr(record, 'request_id'):
            record.request_id = get_request_id()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with any ``extra=`` context merged in."""

    def __init__(self, service='scriptly', environment='production'):
        super().__init__()
        self.service = service
        self.environment = environment

    def format(self, record):
        payload = {
            'timestamp': time.strftime(
                '%Y-%m-%dT%H:%M:%S', time.gmtime(record.created)
            ) + f'.{int(record.msecs):03d}Z',
            'level': record.levelname,
            'logger': record.name,
            'message': _redact_text(record.getMessage()),
            'request_id': getattr(record, 'request_id', '-'),
            'service': self.service,
            'env': self.environment,
        }

        if record.exc_info:
            payload['exception'] = {
                'type': record.exc_info[0].__name__ if record.exc_info[0] else 'Unknown',
                'stack': _redact_text(self.formatException(record.exc_info)),
            }
        if record.stack_info:
            payload['stack'] = self.formatStack(record.stack_info)

        context = {
            key: value for key, value in record.__dict__.items()
            if key not in _RESERVED_ATTRS and not key.startswith('_')
        }
        if context:
            payload['context'] = _redact_mapping(context)

        # default=str so a datetime or Firestore sentinel in the context never
        # turns a log call into a TypeError inside the logging machinery.
        return json.dumps(payload, default=str, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    """Human-readable single line for local development."""

    _COLORS = {
        'DEBUG': '\033[36m', 'INFO': '\033[32m', 'WARNING': '\033[33m',
        'ERROR': '\033[31m', 'CRITICAL': '\033[35m',
    }
    _RESET = '\033[0m'

    def __init__(self, use_color=True):
        super().__init__(datefmt='%H:%M:%S')
        # Only colourise a real terminal; a redirected stream would otherwise
        # accumulate escape sequences in the log file.
        self.use_color = use_color and sys.stderr.isatty()

    def format(self, record):
        level = record.levelname
        colour = self._COLORS.get(level, '') if self.use_color else ''
        reset = self._RESET if colour else ''
        rid = getattr(record, 'request_id', '-')
        rid_part = f' [{rid[:8]}]' if rid and rid != '-' else ''
        line = (
            f'{self.formatTime(record)} {colour}{level:<8}{reset}'
            f'{rid_part} {record.name}: {_redact_text(record.getMessage())}'
        )
        if record.exc_info:
            line += '\n' + self.formatException(record.exc_info)
        return line


def _build_handler(app):
    """A single stdout handler; the platform owns log shipping and rotation."""
    handler = logging.StreamHandler(stream=sys.stdout)
    if app.config.get('LOG_FORMAT', 'json') == 'json':
        handler.setFormatter(JsonFormatter(
            environment=app.config.get('ENV_NAME', 'production')
        ))
    else:
        handler.setFormatter(ConsoleFormatter())
    handler.addFilter(RequestIdFilter())
    return handler


def configure_logging(app):
    """Install the root handler and wire request/response logging.

    Called once from the app factory, before anything else can emit a record.
    """
    level = getattr(logging, str(app.config.get('LOG_LEVEL', 'INFO')).upper(), logging.INFO)

    root = logging.getLogger()
    # Replace rather than append: gunicorn and the Flask reloader both install
    # their own handlers, and leaving them attached duplicates every line.
    for existing in root.handlers[:]:
        root.removeHandler(existing)
    root.addHandler(_build_handler(app))
    root.setLevel(level)

    app.logger.handlers.clear()
    app.logger.propagate = True
    app.logger.setLevel(level)

    # Chatty libraries: keep their warnings, drop their per-call debug noise.
    for noisy, noisy_level in (
        ('werkzeug', logging.WARNING),
        ('urllib3', logging.WARNING),
        ('google', logging.WARNING),
        ('google.auth', logging.WARNING),
        ('googleapiclient', logging.WARNING),
        ('grpc', logging.WARNING),
        ('apscheduler', logging.WARNING),
        ('markdown', logging.WARNING),
        ('PIL', logging.WARNING),
        ('asyncio', logging.WARNING),
    ):
        logging.getLogger(noisy).setLevel(max(noisy_level, level))

    _register_request_hooks(app)
    _init_sentry(app)


def _register_request_hooks(app):
    """Assign a request id on the way in; log an access line on the way out."""
    slow_ms = app.config.get('SLOW_REQUEST_MS', 2000)
    logger = logging.getLogger('scriptly.access')

    @app.before_request
    def _start_request():
        # Honour an upstream id (load balancer / CDN) so a trace spans hops,
        # but bound its length -- it is attacker-controlled and ends up in
        # every log line for this request.
        incoming = request.headers.get('X-Request-ID', '')
        request_id = incoming[:64] if incoming else uuid.uuid4().hex
        set_request_id(request_id)
        g.request_id = request_id
        g.request_start = time.perf_counter()

    @app.after_request
    def _finish_request(response):
        response.headers['X-Request-ID'] = getattr(g, 'request_id', get_request_id())

        started = getattr(g, 'request_start', None)
        if started is None:
            return response
        duration_ms = (time.perf_counter() - started) * 1000

        # Static assets are served by WhiteNoise outside Flask, but the
        # endpoint still exists for url_for; skip it either way.
        if request.endpoint == 'static':
            return response

        level = logging.INFO
        if response.status_code >= 500:
            level = logging.ERROR
        elif response.status_code >= 400 or duration_ms > slow_ms:
            level = logging.WARNING

        logger.log(
            level,
            '%s %s -> %s in %.1fms',
            request.method, request.path, response.status_code, duration_ms,
            extra={
                'method': request.method,
                'path': request.path,
                'endpoint': request.endpoint,
                'status': response.status_code,
                'duration_ms': round(duration_ms, 2),
                'remote_addr': request.remote_addr,
                'user_agent': (request.headers.get('User-Agent') or '')[:200],
            },
        )
        return response

    @app.teardown_request
    def _clear_request(exc=None):
        set_request_id('-')


def _init_sentry(app):
    """Enable Sentry when a DSN is configured and the SDK is installed.

    Optional on purpose: the project must run without the extra dependency,
    so a missing ``sentry_sdk`` is an info-level note rather than a failure.
    """
    dsn = app.config.get('SENTRY_DSN')
    if not dsn:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
    except ImportError:
        app.logger.info(
            'SENTRY_DSN is set but sentry-sdk is not installed; '
            'error tracking is disabled.'
        )
        return

    sentry_sdk.init(
        dsn=dsn,
        environment=app.config.get('ENV_NAME', 'production'),
        integrations=[
            FlaskIntegration(),
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        ],
        traces_sample_rate=float(os.getenv('SENTRY_TRACES_SAMPLE_RATE', '0.0')),
        # Request bodies can carry draft content and credentials; keep them out.
        send_default_pii=False,
        max_request_body_size='never',
    )
    app.logger.info('Sentry error tracking enabled')


def get_logger(name):
    """Module-level logger accessor: ``logger = get_logger(__name__)``."""
    return logging.getLogger(name)


def log_context(**fields):
    """Build an ``extra=`` mapping, dropping ``None`` values.

    Keeps call sites terse::

        logger.info('published blog', extra=log_context(blog_id=bid, user_id=uid))
    """
    return {key: value for key, value in fields.items() if value is not None}


def current_user_context():
    """Session identity for log records, safe to call outside a request."""
    if not has_request_context():
        return {}
    from flask import session
    return log_context(
        user_id=session.get('user_id'),
        user_role=session.get('user_role'),
    )
