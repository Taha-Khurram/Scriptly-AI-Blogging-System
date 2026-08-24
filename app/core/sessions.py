"""Server-side sessions.

What was wrong with the default
-------------------------------
Flask's default session is a *client-side* signed cookie: the whole session --
``user_id``, ``user_role``, ``logged_in`` -- is serialised into the cookie and
handed to the browser. It is signed, so a user cannot forge a role, but three
properties follow from the data living on the client and none of them are
acceptable for an admin dashboard:

* **A session cannot be revoked.** Deleting a user, or demoting an admin to
  editor, changes a Firestore document and nothing else. The cookie in that
  person's browser keeps asserting ``user_role: ADMIN`` until it expires --
  up to ``SESSION_TIMEOUT_MINUTES`` (eight hours by default) of continued
  administrative access after their access was removed. There was no mechanism
  to end a session from the server at all.
* **Signing out is advisory.** ``session.clear()`` asks the browser to drop the
  cookie. A copy captured beforehand stays valid for its full lifetime.
* **Role is carried by the client.** The role is only as trustworthy as the
  signature and the secret. Rotating ``SECRET_KEY`` is the sole revocation
  primitive, and it logs out every user at once.

What this replaces it with
--------------------------
The cookie now carries one thing: an opaque 256-bit random session id. Every
other value lives in a row in the local store, so the server is the authority
on what a session contains and whether it exists at all.

That buys the four things the cookie could not do:

* **Revocation.** :func:`revoke_user_sessions` deletes every session belonging
  to a user. Wired into role changes and account deletion, so removing access
  removes it now rather than in eight hours.
* **Fixation defence.** :func:`rotate_session` issues a new id while keeping
  the payload, called on every successful sign-in. An attacker who fixes a
  victim's session id before login holds an id that stops existing the moment
  the victim authenticates.
* **An absolute lifetime** alongside the sliding idle timeout, so a session
  that is kept warm by activity still ends. A sliding window alone never
  expires for an active client.
* **Visibility.** Active sessions are rows: countable, inspectable, expirable.

The id is signed with ``SECRET_KEY`` on top of being random. That is not for
confidentiality -- it means a tampered or fabricated cookie is rejected by a
signature check rather than by a database lookup, so garbage never reaches the
store, and a ``SECRET_KEY`` rotation still invalidates everything.

Cost
----
One indexed SQLite lookup per request (microseconds) and a write only on login,
logout, an actual change to the session, or an idle-timer touch at most once
per ``SESSION_TOUCH_SECONDS``. Without that touch throttle a sliding-expiry
session store writes on every request; with it, a browsing user writes once a
minute.
"""
from __future__ import annotations

import logging
import secrets
import time

from flask import session as flask_session
from flask.json.tag import TaggedJSONSerializer
from flask.sessions import SessionInterface, SessionMixin
from itsdangerous import BadSignature, URLSafeSerializer
from werkzeug.datastructures import CallbackDict

from app.core.store import store

logger = logging.getLogger(__name__)

# 32 bytes from `secrets` -- 256 bits of entropy, so the id is not guessable
# and collisions are not a consideration.
_SID_BYTES = 32

# Flask's own session serialiser. Chosen over pickle deliberately: the payload
# is written by us but read back into a live process, and a JSON-shaped
# serialiser has no code-execution surface if the file is ever tampered with.
# Chosen over plain JSON because it round-trips the datetimes, tuples and sets
# Flask sessions are allowed to hold.
_serializer = TaggedJSONSerializer()


class ServerSession(CallbackDict, SessionMixin):
    """A session whose contents live in the store, not in the cookie."""

    def __init__(self, initial=None, sid=None, is_new=False,
                 created_at=None, last_seen_at=None):
        def _on_update(_self):
            _self.modified = True

        super().__init__(initial or {}, _on_update)
        self.sid = sid
        self.is_new = is_new
        self.created_at = created_at
        self.last_seen_at = last_seen_at
        self.modified = False
        # Set when the id must change on this response: a fresh login, or a
        # rotation requested by application code.
        self.rotate = False
        # Set when the session must be destroyed server-side on this response.
        self.destroy = False


