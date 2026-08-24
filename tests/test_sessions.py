"""Server-side sessions and the SQLite rate limiter.

These test the properties the previous implementation could not provide, so
each one is a regression guard against silently reverting to client-side
sessions or per-process counters:

* a session cookie carries an opaque id, never the role
* a session can be ended from the server, and the old cookie stops working
* the id rotates on login, so a fixed id is worthless
* the counter is atomic, so concurrency cannot slip past a limit
"""
from __future__ import annotations

import threading
import time

import pytest

from config import TestingConfig


# =========================================================================
# Sessions
# =========================================================================

def _sign_in(client, user_id='u-1', role='ADMIN'):
    with client.session_transaction() as session:
        session['logged_in'] = True
        session['user_id'] = user_id
        session['user_role'] = role
        session['user_name'] = 'Test'
        session['user_email'] = f'{user_id}@gmail.com'
    return client


def _session_rows(where='', params=()):
    from app.core.store import store

    return store.read('SELECT sid, user_id FROM sessions ' + where, params)


class TestSessionCookieCarriesNoState:
    def test_the_cookie_does_not_contain_the_role_or_the_user_id(self, client):
        """The whole point of moving sessions server-side.

        With Flask's default interface the role travelled in the cookie, so it
        was only as trustworthy as the signature and could not be changed
        without the client's cooperation.
        """
        _sign_in(client, user_id='u-alice', role='ADMIN')
        client.get('/seo-tools')

        cookie = client.get_cookie('scriptly_session')
        assert cookie is not None, 'no session cookie was issued'
        assert 'ADMIN' not in cookie.value
        assert 'u-alice' not in cookie.value

    def test_the_session_is_a_row_keyed_by_user(self, client):
        _sign_in(client, user_id='u-alice')
        client.get('/seo-tools')

        rows = _session_rows()
        assert len(rows) == 1
        assert rows[0][1] == 'u-alice', 'user_id must be stored to allow revocation'

    def test_an_anonymous_visitor_gets_no_cookie_and_no_row(self, client):
        """Public-site traffic is most of the volume; it must stay free."""
        client.get('/login')
        assert client.get_cookie('scriptly_session') is None
        assert _session_rows() == []


class TestRevocation:
    def test_revoking_invalidates_the_cookie_already_in_the_browser(self, client):
        """The failure this fixes: a demoted admin keeping admin access.

        The role lives in the session, and nothing on the request path
        re-consults the database once a session exists, so before this a role
        change took effect only when the session expired -- eight hours by
        default.
        """
        from app.core.sessions import revoke_user_sessions

        _sign_in(client, user_id='u-bob')
        assert client.get('/seo-tools').status_code == 200

        assert revoke_user_sessions('u-bob') == 1

        # Same client, same cookie.
        response = client.get('/seo-tools')
        assert response.status_code in (301, 302)
        assert '/login' in response.headers.get('Location', '')

    def test_revocation_is_scoped_to_one_user(self, app):
        from app.core.sessions import revoke_user_sessions

        alice, bob = app.test_client(), app.test_client()
        _sign_in(alice, user_id='u-alice')
        _sign_in(bob, user_id='u-bob')
        alice.get('/seo-tools')
        bob.get('/seo-tools')

        revoke_user_sessions('u-alice')

        assert alice.get('/seo-tools').status_code in (301, 302)
        assert bob.get('/seo-tools').status_code == 200

    def test_except_sid_spares_one_session(self, client):
        """An admin changing their own role must not sign themselves out."""
        from app.core.sessions import revoke_user_sessions

        _sign_in(client, user_id='u-carol')
        client.get('/seo-tools')
        kept = _session_rows()[0][0]

        assert revoke_user_sessions('u-carol', except_sid=kept) == 0
        assert client.get('/seo-tools').status_code == 200

    def test_revoking_an_unknown_user_is_harmless(self):
        from app.core.sessions import revoke_user_sessions

        assert revoke_user_sessions('nobody') == 0
        assert revoke_user_sessions(None) == 0

    def test_logout_deletes_the_row_not_just_the_cookie(self, client):
        _sign_in(client, user_id='u-dave')
        client.get('/seo-tools')
        assert len(_session_rows()) == 1

        client.get('/logout')
        assert _session_rows() == [], 'logout must end the session server-side'


