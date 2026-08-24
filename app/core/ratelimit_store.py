"""A ``limits`` storage backend on SQLite, registered as the ``sqlite`` scheme.

``limits`` 5.x ships memory, Redis, Memcached and MongoDB backends. Of those,
only ``memory://`` needs no new service -- and ``memory://`` is per process, so
with N gunicorn workers the effective rate limit is N times the configured one.
That is what this application was running: rate limiting nominally on, actually
multiplied by the worker count, and reset on every reload.

Implementing the backend is small because ``Storage`` is a narrow interface --
``incr``, ``get``, ``get_expiry``, ``check``, ``reset``, ``clear`` -- and because
the limiter is configured with the ``fixed-window`` strategy, which needs only
those. The moving-window and sliding-window strategies additionally require
``MovingWindowSupport`` / ``SlidingWindowCounterSupport``; those are not
implemented, so ``limits`` will refuse a strategy this backend cannot honour
rather than silently approximating it. That refusal is the desirable behaviour:
a rate limiter that quietly changes strategy is worse than one that will not
start.

Correctness of the counter
--------------------------
A fixed-window counter is a read-modify-write, so it has to be atomic or two
concurrent requests can both read ``n`` and both write ``n+1``, letting a
caller exceed the limit under exactly the concurrency the limit exists to
control. Every increment therefore runs inside ``BEGIN IMMEDIATE``, which takes
the write lock before the read, so the sequence cannot interleave. With eight
worker threads the contention window is microseconds and ``busy_timeout``
covers it.
"""
from __future__ import annotations

import logging
import sqlite3
import time

from limits.storage import Storage

from app.core.store import store

logger = logging.getLogger(__name__)


class SqliteLimiterStorage(Storage):
    """Fixed-window counters in the local SQLite store.

    Registered under the ``sqlite`` scheme by ``Storage``'s metaclass, so
    ``RATELIMIT_STORAGE_URI = 'sqlite://'`` resolves here. The URI carries no
    path: the file is chosen once by the application factory and shared with
    the session store, so both use one connection per thread instead of two.
    """

    STORAGE_SCHEME = ['sqlite']

    def __init__(self, uri=None, wrap_exceptions=False, **options):
        super().__init__(uri, wrap_exceptions=wrap_exceptions, **options)
        if not store.configured:
            raise RuntimeError(
                'The SQLite store must be configured before the rate limiter. '
                'The application factory does this in _init_infrastructure().'
            )

    @property
    def base_exceptions(self):
        return sqlite3.Error

    # --- Storage interface ------------------------------------------------

    def incr(self, key, expiry, amount=1):
        """Increment ``key``, starting a new window if the old one has passed.

        Returns the counter's new value. The window's expiry is set when the
        window opens and is *not* extended by later increments -- that is what
        makes it a fixed window. Extending it on every hit would turn a
        "60 per minute" limit into "60, then a minute of silence", because a
        client hammering the endpoint would keep pushing its own reset away.
        """
        now = time.time()
        deadline = now + expiry
        with store.transaction() as conn:
            row = conn.execute(
                'SELECT count, expires_at FROM rate_limits WHERE key = ?', (key,)
            ).fetchone()

            if row is None or row[1] <= now:
                count, expires_at = amount, deadline
            else:
                count, expires_at = row[0] + amount, row[1]

            conn.execute(
                'INSERT INTO rate_limits (key, count, expires_at) VALUES (?, ?, ?) '
                'ON CONFLICT(key) DO UPDATE SET '
                '  count = excluded.count, expires_at = excluded.expires_at',
                (key, count, expires_at),
            )
        return count

    def get(self, key):
        """The counter's current value, or 0 if the window has passed."""
        row = store.read_one(
            'SELECT count, expires_at FROM rate_limits WHERE key = ?', (key,)
        )
        if row is None or row[1] <= time.time():
            return 0
        return row[0]

    def get_expiry(self, key):
        """When the current window ends, as a Unix timestamp.

        ``limits`` turns this into the ``Retry-After`` header and the
        ``X-RateLimit-Reset`` value, so a stale row must report *now* rather
        than a time in the past -- a negative Retry-After is meaningless to a
        client.
        """
        row = store.read_one(
            'SELECT expires_at FROM rate_limits WHERE key = ?', (key,)
        )
        now = time.time()
        if row is None or row[0] <= now:
            return now
        return row[0]

    def check(self):
        """Whether the backend is reachable. Used by the limiter's healthcheck."""
        return store.healthy()

    def reset(self):
        """Drop every counter. Returns how many were removed."""
        with store.transaction() as conn:
            return conn.execute('DELETE FROM rate_limits').rowcount

    def clear(self, key):
        """Drop one counter, so a limit can be lifted for a single caller."""
        store.write('DELETE FROM rate_limits WHERE key = ?', (key,))