class SqliteSessionInterface(SessionInterface):
    """Reads and writes :class:`ServerSession` rows via :data:`store`."""

    session_class = ServerSession
    # A brand-new empty session is not persisted and gets no cookie: an
    # anonymous visitor to a public page must not cause a write or be tracked.
    pickle_based = False

    def __init__(self, idle_lifetime, absolute_lifetime, touch_seconds, secret_key):
        self.idle_lifetime = idle_lifetime
        self.absolute_lifetime = absolute_lifetime
        self.touch_seconds = touch_seconds
        self._signer = URLSafeSerializer(secret_key, salt='scriptly-session')

    # --- helpers ---------------------------------------------------------

    def _new_sid(self):
        return secrets.token_urlsafe(_SID_BYTES)

    def _sign(self, sid):
        return self._signer.dumps(sid)

    def _unsign(self, raw):
        try:
            return self._signer.loads(raw)
        except BadSignature:
            # A cookie from a previous SECRET_KEY, or a tampered one. Either
            # way it names no session we will honour.
            return None

    def _empty(self):
        return self.session_class(is_new=True)

    # --- SessionInterface ------------------------------------------------

    def open_session(self, app, request):
        raw = request.cookies.get(app.config['SESSION_COOKIE_NAME'])
        if not raw:
            return self._empty()

        sid = self._unsign(raw)
        if not sid:
            return self._empty()

        try:
            row = store.read_one(
                'SELECT payload, created_at, last_seen_at, expires_at '
                'FROM sessions WHERE sid = ?',
                (sid,),
            )
        except Exception:
            # The store being unavailable must not hand out a *new* session to
            # someone who has a valid one -- that would silently sign them out
            # and, worse, could be induced. Fail closed but loudly.
            logger.exception('Session store read failed; treating as anonymous')
            return self._empty()

        if row is None:
            # A signed id naming no row: already revoked, expired and swept, or
            # from a store that has been reset. Not an error.
            return self._empty()

        payload, created_at, last_seen_at, expires_at = row
        now = time.time()

        # Idle expiry, enforced by the store rather than by a value inside the
        # session. A client cannot extend its own idle window.
        if expires_at <= now:
            self._delete(sid)
            return self._empty()

        # Absolute expiry: an active client still gets logged out eventually.
        if self.absolute_lifetime and created_at + self.absolute_lifetime <= now:
            logger.info('Session reached its absolute lifetime')
            self._delete(sid)
            return self._empty()

        try:
            data = _serializer.loads(payload.decode('utf-8'))
        except Exception:
            # A payload written by an older code version whose shape has
            # changed. Discard rather than crash every request for this user.
            logger.warning('Discarding undecodable session payload')
            self._delete(sid)
            return self._empty()

        return self.session_class(
            data, sid=sid, is_new=False,
            created_at=created_at, last_seen_at=last_seen_at,
        )

    def save_session(self, app, session, response):
        name = app.config['SESSION_COOKIE_NAME']
        domain = self.get_cookie_domain(app)
        path = self.get_cookie_path(app)

        # Explicit destruction (logout) or an emptied session.
        if session.destroy or (not session and not session.is_new):
            if session.sid:
                self._delete(session.sid)
            response.delete_cookie(name, domain=domain, path=path)
            # A logout response must never be reused from a cache.
            response.vary.add('Cookie')
            return

        # Nothing to store and nothing stored: no write, no cookie. This is the
        # anonymous-visitor path on the public site, which is most of the
        # traffic, and it must stay free.
        if not session:
            return

        now = time.time()
        rotating = session.rotate or session.sid is None
        old_sid = session.sid

        if rotating:
            session.sid = self._new_sid()
            session.created_at = now
            session.rotate = False

        created_at = session.created_at or now
        expires_at = now + self.idle_lifetime

        # Write only when there is a reason to. A sliding-expiry store
        # otherwise writes on every single request just to move the expiry.
        touch_due = (
            session.last_seen_at is None
            or now - session.last_seen_at >= self.touch_seconds
        )
        if not (session.modified or rotating or touch_due):
            # Still re-send the cookie so its own max-age slides forward.
            self._set_cookie(app, response, session, expires_at)
            return

        payload = _serializer.dumps(dict(session)).encode('utf-8')
        user_id = session.get('user_id')

        try:
            with store.transaction() as conn:
                if rotating and old_sid:
                    # Rotation is a move, not a copy: the old id must stop
                    # working immediately or the fixation defence is nominal.
                    conn.execute('DELETE FROM sessions WHERE sid = ?', (old_sid,))
                conn.execute(
                    'INSERT INTO sessions '
                    '  (sid, user_id, payload, created_at, last_seen_at, expires_at) '
                    'VALUES (?, ?, ?, ?, ?, ?) '
                    'ON CONFLICT(sid) DO UPDATE SET '
                    '  user_id = excluded.user_id, '
                    '  payload = excluded.payload, '
                    '  last_seen_at = excluded.last_seen_at, '
                    '  expires_at = excluded.expires_at',
                    (session.sid, user_id, payload, created_at, now, expires_at),
                )
        except Exception:
            # Failing the write would 500 a request whose real work already
            # succeeded. The user keeps the session they had; the next request
            # tries again.
            logger.exception('Session store write failed')
            return

        session.last_seen_at = now
        session.modified = False
        self._set_cookie(app, response, session, expires_at)

    def _set_cookie(self, app, response, session, expires_at):
        response.set_cookie(
            app.config['SESSION_COOKIE_NAME'],
            self._sign(session.sid),
            expires=expires_at,
            httponly=self.get_cookie_httponly(app),
            secure=self.get_cookie_secure(app),
            samesite=self.get_cookie_samesite(app),
            domain=self.get_cookie_domain(app),
            path=self.get_cookie_path(app),
        )
        # The response body depends on which session presented itself, so a
        # shared cache must not serve one user's page to another.
        response.vary.add('Cookie')

    def _delete(self, sid):
        try:
            store.write('DELETE FROM sessions WHERE sid = ?', (sid,))
        except Exception:
            logger.exception('Session delete failed')


