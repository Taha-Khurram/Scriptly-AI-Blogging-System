"""Process-shared cache with a Redis backend and an in-memory fallback.

The original implementation was a module-level dict. That is correct for one
worker and wrong the moment there are two: each process holds its own copy, so
a publish that invalidates a key in worker A leaves worker B serving the stale
version, and the hit rate falls by however many workers there are. It also lost
everything on deploy, producing a thundering herd of Firestore reads against a
50k/day quota.

This module keeps the exact same call surface -- ``cache.get/set/delete/
clear/clear_prefix`` and the ``@cached`` decorator -- so no call site changes,
and swaps the storage:

* ``RedisBackend`` when ``REDIS_URL`` is set: shared across workers and
  instances, survives deploys, and gives correct cross-worker invalidation.
* ``MemoryBackend`` otherwise: the previous behaviour, plus an entry cap and
  LRU-ish eviction so a long-lived single worker cannot grow without bound.

A Redis outage must never take the application down, so every Redis call is
wrapped: on failure the backend degrades to the in-memory store, logs once, and
periodically retries. A cache is an optimisation, and behaving as though it is
simply cold is always safer than propagating the error.
"""
from __future__ import annotations

import logging
import pickle
import threading
import time
from functools import wraps

logger = logging.getLogger(__name__)

# Entry ceiling for the in-memory backend. Roughly a few MB of typical payloads
# (blog lists, settings maps); enough to hold a busy working set, small enough
# that a runaway key pattern cannot exhaust a 512 MB instance.
_MEMORY_MAX_ENTRIES = 5000

# How long to wait before trying Redis again after a failure, so a dead Redis
# does not add a connection timeout to every single request.
_REDIS_RETRY_SECONDS = 30

# A persistent backend has no equivalent of "the process restarted", so an
# entry written with no TTL would never be reclaimed. Callers all pass one (or
# get Cache's default); this is the belt-and-braces value for anything that
# does not.
_NO_TTL_FALLBACK_SECONDS = 300

_MISS = object()


