"""CSRF protection: exemptions, and the token round-trip the frontend relies on.

These tests exist because of a failure that was invisible to the rest of the
suite. ``CSRFProtect.exempt`` takes a view function or a *dotted import path*
(``app.routes.auth.verify_token``); handed any other string it stores the value
as-is and matches nothing. It was being called with Flask **endpoint** names
(``auth_bp.verify_token``) -- a different namespace -- so every exemption was a
silent no-op.

The user-visible result was that sign-in could not complete at all: the browser
has no CSRF token to present before a session exists, so ``/api/auth/verify``
answered 400 "session security token expired" and the redirect to the dashboard
never happened.

``TestingConfig`` sets ``WTF_CSRF_ENABLED = False``, which is why no existing
test caught this. The fixture here turns it back on, so these are the only
tests running the real CSRF pipeline.
"""
from __future__ import annotations

import pytest

from app.core.extensions import CSRF_EXEMPT_ENDPOINTS, csrf


@pytest.fixture
def csrf_app():
    """The real app with CSRF protection actually enabled.

    ``_init_csrf`` returns early when ``WTF_CSRF_ENABLED`` is false -- no
    ``before_request`` hook is installed at all -- so this has to be set in the
    config the factory reads, not flipped afterwards.
    """
    from config import TestingConfig

    from app import create_app

    class CsrfTestingConfig(TestingConfig):
        WTF_CSRF_ENABLED = True

    flask_app = create_app(CsrfTestingConfig)
    flask_app.config.update(TESTING=True)
    return flask_app


@pytest.fixture
def csrf_client(csrf_app):
    return csrf_app.test_client()


def _read_csrf_cookie(client):
    """The token as JavaScript would read it out of ``document.cookie``."""
    cookie = client.get_cookie('csrf_token')
    return cookie.decoded_value if cookie else None


# ---------------------------------------------------------------------------
# Exemption registration
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('endpoint', sorted(CSRF_EXEMPT_ENDPOINTS))
def test_exemption_resolves_to_a_real_view(app, endpoint):
    """A stale or renamed endpoint name must fail here, not in production.

    An entry that resolves to nothing leaves the endpoint fully CSRF-protected
    while the list claims otherwise -- which is exactly how login broke.
    """
    assert endpoint in app.view_functions, (
        f'{endpoint} is on the CSRF exemption list but no such endpoint is '
        f'registered, so the exemption does nothing.'
    )


@pytest.mark.parametrize('endpoint', sorted(CSRF_EXEMPT_ENDPOINTS))
def test_exemption_is_registered_in_the_form_flask_wtf_checks(app, endpoint):
    """Guards the endpoint-name vs import-path confusion specifically.

    Flask-WTF compares ``f'{view.__module__}.{view.__name__}'`` against its
    exempt set. Registering the endpoint name instead passes silently and
    protects nothing, so assert on the value it actually compares.
    """
    view = app.view_functions[endpoint]
    dotted_path = f'{view.__module__}.{view.__name__}'

    assert dotted_path in csrf._exempt_views, (
        f'{endpoint} resolves to {dotted_path}, which is not in the exempt '
        f'set -- the exemption will not apply.'
    )


# ---------------------------------------------------------------------------
# The login path, which cannot present a token
# ---------------------------------------------------------------------------

def test_login_verify_accepts_a_post_with_no_csrf_token(csrf_client):
    """The regression test for the broken sign-in.

    A garbage token still fails verification (401) -- the point is that it is
    rejected by the *auth* check, having got past CSRF, rather than refused as
    ``csrf_invalid`` before the route ever runs.
    """
    response = csrf_client.post('/api/auth/verify', json={'idToken': 'a.b.c'})

    assert response.get_json()['code'] != 'csrf_invalid'
    assert response.status_code == 401


def test_public_site_writes_accept_a_post_with_no_csrf_token(csrf_client):
    """Unauthenticated visitor endpoints have no session to carry a token."""
    response = csrf_client.post(
        '/newsletter/unsubscribe', data={'email': 'reader@gmail.com'}
    )

    assert response.status_code != 400 or (
        (response.get_json() or {}).get('code') != 'csrf_invalid'
    )


# ---------------------------------------------------------------------------
# The token round-trip for authenticated writes
# ---------------------------------------------------------------------------

def test_page_load_publishes_a_javascript_readable_token(csrf_client):
    """The fetch layer reads the token from this cookie.

    It must be present and must *not* be HttpOnly, or ``document.cookie``
    cannot see it and every write in the dashboard fails.
    """
    response = csrf_client.get('/login')

    assert _read_csrf_cookie(csrf_client), 'no csrf_token cookie was set'

    set_cookie = ' '.join(
        value for key, value in response.headers.items()
        if key.lower() == 'set-cookie' and value.startswith('csrf_token=')
    )
    assert 'HttpOnly' not in set_cookie


def test_protected_write_is_refused_without_a_token(csrf_client):
    """The protection is real: this is what the exemptions are carved out of."""
    response = csrf_client.post('/api/profile/update', json={'name': 'New'})

    assert response.status_code == 400
    assert response.get_json()['code'] == 'csrf_invalid'


def test_protected_write_passes_when_the_cookie_is_echoed_back(csrf_client):
    """What the fetch wrapper does: cookie value -> ``X-CSRFToken`` header.

    Reaching the 401 auth check means CSRF validation passed; the request is
    deliberately unauthenticated so this asserts on the CSRF layer alone.
    """
    csrf_client.get('/login')
    token = _read_csrf_cookie(csrf_client)

    response = csrf_client.post(
        '/api/profile/update',
        json={'name': 'New'},
        headers={'X-CSRFToken': token},
    )

    assert response.get_json()['code'] != 'csrf_invalid'
    assert response.status_code == 401
