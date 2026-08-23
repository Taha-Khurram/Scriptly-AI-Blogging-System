"""Shared pytest fixtures.

The suite used to hand-assemble a minimal Flask app mounting two blueprints,
because ``create_app()`` had side effects a test could not tolerate: it started
the APScheduler thread, opened a Firestore connection, and spawned a warm-up
thread that made network calls. The consequence was that the thing under test
was not the thing that ships -- it had no error handlers, no security headers,
no CSRF, and no session-timeout hook, so a test could pass against a route that
was broken in production.

``TestingConfig`` now makes the real factory inert: no scheduler, no warm-up, no
Redis, no rate limiting, and CSRF disabled. So these fixtures build the actual
application, and a route test exercises the whole real request pipeline --
middleware, error handling, headers and all.

Firebase is still mocked, and the singleton is pre-seeded *before* any app
module is imported: importing ``app.routes.auth`` instantiates
``FirestoreService()`` at module scope, which calls
``FirebaseLoader.get_instance()``.
"""
from __future__ import annotations

import base64
import json
import os
import sys
from unittest.mock import MagicMock

import pytest

# --- Make the project root importable ---------------------------------------
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# --- Select the testing configuration before config.py is imported ----------
os.environ['FLASK_ENV'] = 'testing'
os.environ.setdefault('SECRET_KEY', 'test-secret-key')
os.environ.setdefault('FIREBASE_SERVICE_ACCOUNT', '{}')
# A REDIS_URL inherited from a developer's shell would make the suite depend on
# a running Redis and share cache state between test runs.
os.environ.pop('REDIS_URL', None)

# --- Seed the Firebase singleton so nothing reaches the network -------------
from app.firebase.firebase_admin import FirebaseLoader  # noqa: E402

FirebaseLoader._instance = MagicMock(name='firestore_client')
FirebaseLoader._bucket = MagicMock(name='storage_bucket')


def make_fake_id_token(uid='test-uid', email='user@gmail.com'):
    """Build a 3-segment JWT whose payload decodes to the given claims.

    ``/api/auth/verify`` decodes the payload itself to probe the session cache
    before verifying anything, so the middle segment must be real base64 JSON.
    Signature verification is mocked separately -- and the security tests rely
    on being able to make the payload disagree with the verified claims.
    """
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip('=')
    payload = base64.urlsafe_b64encode(
        json.dumps({'user_id': uid, 'email': email}).encode()
    ).decode().rstrip('=')
    return f'{header}.{payload}.signature'


@pytest.fixture
def app():
    """The real application, built under TestingConfig."""
    from config import TestingConfig

    from app import create_app

    flask_app = create_app(TestingConfig)
    flask_app.config.update(TESTING=True)
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def clean_cache():
    """Isolate tests from each other's cached values.

    The cache is a module-level singleton, so a user record cached by one test
    would otherwise satisfy the fast path in the next and skip the Firestore
    calls that test is asserting on.
    """
    from app.utils.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def signed_in(client):
    """Establish a signed-in ADMIN session and return the factory.

    Returns a callable so a test can choose the role::

        signed_in(role='USER', user_id='u2')
    """
    def _sign_in(role='ADMIN', user_id='admin-1', name='Admin'):
        with client.session_transaction() as session:
            session['logged_in'] = True
            session['user_id'] = user_id
            session['user_name'] = name
            session['user_role'] = role
            session['user_email'] = f'{user_id}@gmail.com'
        return client
    return _sign_in


@pytest.fixture
def mock_db(monkeypatch):
    """Replace the data layer with a mock for every module that holds one.

    Route modules bind ``db_service = FirestoreService()`` at import time, so
    patching the class is too late -- the instances already exist. This patches
    the bound attribute on each module that has one.
    """
    import importlib

    db = MagicMock(name='db_service')
    db.get_app_settings.return_value = {'app_name': 'Scriptly', 'tagline': ''}

    module_names = (
        'app.routes.auth', 'app.routes.blog_routes', 'app.routes.site_routes',
        'app.routes.activity_routes', 'app.routes.analytics_routes',
        'app.routes.blogs_listing_routes', 'app.routes.gallery_routes',
        'app.routes.leads_routes', 'app.routes.schedule_routes',
        'app.routes.settings_routes', 'app.routes.user_mgmt',
    )
    for name in module_names:
        try:
            module = importlib.import_module(name)
        except ImportError:
            continue
        if hasattr(module, 'db_service'):
            monkeypatch.setattr(module, 'db_service', db)
    return db
