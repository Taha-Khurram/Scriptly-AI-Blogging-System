"""Local durable store for session records and rate-limit counters.

Why SQLite and not Redis
------------------------
Both of these need a store that is *shared between the worker threads of one
instance* and *fast on every single request*. Redis satisfies both but is a
service to run and pay for. Firestore is already in the stack and satisfies
neither: measured on this deployment a Firestore round trip costs 0.5-3.5 s, so
reading a session on every request would add that to every request -- undoing
the whole point of having a session store.

SQLite is the remaining option that needs no new infrastructure and no new
dependency (it is in the standard library). In WAL mode it supports one writer
and many concurrent readers, which is exactly the shape of this workload:
reads on every request, writes only on login, logout and an occasional
last-seen touch. Latency is a local disk write -- microseconds, not the
hundreds of milliseconds a network round trip costs here.

The limitation, stated plainly
------------------------------
**This store is per host.** WAL makes it correct across the threads *and* the
worker processes of one machine, so the shipped ``gunicorn --workers N
--threads 8`` configuration is fine. It is *not* shared between separate
instances or containers: two instances would each keep their own sessions (a
user would bounce between them) and their own rate-limit counters (the
effective limit multiplied by the instance count). :func:`warn_if_not_shared`
logs this loudly at startup.

If the deployment ever grows past one instance, the fix is contained: both
consumers of this module go through a narrow interface -- :class:`SqliteStore`
for sessions and :class:`~app.core.ratelimit_store.SqliteLimiterStorage` for
counters -- so swapping in a shared backend is a new implementation of those
two, not a change to any call site.

Durability
----------
The file lives outside the application tree (``instance/`` by default), so it
is not served by WhiteNoise and not swept up by a deploy that replaces the
source. On a platform with an ephemeral filesystem the file is still lost when
the container is replaced, which logs every user out; they are re-authenticated
silently by the frontend, which holds the Firebase credential and re-posts it to
``/api/auth/verify``. Rate-limit counters resetting on deploy is harmless.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time

logger = logging.getLogger(__name__)

# Long enough to ride out a slow write on a contended disk, short enough that a
# genuinely stuck lock surfaces as an error instead of hanging a worker thread.
_BUSY_TIMEOUT_MS = 5000

# Expired rows are deleted opportunistically rather than by a scheduled job, so
# there is no second moving part to fail. Sweeping on every request would be a
# write per request, so it is rate-limited to this interval per process.
_SWEEP_INTERVAL_SECONDS = 300

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS sessions (
        sid          TEXT PRIMARY KEY,
        user_id      TEXT,
        payload      BLOB    NOT NULL,
        created_at   REAL    NOT NULL,
        last_seen_at REAL    NOT NULL,
        expires_at   REAL    NOT NULL
    )
    """,
    # Revoking every session belonging to one user is the whole reason to keep
    # sessions server-side, and it is a lookup by user rather than by sid.
    "CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)",
    # Supports the expiry sweep without scanning the table.
    "CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at)",
    """
    CREATE TABLE IF NOT EXISTS rate_limits (
        key        TEXT PRIMARY KEY,
        count      INTEGER NOT NULL,
        expires_at REAL    NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_rate_limits_expiry ON rate_limits(expires_at)",
    """
    CREATE TABLE IF NOT EXISTS cache_entries (
        key        TEXT PRIMARY KEY,
        value      BLOB NOT NULL,
        expires_at REAL NOT NULL
    )
    """,
    # Supports both the expiry sweep and the bounded-size eviction.
    "CREATE INDEX IF NOT EXISTS idx_cache_expiry ON cache_entries(expires_at)",
)

# Ceiling on cached rows. Every writer sets a TTL and the sweep removes expired
# entries, so growth is already bounded in normal operation -- this is the
# backstop against a runaway key pattern (an unbounded cache key derived from
# user input, say) filling the disk instead of merely wasting memory.
_CACHE_MAX_ENTRIES = 5000


class SqliteStore:
    """One SQLite file, one connection per thread.

    A ``sqlite3.Connection`` may not be shared across threads unless the
    library is in serialized mode *and* every access is serialised by hand --
    which would funnel all eight worker threads through one lock and throw away
    WAL's concurrent-reader support. A connection per thread is the standard
    arrangement and the one WAL is designed for.

    Configured once from the application factory and never replaced, because
    modules import the singleton at module scope.
    """

    def __init__(self):
        self._path = None
        self._local = threading.local()
        self._configured = False
        self._last_sweep = 0.0
        self._sweep_lock = threading.Lock()

    # --- lifecycle -------------------------------------------------------

    def configure(self, path):
        """Point the store at ``path`` and create the schema if needed."""
        directory = os.path.dirname(os.path.abspath(path))
        if directory:
            os.makedirs(directory, exist_ok=True)

        self._path = path
        # Drop any connection a thread opened against a previous path. Only
        # relevant to tests, which reconfigure between cases.
        self._local = threading.local()

        conn = self._connect()
        with conn:
            for statement in _SCHEMA:
                conn.execute(statement)
        self._configured = True
        logger.info('Session/rate-limit store: SQLite at %s', path)

    @property
    def configured(self):
        return self._configured

    @property
    def path(self):
        return self._path

    def _connect(self):
        conn = sqlite3.connect(
            self._path,
            timeout=_BUSY_TIMEOUT_MS / 1000.0,
            isolation_level=None,   # explicit transactions; see _write()
        )
        # WAL is what allows readers to proceed during a write. It persists on
        # the database file, so setting it on every connection is a no-op after
        # the first -- but harmless and keeps the guarantee local to this code.
        conn.execute('PRAGMA journal_mode=WAL')
        # NORMAL trades a fsync per commit for a fsync per checkpoint. The
        # worst case on power loss is losing the last few session touches,
        # which is not data worth a fsync on every request.
        conn.execute('PRAGMA synchronous=NORMAL')
        conn.execute('PRAGMA busy_timeout=%d' % _BUSY_TIMEOUT_MS)
        conn.execute('PRAGMA foreign_keys=ON')
        return conn

    def connection(self):
        """This thread's connection, opening one on first use."""
        conn = getattr(self._local, 'conn', None)
        if conn is None:
            if not self._path:
                raise RuntimeError(
                    'SqliteStore.configure() has not been called; the '
                    'application factory does this at startup.'
                )
            conn = self._local.conn = self._connect()
        return conn

    def close(self):
        """Close this thread's connection. For tests and shutdown hooks."""
        conn = getattr(self._local, 'conn', None)
        if conn is not None:
            try:
                conn.close()
            finally:
                self._local.conn = None

    # --- transactions ----------------------------------------------------

    def read(self, sql, params=()):
        return self.connection().execute(sql, params).fetchall()

    def read_one(self, sql, params=()):
        return self.connection().execute(sql, params).fetchone()

    def write(self, sql, params=()):
        """Run one statement in its own immediate transaction."""
        with self.transaction() as conn:
            conn.execute(sql, params)

    def transaction(self):
        """A write transaction that takes the write lock up front.

        ``BEGIN IMMEDIATE`` rather than the default deferred begin: a
        read-then-write sequence under a deferred transaction can fail with
        SQLITE_BUSY at upgrade time after the read has already succeeded, which
        is exactly the shape of the rate-limit increment. Taking the lock first
        turns that race into a bounded wait governed by ``busy_timeout``.
        """
        return _Transaction(self.connection())

    # --- maintenance -----------------------------------------------------

    def sweep_expired(self, force=False):
        """Delete expired rows, at most once per interval per process."""
        now = time.time()
        if not force:
            with self._sweep_lock:
                if now - self._last_sweep < _SWEEP_INTERVAL_SECONDS:
                    return 0
                self._last_sweep = now

        try:
            with self.transaction() as conn:
                sessions = conn.execute(
                    'DELETE FROM sessions WHERE expires_at <= ?', (now,)
                ).rowcount
                limits = conn.execute(
                    'DELETE FROM rate_limits WHERE expires_at <= ?', (now,)
                ).rowcount
                entries = conn.execute(
                    'DELETE FROM cache_entries WHERE expires_at <= ?', (now,)
                ).rowcount

                # Bounded size, checked only here rather than on every write.
                # Evicting by soonest expiry rather than by least-recently-used:
                # a cache entry's remaining TTL is the closest thing this store
                # has to a value's remaining usefulness, and it needs no extra
                # per-read write to maintain.
                over = conn.execute(
                    'SELECT COUNT(*) FROM cache_entries'
                ).fetchone()[0] - _CACHE_MAX_ENTRIES
                if over > 0:
                    entries += conn.execute(
                        'DELETE FROM cache_entries WHERE key IN ('
                        '  SELECT key FROM cache_entries '
                        '  ORDER BY expires_at ASC LIMIT ?)',
                        (over,),
                    ).rowcount or 0
                    logger.info('Evicted %d cache entries over the %d cap',
                                over, _CACHE_MAX_ENTRIES)

            removed = (sessions or 0) + (limits or 0) + (entries or 0)
            if removed:
                logger.debug('Swept %d expired store rows', removed)
            return removed
        except sqlite3.Error:
            # Housekeeping must never fail a request.
            logger.warning('Store sweep failed', exc_info=True)
            return 0

    def stats(self):
        try:
            sessions = self.read_one('SELECT COUNT(*) FROM sessions')[0]
            limits = self.read_one('SELECT COUNT(*) FROM rate_limits')[0]
            entries = self.read_one('SELECT COUNT(*) FROM cache_entries')[0]
            size = os.path.getsize(self._path) if self._path else 0
            return {'backend': 'sqlite', 'path': self._path,
                    'sessions': sessions, 'rate_limit_keys': limits,
                    'cache_entries': entries, 'file_bytes': size}
        except (sqlite3.Error, OSError) as exc:
            return {'backend': 'sqlite', 'path': self._path, 'error': str(exc)}

    def healthy(self):
        try:
            self.read_one('SELECT 1')
            return True
        except sqlite3.Error:
            return False