# ---------------------------------------------------------------------------
# Application-facing operations
# ---------------------------------------------------------------------------

def rotate_session():
    """Issue a new session id on this response, keeping the contents.

    Called immediately after authentication succeeds. Session fixation works by
    getting a victim to browse with an id the attacker already knows and then
    waiting for them to log in; if the id changes at that moment, what the
    attacker holds is worthless. This is the standard defence and it is only
    possible with server-side sessions -- there is no id to rotate when the
    cookie *is* the session.
    """
    # Attribute check rather than isinstance: `flask_session` is a proxy, and
    # under SESSION_BACKEND=cookie it is a SecureCookieSession with no id to
    # rotate. A no-op there is correct -- there is nothing to rotate when the
    # cookie *is* the session -- and the startup warning already says so.
    if hasattr(flask_session, 'rotate'):
        flask_session.rotate = True
        flask_session.modified = True


def destroy_session():
    """End this session server-side, not just in the browser.

    ``session.clear()`` alone empties the local copy and asks the browser to
    drop the cookie. This deletes the row, so a cookie captured earlier stops
    working -- which is what signing out is supposed to mean.
    """
    if hasattr(flask_session, 'destroy'):
        flask_session.destroy = True
    flask_session.clear()
    flask_session.modified = True


def revoke_user_sessions(user_id, except_sid=None):
    """Delete every session belonging to ``user_id``. Returns the count.

    The reason server-side sessions are worth their cost. Called when a user is
    deleted or their role changes: without it, a demoted admin keeps
    administrative access for as long as their cookie remains valid, because
    the role is asserted by the cookie and nothing consults the database again.

    ``except_sid`` spares one session, for an admin changing their own role who
    should not be signed out by their own action.
    """
    if not user_id:
        return 0
    try:
        if except_sid:
            with store.transaction() as conn:
                cursor = conn.execute(
                    'DELETE FROM sessions WHERE user_id = ? AND sid != ?',
                    (user_id, except_sid),
                )
                removed = cursor.rowcount or 0
        else:
            with store.transaction() as conn:
                cursor = conn.execute(
                    'DELETE FROM sessions WHERE user_id = ?', (user_id,)
                )
                removed = cursor.rowcount or 0
        if removed:
            logger.info(
                'Revoked %d session(s)', removed, extra={'user_id': user_id}
            )
        return removed
    except Exception:
        logger.exception('Session revocation failed', extra={'user_id': user_id})
        return 0


def count_user_sessions(user_id):
    """How many live sessions this user has. For the account screen."""
    if not user_id:
        return 0
    try:
        row = store.read_one(
            'SELECT COUNT(*) FROM sessions WHERE user_id = ? AND expires_at > ?',
            (user_id, time.time()),
        )
        return row[0] if row else 0
    except Exception:
        logger.exception('Session count failed')
        return 0


def current_sid():
    """This request's session id, or ``None``."""
    return getattr(flask_session, 'sid', None)


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------

def init_sessions(app):
    """Install the server-side session interface.

    ``SESSION_BACKEND=cookie`` keeps Flask's built-in client-side sessions. It
    exists as an escape hatch, not as a supported mode: it cannot revoke.
    """
    backend = (app.config.get('SESSION_BACKEND') or 'sqlite').lower()

    if backend == 'cookie':
        app.logger.warning(
            'SESSION_BACKEND=cookie: sessions are client-side signed cookies '
            'and CANNOT be revoked. A deleted or demoted user keeps their '
            'access until the cookie expires.'
        )
        return

    idle = app.config['PERMANENT_SESSION_LIFETIME'].total_seconds()
    absolute = app.config.get('SESSION_ABSOLUTE_LIFETIME')
    absolute = absolute.total_seconds() if absolute else 0

    if absolute and absolute < idle:
        # An absolute lifetime shorter than the idle window makes the idle
        # window unreachable, which is almost always a misconfiguration rather
        # than an intent.
        app.logger.warning(
            'SESSION_ABSOLUTE_LIFETIME (%.0fs) is shorter than the idle '
            'timeout (%.0fs), so the idle timeout can never be reached.',
            absolute, idle,
        )

    app.session_interface = SqliteSessionInterface(
        idle_lifetime=idle,
        absolute_lifetime=absolute,
        touch_seconds=app.config.get('SESSION_TOUCH_SECONDS', 60),
        secret_key=app.config['SECRET_KEY'],
    )
    app.logger.info(
        'Sessions: server-side (SQLite). idle=%.0fs absolute=%s touch=%ss',
        idle, ('%.0fs' % absolute) if absolute else 'off',
        app.config.get('SESSION_TOUCH_SECONDS', 60),
    )