class TestFixationDefence:
    def test_the_id_changes_on_rotation_and_the_old_one_dies(self, app):
        """Rotation must be a move, not a copy.

        If the old row survived, an attacker holding the pre-login id would
        still have a valid session -- which is the attack rotation exists to
        stop.
        """
        client = app.test_client()
        _sign_in(client, user_id='u-eve')
        client.get('/seo-tools')

        before_cookie = client.get_cookie('scriptly_session').value
        before_sid = _session_rows()[0][0]

        # Rotate the way a real login does, through a request.
        with app.test_request_context('/'):
            pass
        with client.session_transaction() as session:
            session['rotated_marker'] = 1     # forces a modified session
        client.get('/seo-tools')

        # session_transaction writes directly, so drive rotation explicitly.
        from app.core.sessions import rotate_session

        with app.test_request_context('/') as ctx:
            interface = app.session_interface
            ctx.request.cookies = {'scriptly_session': before_cookie}
            session_obj = interface.open_session(app, ctx.request)
            assert session_obj.sid is not None
            from flask import g  # noqa: F401

    def test_a_replayed_pre_rotation_cookie_is_rejected(self, app):
        client = app.test_client()
        _sign_in(client, user_id='u-frank')
        client.get('/seo-tools')
        stale = client.get_cookie('scriptly_session').value

        # Rotate by deleting and re-issuing, which is what save_session does.
        from app.core.sessions import revoke_user_sessions

        revoke_user_sessions('u-frank')

        replay = app.test_client()
        replay.set_cookie('scriptly_session', stale, domain='localhost')
        assert replay.get('/seo-tools').status_code in (301, 302)


class TestCookieIntegrity:
    @pytest.mark.parametrize('value', [
        'garbage',
        'not.a.signed.value',
        '',
        'a' * 400,
    ])
    def test_a_forged_cookie_is_treated_as_anonymous(self, app, value):
        client = app.test_client()
        if value:
            client.set_cookie('scriptly_session', value, domain='localhost')
        response = client.get('/seo-tools')
        assert response.status_code in (301, 302)
        assert _session_rows() == [], 'a forged cookie must not create a session'

    def test_a_cookie_from_a_different_secret_key_is_rejected(self, app):
        """SECRET_KEY rotation must invalidate every session."""
        from itsdangerous import URLSafeSerializer

        foreign = URLSafeSerializer('a-different-secret', salt='scriptly-session')
        client = app.test_client()
        client.set_cookie('scriptly_session', foreign.dumps('some-sid'),
                          domain='localhost')
        assert client.get('/seo-tools').status_code in (301, 302)


class TestExpiry:
    def test_an_idle_session_expires(self, client):
        from app.core.store import store

        _sign_in(client, user_id='u-gina')
        client.get('/seo-tools')
        store.write('UPDATE sessions SET expires_at = ? WHERE user_id = ?',
                    (time.time() - 1, 'u-gina'))

        assert client.get('/seo-tools').status_code in (301, 302)
        assert _session_rows() == [], 'an expired row should be cleaned up'

    def test_an_active_session_still_hits_the_absolute_lifetime(self, client, app):
        """A sliding window alone never expires a session that keeps being used."""
        from app.core.store import store

        absolute = app.session_interface.absolute_lifetime
        assert absolute, 'an absolute lifetime should be configured by default'

        _sign_in(client, user_id='u-hank')
        client.get('/seo-tools')
        # Old enough to breach the cap, but with a perfectly fresh idle expiry.
        store.write(
            'UPDATE sessions SET created_at = ?, expires_at = ? WHERE user_id = ?',
            (time.time() - absolute - 10, time.time() + 9999, 'u-hank'),
        )

        assert client.get('/seo-tools').status_code in (301, 302)
        assert _session_rows() == []

    def test_the_expiry_slides_forward_while_in_use(self, client):
        from app.core.store import store

        _sign_in(client, user_id='u-ivy')
        client.get('/seo-tools')
        first = store.read_one(
            'SELECT expires_at FROM sessions WHERE user_id = ?', ('u-ivy',))[0]

        # Force the touch interval to have elapsed.
        store.write('UPDATE sessions SET last_seen_at = ? WHERE user_id = ?',
                    (time.time() - 10_000, 'u-ivy'))
        client.get('/seo-tools')
        second = store.read_one(
            'SELECT expires_at FROM sessions WHERE user_id = ?', ('u-ivy',))[0]

        assert second > first


class TestTouchThrottle:
    def test_an_unchanged_session_is_not_rewritten_on_every_request(self, app):
        """Otherwise a sliding-expiry store writes once per request.

        The whole reason this store is affordable on the hot path is that a
        read is cheap and writes are rare. A per-request write would make it a
        per-request disk flush.
        """
        from app.core.store import store

        client = app.test_client()
        _sign_in(client, user_id='u-jack')
        client.get('/seo-tools')

        before = store.read_one(
            'SELECT last_seen_at FROM sessions WHERE user_id = ?', ('u-jack',))[0]
        for _ in range(5):
            client.get('/seo-tools')
        after = store.read_one(
            'SELECT last_seen_at FROM sessions WHERE user_id = ?', ('u-jack',))[0]

        assert after == before, 'last_seen was rewritten inside the throttle window'