class _Transaction:
    """Context manager for one ``BEGIN IMMEDIATE`` .. ``COMMIT``/``ROLLBACK``.

    Nesting-safe: if the connection is already inside a transaction this joins
    it and leaves the commit to whoever opened it. Without that, a nested use
    would either fail on the second ``BEGIN`` or -- worse -- have the inner
    block's exit commit the outer block's half-finished work.
    """

    def __init__(self, conn):
        self._conn = conn
        self._owns = False

    def __enter__(self):
        if not self._conn.in_transaction:
            self._conn.execute('BEGIN IMMEDIATE')
            self._owns = True
        return self._conn

    def __exit__(self, exc_type, exc, tb):
        if not self._owns:
            return False
        if exc_type is None:
            self._conn.execute('COMMIT')
        else:
            self._conn.execute('ROLLBACK')
        return False


def default_path(app):
    """Where the database lives unless configured otherwise.

    Flask's ``instance_path`` is the conventional home for per-deployment
    state, and it sits *outside* ``app/``, so WhiteNoise never serves it and a
    source deploy does not overwrite it.
    """
    return os.path.join(app.instance_path, 'scriptly.db')


def register_sweep(app):
    """Delete expired rows opportunistically, after a response is sent.

    Registered on the app rather than called from the session interface,
    because the two tables fill up on different traffic. Rate-limit counters
    are written by *anonymous* public requests, which create no session -- so a
    sweep that only ran on session writes would let those rows accumulate
    indefinitely on the busiest part of the site.

    ``sweep_expired`` throttles itself to one pass per interval per process, so
    this hook is a cheap timestamp comparison on all but a handful of requests.
    """

    @app.teardown_request
    def _sweep(exc=None):
        try:
            store.sweep_expired()
        except Exception:  # pragma: no cover - housekeeping must never raise
            logger.debug('Store sweep skipped', exc_info=True)


def warn_if_not_shared(app):
    """Say clearly, at startup, what this store does and does not cover."""
    workers = 0
    for name in ('WEB_CONCURRENCY', 'GUNICORN_WORKERS'):
        try:
            workers = max(workers, int(os.getenv(name, '0')))
        except ValueError:
            pass

    app.logger.info(
        'Sessions and rate limits are stored in SQLite at %s. Shared across '
        'the threads and worker processes of THIS instance (WAL), not across '
        'separate instances. With more than one instance, sessions would not '
        'be shared and the effective rate limit would be multiplied by the '
        'instance count.',
        store.path,
    )
    if workers > 1:
        app.logger.info(
            '%d gunicorn workers detected on this host. WAL handles that '
            'correctly -- all workers share the one file.', workers,
        )


# Module-level singleton. Imported directly by the session interface and the
# rate-limit storage, so it must exist at import time and be *configured*,
# never replaced, at startup.
store = SqliteStore()