class MemoryBackend:
    """Thread-safe in-process store with TTL and a bounded entry count."""

    def __init__(self, max_entries=_MEMORY_MAX_ENTRIES):
        self._data = {}
        self._lock = threading.RLock()
        self._max_entries = max_entries

    def get(self, key):
        now = time.time()
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return _MISS
            value, expiry, _ = entry
            if expiry is not None and now >= expiry:
                del self._data[key]
                return _MISS
            # Re-stamp last-access so eviction sheds genuinely cold keys.
            self._data[key] = (value, expiry, now)
            return value

    def set(self, key, value, ttl=None):
        now = time.time()
        with self._lock:
            if len(self._data) >= self._max_entries and key not in self._data:
                self._evict_locked(now)
            self._data[key] = (value, now + ttl if ttl else None, now)

    def delete(self, key):
        with self._lock:
            self._data.pop(key, None)

    def clear(self):
        with self._lock:
            self._data.clear()

    def clear_prefix(self, prefix):
        with self._lock:
            for key in [k for k in self._data if k.startswith(prefix)]:
                del self._data[key]

    def _evict_locked(self, now):
        """Drop expired entries first, then the coldest tenth."""
        expired = [
            k for k, (_, expiry, _) in self._data.items()
            if expiry is not None and now >= expiry
        ]
        for key in expired:
            del self._data[key]
        if len(self._data) < self._max_entries:
            return
        by_age = sorted(self._data.items(), key=lambda item: item[1][2])
        for key, _ in by_age[: max(1, self._max_entries // 10)]:
            del self._data[key]

    def stats(self):
        with self._lock:
            return {'backend': 'memory', 'entries': len(self._data),
                    'max_entries': self._max_entries}


class SqliteBackend:
    """Shared store on the local SQLite database, no service required.

    Replaces ``MemoryBackend`` as the default. The in-process dict is only
    correct for a single worker: with N workers each keeps its own copy, so the
    hit rate divides by N and -- the part that is an actual bug rather than a
    slowdown -- **invalidation does not propagate**. Publishing a post clears
    ``published_blogs:<owner>`` in the worker that handled the request and
    leaves every other worker serving the pre-publish list until its TTL runs
    out. The user sees the change appear and disappear depending on which
    worker answers.

    This is the same file the sessions and rate-limit counters live in, so it
    is shared across every thread and worker process on the host (SQLite WAL),
    survives a reload, and adds no infrastructure.

    Values are pickled for the same reason the Redis backend pickles them: the
    cache holds Firestore documents -- nested dicts containing
    ``DatetimeWithNanoseconds`` and other SDK types -- which JSON cannot
    round-trip without a lossy custom encoder. Only this application writes
    these rows, and they live in a file the application owns.

    Not shared across *separate instances*, same as the rest of the store. A
    second instance would keep its own cache; correctness is unaffected because
    every entry has a TTL, but invalidation would again be per instance.
    """

    def __init__(self, store):
        self._store = store

    def get(self, key):
        try:
            row = self._store.read_one(
                'SELECT value, expires_at FROM cache_entries WHERE key = ?',
                (key,),
            )
        except Exception:
            logger.warning('Cache read failed for %s', key, exc_info=True)
            return _MISS

        if row is None:
            return _MISS
        if row[1] <= time.time():
            # Expired but not yet swept. Treated as absent; the sweeper will
            # remove it, so there is no need to pay a write on a read path.
            return _MISS
        try:
            return pickle.loads(row[0])
        except Exception:
            # Written by an older code version whose classes have changed
            # shape. Drop it and report a miss.
            logger.warning('Discarding undecodable cache entry: %s', key)
            self.delete(key)
            return _MISS

    def set(self, key, value, ttl=None):
        try:
            payload = pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
        except Exception as exc:
            logger.warning('Value for %s is not cacheable: %s', key, exc)
            return
        # A missing TTL would mean a row that the sweeper never reclaims, so
        # entries without one are given the default rather than living forever.
        expires_at = time.time() + (ttl if ttl else _NO_TTL_FALLBACK_SECONDS)
        try:
            self._store.write(
                'INSERT INTO cache_entries (key, value, expires_at) '
                'VALUES (?, ?, ?) '
                'ON CONFLICT(key) DO UPDATE SET '
                '  value = excluded.value, expires_at = excluded.expires_at',
                (key, payload, expires_at),
            )
        except Exception:
            # A cache is an optimisation. Failing to write one must never fail
            # the request whose work already succeeded.
            logger.warning('Cache write failed for %s', key, exc_info=True)

    def delete(self, key):
        try:
            self._store.write('DELETE FROM cache_entries WHERE key = ?', (key,))
        except Exception:
            logger.warning('Cache delete failed for %s', key, exc_info=True)

    def clear(self):
        try:
            self._store.write('DELETE FROM cache_entries')
        except Exception:
            logger.warning('Cache clear failed', exc_info=True)

    def clear_prefix(self, prefix):
        """Invalidate every key beginning with ``prefix``.

        ``LIKE ? || '%'`` uses the primary-key index, so this is a range scan
        rather than a table scan. ``ESCAPE`` is set because a prefix built from
        an id could legitimately contain ``%`` or ``_``, which would otherwise
        be read as wildcards and delete more than asked.
        """
        escaped = (
            prefix.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        )
        try:
            self._store.write(
                "DELETE FROM cache_entries WHERE key LIKE ? ESCAPE '\\'",
                (escaped + '%',),
            )
        except Exception:
            logger.warning('Cache prefix clear failed: %s', prefix, exc_info=True)

    def stats(self):
        try:
            row = self._store.read_one(
                'SELECT COUNT(*), COALESCE(SUM(LENGTH(value)), 0) '
                'FROM cache_entries'
            )
            return {'backend': 'sqlite', 'entries': row[0],
                    'value_bytes': row[1], 'path': self._store.path}
        except Exception as exc:
            return {'backend': 'sqlite', 'error': str(exc)}

    def healthy(self):
        return self._store.healthy()


class RedisBackend:
    """Shared store on Redis, degrading to ``fallback`` when Redis is down.

    Values are pickled: the cache holds Firestore documents -- nested dicts
    containing ``DatetimeWithNanoseconds`` and other SDK types -- which JSON
    cannot round-trip without a lossy custom encoder. Only this application
    writes these keys, so the usual objection to unpickling untrusted input
    does not apply; a dedicated Redis database (or key prefix) keeps them
    separate from anything else.
    """

    def __init__(self, url, key_prefix='scriptly', fallback=None):
        import redis  # imported lazily so redis stays an optional dependency

        self._prefix = f'{key_prefix}:cache:'
        self._fallback = fallback or MemoryBackend()
        self._degraded_until = 0.0
        self._lock = threading.Lock()

        self._client = redis.Redis.from_url(
            url,
            # Bounded waits: an unresponsive Redis must not hold a worker
            # thread for the duration of a TCP timeout on every request.
            socket_timeout=1.0,
            socket_connect_timeout=1.0,
            retry_on_timeout=False,
            health_check_interval=30,
            decode_responses=False,
            max_connections=int(_pool_size()),
        )
        # Prove reachability at construction so a typo in REDIS_URL surfaces at
        # boot rather than as a slow trickle of cache misses in production.
        self._client.ping()

    @property
    def degraded(self):
        return time.time() < self._degraded_until

    def _degrade(self, exc):
        with self._lock:
            first = not self.degraded
            self._degraded_until = time.time() + _REDIS_RETRY_SECONDS
        if first:
            logger.error(
                'Redis cache unavailable, falling back to in-process cache '
                'for %ss: %s', _REDIS_RETRY_SECONDS, exc,
            )

    def _key(self, key):
        return f'{self._prefix}{key}'

    def get(self, key):
        if self.degraded:
            return self._fallback.get(key)
        try:
            raw = self._client.get(self._key(key))
        except Exception as exc:
            self._degrade(exc)
            return self._fallback.get(key)
        if raw is None:
            return _MISS
        try:
            return pickle.loads(raw)
        except Exception:
            # A payload written by an older code version whose classes have
            # changed shape. Drop it and treat this as a miss.
            logger.warning('Discarding undecodable cache entry: %s', key)
            try:
                self._client.delete(self._key(key))
            except Exception:
                pass
            return _MISS

    def set(self, key, value, ttl=None):
        if self.degraded:
            self._fallback.set(key, value, ttl)
            return
        try:
            payload = pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
        except Exception as exc:
            logger.warning('Value for %s is not cacheable: %s', key, exc)
            return
        try:
            if ttl:
                self._client.setex(self._key(key), int(ttl), payload)
            else:
                self._client.set(self._key(key), payload)
        except Exception as exc:
            self._degrade(exc)
            self._fallback.set(key, value, ttl)

    def delete(self, key):
        self._fallback.delete(key)
        if self.degraded:
            return
        try:
            self._client.delete(self._key(key))
        except Exception as exc:
            self._degrade(exc)

    def clear(self):
        self._fallback.clear()
        if self.degraded:
            return
        try:
            self._delete_matching(f'{self._prefix}*')
        except Exception as exc:
            self._degrade(exc)

    def clear_prefix(self, prefix):
        self._fallback.clear_prefix(prefix)
        if self.degraded:
            return
        try:
            self._delete_matching(f'{self._prefix}{prefix}*')
        except Exception as exc:
            self._degrade(exc)

    def _delete_matching(self, pattern):
        """Delete by pattern using SCAN, never KEYS.

        ``KEYS`` blocks the Redis event loop for the whole keyspace scan, which
        on a shared instance stalls every other client. SCAN yields in chunks
        and deletes in pipelined batches.
        """
        cursor = 0
        while True:
            cursor, keys = self._client.scan(cursor=cursor, match=pattern, count=500)
            if keys:
                pipe = self._client.pipeline(transaction=False)
                for key in keys:
                    pipe.delete(key)
                pipe.execute()
            if cursor == 0:
                break

    def stats(self):
        info = {'backend': 'redis', 'degraded': self.degraded}
        if not self.degraded:
            try:
                server = self._client.info(section='memory')
                info['used_memory_human'] = server.get('used_memory_human')
            except Exception:
                info['degraded'] = True
        return info


def _pool_size():
    """Connection pool size, sized to the worker's thread count."""
    import os
    try:
        return max(8, int(os.getenv('GUNICORN_THREADS', '8')) + 4)
    except ValueError:
        return 12


class Cache:
    """Front door for the cache. Swappable backend, stable API."""

    def __init__(self):
        self._backend = MemoryBackend()
        self._default_ttl = 300
        self._configured = False
        self._hits = 0
        self._misses = 0
        self._stats_lock = threading.Lock()

    def configure(self, *, redis_url=None, default_ttl=300, key_prefix='scriptly'):
        """Choose the backend. Called once from the app factory.

        Order of preference:

        1. **Redis**, when ``REDIS_URL`` is set. Shared across instances, so it
           remains the right answer for a multi-instance deployment.
        2. **SQLite**, the default. Shared across every thread and worker
           process on the host, needs no service, and -- crucially -- makes
           invalidation propagate between workers.
        3. **Memory**, only if the SQLite store has not been configured. That
           means something is being constructed outside the application factory
           (a script, a bare unit test), where a process-local cache is the
           correct scope anyway.

        A failure to reach Redis is logged and tolerated: the app falls through
        to SQLite and stays up. Losing the shared cache degrades performance;
        refusing to boot would be an outage.
        """
        self._default_ttl = default_ttl

        if redis_url:
            try:
                self._backend = RedisBackend(
                    redis_url, key_prefix=key_prefix, fallback=MemoryBackend()
                )
                logger.info('Cache backend: Redis (shared across instances)')
                self._configured = True
                return
            except Exception as exc:
                logger.error(
                    'Redis at %s is unreachable (%s); falling back to the '
                    'local SQLite store.', _safe_url(redis_url), exc,
                )

        from app.core.store import store as sqlite_store

        if sqlite_store.configured:
            self._backend = SqliteBackend(sqlite_store)
            logger.info(
                'Cache backend: SQLite at %s (shared across this instance\'s '
                'workers and threads)', sqlite_store.path,
            )
        else:
            # Not reachable through create_app, which configures the store
            # first. Kept so importing the singleton outside an app context
            # still yields a working cache.
            logger.info(
                'Cache backend: in-process (the SQLite store is not '
                'configured, so this is not an application context).'
            )
            self._backend = MemoryBackend()

        self._configured = True

    # --- Public API (unchanged from the original SimpleCache) -------------

    def get(self, key):
        """Cached value, or ``None`` when absent or expired.

        ``None`` doubles as "miss", matching the original behaviour every call
        site was written against. :meth:`get_or_miss` is available where the
        difference between a cached ``None`` and a miss actually matters.
        """
        value = self._backend.get(key)
        if value is _MISS:
            self._record(hit=False)
            return None
        self._record(hit=True)
        return value

    def get_or_miss(self, key, default=None):
        """Like :meth:`get`, but distinguishes a stored ``None`` from a miss."""
        value = self._backend.get(key)
        if value is _MISS:
            self._record(hit=False)
            return default
        self._record(hit=True)
        return value

    def set(self, key, value, ttl=None):
        self._backend.set(key, value, ttl if ttl is not None else self._default_ttl)

    def delete(self, key):
        self._backend.delete(key)

    def clear(self):
        self._backend.clear()

    def clear_prefix(self, prefix):
        """Invalidate every key beginning with ``prefix``.

        The invalidation primitive the data layer relies on: publishing a post
        clears ``published_blogs:<owner>`` regardless of the ``:limit`` suffix
        each cached variant carries.
        """
        self._backend.clear_prefix(prefix)

    # --- Introspection ----------------------------------------------------

    def _record(self, hit):
        with self._stats_lock:
            if hit:
                self._hits += 1
            else:
                self._misses += 1

    def stats(self):
        with self._stats_lock:
            hits, misses = self._hits, self._misses
        total = hits + misses
        payload = {
            'hits': hits,
            'misses': misses,
            'hit_rate': round(hits / total, 4) if total else None,
            'configured': self._configured,
        }
        payload.update(self._backend.stats())
        return payload

    @property
    def is_shared(self):
        """True when the backend is visible to this instance's other workers.

        The property that matters for correctness: with an unshared cache,
        invalidation does not propagate and one worker keeps serving content
        another worker has already replaced.
        """
        if isinstance(self._backend, RedisBackend):
            return not self._backend.degraded
        return isinstance(self._backend, SqliteBackend)

    @property
    def is_shared_across_instances(self):
        """True only for Redis. SQLite is shared per host, not per fleet."""
        return isinstance(self._backend, RedisBackend) and not self._backend.degraded

    def healthy(self):
        """Whether the configured backend is currently serving."""
        if isinstance(self._backend, RedisBackend):
            return not self._backend.degraded
        if isinstance(self._backend, SqliteBackend):
            return self._backend.healthy()
        return True


def _safe_url(url):
    """Redact credentials before a Redis URL reaches the logs."""
    if '@' not in url:
        return url
    scheme, _, rest = url.partition('://')
    return f'{scheme}://***@{rest.rpartition("@")[2]}'


# Module-level singleton. Imported directly across the codebase, so it must
# exist at import time and be reconfigured -- never replaced -- at startup.
cache = Cache()


def cached(ttl=300, key_prefix='', key_builder=None):
    """Memoise a function's return value in the shared cache.

    ``key_builder`` exists because the default key is derived from ``repr`` of
    the arguments, which is unstable for objects without a deterministic repr
    (and unbounded in length). Pass one for anything other than scalar
    arguments::

        @cached(ttl=120, key_builder=lambda user_id, **_: f'blogs:{user_id}')
        def get_blogs(user_id, verbose=False): ...

    A ``None`` return is not cached, matching the original decorator: it is
    almost always the "lookup failed" path here, and caching it would pin a
    transient Firestore error in place for the whole TTL.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if key_builder is not None:
                suffix = key_builder(*args, **kwargs)
            else:
                suffix = f'{func.__name__}:{args!r}:{sorted(kwargs.items())!r}'
            key = f'{key_prefix}{suffix}'

            hit = cache.get_or_miss(key, _MISS)
            if hit is not _MISS:
                return hit

            result = func(*args, **kwargs)
            if result is not None:
                cache.set(key, result, ttl)
            return result

        wrapper.cache_clear = lambda: cache.clear_prefix(key_prefix or func.__name__)
        return wrapper
    return decorator


# Legacy alias: earlier code referenced the class by its original name.
SimpleCache = Cache
