"""Application exception hierarchy and centralised error handling.

Two problems this solves.

First, routes used to end in ``except Exception as e: return jsonify({'error':
str(e)}), 500``. That leaks Firebase error codes, gRPC internals and sometimes
filesystem paths to unauthenticated callers, and it flattens every distinct
failure into one status code. Here, each failure mode is a class with a status
code and a stable machine-readable ``code``; the handler decides what the
client is allowed to see.

Second, an unexpected exception used to be reported as whatever string it
carried. Now it is logged in full server-side with the request id, and the
client gets a generic message plus that id -- enough for a user to quote in a
bug report, nothing an attacker can use to map the system.

Content negotiation matters for a mixed app: the same failure must render an
HTML error page for a browser navigation and a JSON body for a ``fetch()`` from
the dashboard. ``_wants_json()`` decides, so no route has to.
"""

from __future__ import annotations

import logging

from flask import jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from app.core.logging import get_request_id

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base class for every deliberately raised application failure.

    ``message`` is safe to show a user. ``code`` is a stable identifier the
    frontend can branch on without string-matching prose. ``details`` carries
    field-level information for validation failures.
    """

    status_code = 500
    code = 'internal_error'
    message = 'Something went wrong. Please try again.'

    def __init__(self, message=None, *, code=None, status_code=None, details=None):
        super().__init__(message or self.message)
        if message:
            self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        self.details = details or {}

    def to_dict(self):
        payload = {
            'success': False,
            'error': self.message,
            'code': self.code,
            'request_id': get_request_id(),
        }
        if self.details:
            payload['details'] = self.details
        return payload


class ValidationError(AppError):
    """Caller-supplied input failed validation."""

    status_code = 400
    code = 'validation_error'
    message = 'The submitted data is invalid.'


class AuthenticationError(AppError):
    """No valid session; the caller must sign in."""

    status_code = 401
    code = 'unauthenticated'
    message = 'Please sign in to continue.'


class SessionExpiredError(AuthenticationError):
    """A session existed but has passed its inactivity window.

    Distinct from :class:`AuthenticationError` because the frontend shows a
    different message and preserves the current page for a post-login return.
    """

    code = 'session_expired'
    message = 'Your session has expired. Please sign in again.'


class AuthorizationError(AppError):
    """Authenticated, but not permitted to perform this action."""

    status_code = 403
    code = 'forbidden'
    message = 'You do not have permission to perform this action.'


class NotFoundError(AppError):
    """The requested resource does not exist, or is not visible to the caller."""

    status_code = 404
    code = 'not_found'
    message = 'The requested resource was not found.'


class ConflictError(AppError):
    """The request collides with existing state (duplicate slug, for example)."""

    status_code = 409
    code = 'conflict'
    message = 'That change conflicts with existing data.'


class PayloadTooLargeError(AppError):
    """Request body exceeded the configured limit."""

    status_code = 413
    code = 'payload_too_large'
    message = 'The uploaded file is too large.'


class RateLimitError(AppError):
    """Caller exceeded a rate limit."""

    status_code = 429
    code = 'rate_limited'
    message = 'Too many requests. Please slow down and try again shortly.'


class CapacityError(AppError):
    """A bounded internal queue is full; the work was not accepted.

    503 rather than 429: the caller has not misbehaved, the system is
    momentarily saturated, and a ``Retry-After`` is the honest answer.
    """

    status_code = 503
    code = 'at_capacity'
    message = 'The system is busy. Please try again in a moment.'


class ExternalServiceError(AppError):
    """An upstream dependency (Gemini, Firestore, SMTP) failed or timed out."""

    status_code = 502
    code = 'upstream_error'
    message = 'An external service is temporarily unavailable. Please try again.'

    def __init__(self, message=None, *, service=None, **kwargs):
        super().__init__(message, **kwargs)
        self.service = service


class ConfigurationError(AppError):
    """A required setting is missing at the point it is needed."""

    status_code = 500
    code = 'misconfigured'
    message = 'This feature is not configured. Contact an administrator.'


def _wants_json():
    """True when the caller expects JSON rather than an HTML error page.

    Ordered from most to least reliable signal. The ``/api/`` prefix is checked
    because some older frontend calls send neither ``Accept: application/json``
    nor ``X-Requested-With``.
    """
    if request.path.startswith('/api/') or '/api/' in request.path:
        return True
    if request.is_json:
        return True
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return True
    accept = request.accept_mimetypes
    return (
        accept['application/json'] >= accept['text/html']
        and accept['application/json'] > 0
    )


def _render_error_page(status_code, message):
    """Render the shared error template, degrading to plain text if it fails.

    An exception raised *inside* the error handler would otherwise escape as an
    unhandled 500 and lose the original failure entirely.
    """
    try:
        return render_template(
            'errors/error.html', status_code=status_code, message=message
        ), status_code
    except Exception:
        logger.exception('Error template failed to render')
        return message, status_code


def register_error_handlers(app):
    """Attach handlers for AppError, HTTPException and everything else."""

    @app.errorhandler(AppError)
    def _handle_app_error(error):
        # Client mistakes are expected traffic and belong at INFO/WARNING;
        # only server-side failures deserve a stack trace.
        if error.status_code >= 500:
            logger.error(
                'Application error: %s', error.message,
                exc_info=True,
                extra={'error_code': error.code, 'status': error.status_code},
            )
        else:
            logger.info(
                'Client error: %s', error.message,
                extra={'error_code': error.code, 'status': error.status_code},
            )

        if _wants_json():
            response = jsonify(error.to_dict())
            response.status_code = error.status_code
            if isinstance(error, (RateLimitError, CapacityError)):
                response.headers['Retry-After'] = '30'
            return response

        if isinstance(error, AuthenticationError):
            from flask import redirect, url_for
            expired = 1 if isinstance(error, SessionExpiredError) else None
            return redirect(url_for('auth_bp.login', expired=expired))

        return _render_error_page(error.status_code, error.message)

    @app.errorhandler(HTTPException)
    def _handle_http_exception(error):
        # Werkzeug's own aborts (abort(404), 405 from routing, 413 from the
        # body-size guard). Reuse the same shape so clients see one contract.
        status = error.code or 500
        message = error.description or 'Request could not be completed.'

        if status >= 500:
            logger.error('HTTP %s: %s', status, message, exc_info=True)

        if _wants_json():
            response = jsonify({
                'success': False,
                'error': message,
                'code': _HTTP_CODES.get(status, 'http_error'),
                'request_id': get_request_id(),
            })
            response.status_code = status
            return response

        return _render_error_page(status, message)

    @app.errorhandler(Exception)
    def _handle_unexpected(error):
        # Anything that reached here is a bug. Log it whole, tell the client
        # nothing but the request id.
        logger.critical(
            'Unhandled exception: %s', error,
            exc_info=True,
            extra={'path': request.path, 'method': request.method},
        )

        request_id = get_request_id()
        message = (
            'An unexpected error occurred. Quote reference '
            f'{request_id} if you contact support.'
        )

        if _wants_json():
            response = jsonify({
                'success': False,
                'error': 'An unexpected error occurred.',
                'code': 'internal_error',
                'request_id': request_id,
            })
            response.status_code = 500
            return response

        return _render_error_page(500, message)

    app.logger.debug('Error handlers registered')


_HTTP_CODES = {
    400: 'bad_request',
    401: 'unauthenticated',
    403: 'forbidden',
    404: 'not_found',
    405: 'method_not_allowed',
    409: 'conflict',
    413: 'payload_too_large',
    422: 'unprocessable',
    429: 'rate_limited',
    500: 'internal_error',
    502: 'upstream_error',
    503: 'unavailable',
    504: 'timeout',
}
