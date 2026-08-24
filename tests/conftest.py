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


# Realistic return shapes for the data-layer methods routes actually consume.
#
# A bare MagicMock is truthy and supports attribute access, which is enough for
# a route that just passes a value to a template -- but not for one that calls
# len() on it, iterates it, or does pagination arithmetic with it. Without these
# defaults a smoke test cannot tell "this route is broken" from "the mock
# returned the wrong type", which makes the whole sweep useless.
#
# Keyed by method name; anything not listed keeps MagicMock's default.
_DB_RETURN_SHAPES = {
    # --- single documents / maps ---
    'get_app_settings': {'app_name': 'Scriptly', 'tagline': ''},
    'get_site_settings': {'site_name': 'Test Site', 'site_slug': 'test',
                          'posts_per_page': 10, 'timezone': 'UTC',
                          'date_format': 'MMM DD, YYYY', 'time_format': '12h'},
    'get_user_by_id': {'uid': 'admin-1', 'name': 'Admin', 'email': 'a@gmail.com',
                       'role': 'ADMIN', 'profile_image': ''},
    'get_blog_by_id': None,
    'get_gallery_image': None,
    'get_comment_by_id': None,
    'get_category_by_id': None,
    'get_pending_invitation_by_email': None,
    'get_published_blog_by_id': None,
    'get_published_blog_by_slug': None,
    'resolve_site_identifier': (None, None),
    'get_site_owner_for_user': 'admin-1',

    # --- counts ---
    'get_total_blogs_count': 0,
    'get_published_count': 0,
    'get_user_published_count': 0,
    'get_subscriber_count': 0,

    # --- lists ---
    'get_all_categories': [],
    'get_category_names': [],
    'get_team_categories': [],
    'get_user_blog_categories': [],
    'get_blogs_by_status': [],
    'get_blogs_by_category': [],
    'get_approval_queue': [],
    'get_recent_activity': [],
    'get_my_sub_users': [],
    'get_invitations_by_admin': [],
    'get_scheduled_blogs': [],
    'get_due_scheduled_blogs': [],
    'get_all_scheduled_for_calendar': [],
    'get_schedule_entries_for_calendar': [],
    'get_published_blogs': [],
    'get_comments_for_blog': [],
    'get_newsletter_subscribers': [],
    'get_newsletter_history': [],
    'get_newsletter_drafts': [],
    'get_blogs_with_embeddings': [],
    'get_blogs_without_embeddings': [],
    'get_user_seo_reports': [],

    # --- paginated results ---
    # Shapes copied from the repositories' own `return` statements. Guessing
    # here is worse than not mocking at all: a wrong shape produces a 500 that
    # looks exactly like a broken route.
    'get_paginated_drafts': ([], 0),           # tuple, not a dict
    'get_all_blogs_filtered': {'blogs': [], 'total': 0, 'page': 1,
                               'per_page': 10, 'total_pages': 1},
    'get_comments_for_dashboard': {'comments': [], 'total': 0, 'page': 1,
                                   'per_page': 20, 'total_pages': 0},
    'get_contact_submissions': {'submissions': [], 'total': 0, 'page': 1,
                                'per_page': 10, 'total_pages': 0},
    'get_gallery_images': {'images': [], 'total': 0, 'library_total': 0,
                           'matched_size': 0, 'type_counts': {}, 'page': 1,
                           'per_page': 24, 'total_pages': 0},
    'get_all_activity_for_admin': {'activities': [], 'total': 0, 'page': 1,
                                   'per_page': 10, 'total_pages': 1},

    # --- stats maps ---
    'get_activity_stats': {'total': 0, 'blog': 0, 'user': 0, 'comment': 0,
                           'settings': 0, 'newsletter': 0, 'category': 0},
    'get_comment_stats': {'total': 0, 'published': 0, 'ai_edited': 0, 'removed': 0},
    'get_contact_stats': {'total': 0, 'unread': 0, 'read': 0},
    'get_dashboard_data': {'published_count': 0, 'drafts': [], 'pending': [],
                           'published_blogs': [], 'total_blogs': 0,
                           'categories': [], 'recent_activity': []},
    'get_admin_dashboard_data': {'published_count': 0, 'drafts': [],
                                 'pending': [], 'published_blogs': [],
                                 'total_blogs': 0, 'categories': [],
                                 'recent_activity': []},

    # --- writes ---
    'save_newsletter_subscriber': ('doc-1', True),
    'create_draft': 'blog-1',
    'create_comment': 'comment-1',
    'save_gallery_image': 'image-1',
    'save_contact_submission': 'contact-1',
    'update_blog_status': True,
    'update_blog_content': True,
    'update_user_profile': True,
    'delete_gallery_image': True,
    'is_slug_available': True,
}


@pytest.fixture
def mock_db(monkeypatch):
    """Replace the data layer with a mock for every module that holds one.

    Route modules bind ``db_service = FirestoreService()`` at import time, so
    patching the class is too late -- the instances already exist. This patches
    the bound attribute on each module that has one.
    """
    import importlib

    db = MagicMock(name='db_service')
    for method, value in _DB_RETURN_SHAPES.items():
        getattr(db, method).return_value = value

    module_names = (
        'app.routes.auth', 'app.routes.blog_routes', 'app.routes.site_routes',
        'app.routes.activity_routes', 'app.routes.analytics_routes',
        'app.routes.blogs_listing_routes', 'app.routes.gallery_routes',
        'app.routes.leads_routes', 'app.routes.schedule_routes',
        'app.routes.settings_routes', 'app.routes.user_mgmt',
        'app.routes.newsletter_routes', 'app.routes.optimization_routes',
    )
    for name in module_names:
        try:
            module = importlib.import_module(name)
        except ImportError:
            continue
        for attribute in ('db_service', '_db', 'firestore'):
            if hasattr(module, attribute):
                monkeypatch.setattr(module, attribute, db)

    # Several modules construct FirestoreService() inside a view from a
    # module-level import. Patching the class where it is *defined* does not
    # rebind those already-imported names, so each importer is patched too --
    # including app.__init__, whose context processor builds one per request.
    factory = lambda: db          # noqa: E731  (deliberately trivial)
    monkeypatch.setattr(
        'app.firebase.firestore_service.FirestoreService', factory
    )
    for name in module_names + ('app', 'app.scheduler',
                                'app.services.google_sheets_service'):
        try:
            module = importlib.import_module(name)
        except ImportError:
            continue
        if hasattr(module, 'FirestoreService'):
            monkeypatch.setattr(module, 'FirestoreService', factory)
    return db