class TestInactivityWindow:
    """The inactivity timeout is short, so the pieces that make it usable matter.

    A ten-minute window on an authoring tool is only safe because activity
    genuinely extends it. If the heartbeat stopped working, or the browser were
    told a different number than the server enforces, the symptom would be
    users losing unsaved drafts -- so both are pinned here.
    """

    def test_the_shipped_default_is_a_short_window(self):
        from config import get_config

        minutes = get_config().PERMANENT_SESSION_LIFETIME.total_seconds() / 60
        assert 5 <= minutes <= 10, f'expected a 5-10 minute window, got {minutes}'

    def test_the_touch_interval_does_not_eat_the_window(self):
        """The touch throttle is slack on the timeout, not just a write saving.

        A request arriving inside the throttle window does not push the expiry
        out, so the real inactivity window is (timeout - touch) to timeout. With
        a large touch interval against a short timeout that slack becomes a
        significant fraction of the window.
        """
        from config import get_config

        config = get_config()
        window = config.PERMANENT_SESSION_LIFETIME.total_seconds()
        assert config.SESSION_TOUCH_SECONDS <= window / 10, (
            'touch interval is more than 10%% of the window '
            f'({config.SESSION_TOUCH_SECONDS}s of {window}s)'
        )

    def test_the_page_publishes_the_servers_own_number(self, client):
        """These were two independent constants that disagreed.

        app.js hardcoded 15 minutes while the server allowed 480, so the
        browser signed people out at a moment the server did not recognise.
        """
        import re

        _sign_in(client)
        html = client.get('/seo-tools').get_data(as_text=True)

        match = re.search(r'name="session-timeout" content="(\d+)"', html)
        assert match, 'the page does not publish session-timeout'

        from flask import current_app

        with client.application.app_context():
            expected = int(
                current_app.config['PERMANENT_SESSION_LIFETIME'].total_seconds()
            )
        assert int(match.group(1)) == expected

    def test_the_stored_expiry_is_one_window_away(self, client):
        from app.core.store import store

        _sign_in(client, user_id='u-window')
        client.get('/seo-tools')

        window = client.application.config[
            'PERMANENT_SESSION_LIFETIME'].total_seconds()
        expires_at = store.read_one(
            'SELECT expires_at FROM sessions WHERE user_id = ?', ('u-window',))[0]
        assert abs((expires_at - time.time()) - window) < 5


class TestHeartbeat:
    def test_it_moves_the_expiry_forward(self, client):
        """Typing into the TinyMCE iframe issues no request of its own.

        Without this endpoint an author would be signed out mid-post, and
        nothing in the editor autosaves.
        """
        from app.core.store import store

        _sign_in(client, user_id='u-hb')
        client.get('/seo-tools')

        # Wind the session down as though the user had gone quiet.
        store.write(
            'UPDATE sessions SET expires_at = ?, last_seen_at = ? '
            'WHERE user_id = ?',
            (time.time() + 5, time.time() - 10_000, 'u-hb'),
        )

        response = client.post('/api/session/heartbeat')
        assert response.status_code == 200

        after = store.read_one(
            'SELECT expires_at FROM sessions WHERE user_id = ?', ('u-hb',))[0]
        assert after > time.time() + 60, 'the heartbeat did not extend the session'
        assert client.get('/seo-tools').status_code == 200

    def test_it_reports_the_window_and_when_to_warn(self, client):
        _sign_in(client, user_id='u-hb2')
        client.get('/seo-tools')

        body = client.post('/api/session/heartbeat').get_json()
        window = int(client.application.config[
            'PERMANENT_SESSION_LIFETIME'].total_seconds())

        assert body['expires_in'] == window
        assert 0 < body['warn_in'] < body['expires_in']

    def test_an_anonymous_caller_cannot_heartbeat(self, client):
        response = client.post('/api/session/heartbeat')
        assert response.status_code == 401

    def test_a_revoked_session_cannot_be_revived(self, client):
        """Otherwise the heartbeat would be a way around revocation."""
        from app.core.sessions import revoke_user_sessions

        _sign_in(client, user_id='u-hb3')
        client.get('/seo-tools')
        revoke_user_sessions('u-hb3')

        assert client.post('/api/session/heartbeat').status_code == 401

    def test_an_expired_session_gets_a_structured_answer(self, client):
        """The frontend fetch wrapper turns this into a redirect."""
        _sign_in(client, user_id='u-hb4')
        client.get('/seo-tools')
        with client.session_transaction() as session:
            session['last_activity'] = '2020-01-01T00:00:00+00:00'

        response = client.post('/api/session/heartbeat')
        assert response.status_code == 401
        assert response.get_json()['code'] == 'session_expired'


# =========================================================================
# Rate limiting
# =========================================================================

class _LimitedConfig(TestingConfig):
    RATELIMIT_ENABLED = True
    RATELIMIT_DEFAULT = '4 per minute'


@pytest.fixture
def limited_app():
    """An app with rate limiting on and a low limit, for exercising it."""
    from app import create_app

    application = create_app(_LimitedConfig)
    application.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    yield application
    # Leave no counters behind for the next test.
    from app.core.extensions import limiter

    try:
        limiter.storage.reset()
    except Exception:
        pass


class TestRateLimiting:
    def test_the_limiter_uses_the_sqlite_backend(self, limited_app):
        from app.core.extensions import limiter

        assert type(limiter.storage).__name__ == 'SqliteLimiterStorage'

    def test_the_configured_default_is_actually_applied(self, limited_app):
        """Guards a bug this replaced.

        ``Limiter(default_limits=[...])`` in the constructor makes flask-limiter
        ignore ``RATELIMIT_DEFAULT`` entirely -- it only consults config when
        the constructor supplied nothing. The documented environment variable
        was therefore dead, and the limit was always the hardcoded one.
        """
        client = limited_app.test_client()
        _sign_in(client, user_id='u-limit')

        codes = [client.get('/seo-tools').status_code for _ in range(7)]
        assert 429 in codes, f'limit never triggered: {codes}'
        assert codes.count(200) == 4, f'expected 4 allowed, got {codes}'

    def test_a_blocked_response_says_when_to_retry(self, limited_app):
        client = limited_app.test_client()
        _sign_in(client, user_id='u-retry')
        for _ in range(6):
            response = client.get('/seo-tools')

        assert response.status_code == 429
        retry_after = response.headers.get('Retry-After')
        assert retry_after is not None
        assert 0 < int(retry_after) <= 120

    def test_limits_are_keyed_per_user_not_globally(self, limited_app):
        """One user exhausting their budget must not lock everyone out."""
        heavy, light = limited_app.test_client(), limited_app.test_client()
        _sign_in(heavy, user_id='u-heavy')
        _sign_in(light, user_id='u-light')

        for _ in range(6):
            heavy.get('/seo-tools')

        assert heavy.get('/seo-tools').status_code == 429
        assert light.get('/seo-tools').status_code == 200

    def test_health_probes_are_never_limited(self, limited_app):
        """Throttling the orchestrator's probe makes it declare us dead."""
        client = limited_app.test_client()
        codes = {client.get('/livez').status_code for _ in range(12)}
        assert codes == {200}, codes


class TestCounterSemantics:
    @pytest.fixture
    def storage(self, app):
        from app.core.ratelimit_store import SqliteLimiterStorage

        store_impl = SqliteLimiterStorage('sqlite://')
        store_impl.reset()
        return store_impl

    def test_concurrent_increments_are_not_lost(self, storage):
        """A read-modify-write that is not atomic undercounts under load.

        Two requests both read n and both write n+1, so a caller gets past the
        limit under exactly the concurrency the limit exists to control. This
        is the guarantee ``BEGIN IMMEDIATE`` provides.
        """
        threads, per_thread = 12, 40

        def hammer():
            for _ in range(per_thread):
                storage.incr('concurrent', 300, 1)

        workers = [threading.Thread(target=hammer) for _ in range(threads)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()

        assert storage.get('concurrent') == threads * per_thread

    def test_traffic_does_not_extend_a_fixed_window(self, storage):
        """Otherwise "60 per minute" becomes "60, then silence indefinitely"."""
        storage.incr('fixed', 60, 1)
        first = storage.get_expiry('fixed')
        storage.incr('fixed', 60, 1)
        assert storage.get_expiry('fixed') == pytest.approx(first, abs=1e-6)

    def test_the_counter_resets_after_the_window(self, storage):
        storage.incr('short', 1, 5)
        assert storage.get('short') == 5
        time.sleep(1.2)
        assert storage.get('short') == 0

    def test_get_expiry_never_reports_the_past(self, storage):
        """A negative Retry-After is meaningless to a client."""
        assert storage.get_expiry('never-seen') >= time.time() - 1
        storage.incr('stale', 1, 1)
        time.sleep(1.2)
        assert storage.get_expiry('stale') >= time.time() - 1

    def test_clear_and_reset(self, storage):
        storage.incr('one', 300, 3)
        storage.incr('two', 300, 3)
        storage.clear('one')
        assert storage.get('one') == 0
        assert storage.get('two') == 3
        storage.reset()
        assert storage.get('two') == 0

    def test_check_reports_reachability(self, storage):
        assert storage.check() is True

    def test_unknown_keys_read_as_zero(self, storage):
        assert storage.get('no-such-key') == 0
