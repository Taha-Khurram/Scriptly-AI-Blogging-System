"""Per-request Firestore round-trip accounting.

Why this module exists
---------------------
This application's page latency is not driven by CPU, by Jinja, or by the size
of any collection. It is driven by **how many sequential Firestore round trips
a view issues**, because one round trip costs 0.5-3.5 s from a client that is
not co-located with the database. A page that issues zero queries renders in
2 ms; a page that issues six sequential queries takes six seconds. Nothing in
a normal profiler makes that visible -- ``cProfile`` reports the time inside
``grpc``, which tells you the network was slow but not *which view asked for
what, how many times, and whether it needed to wait*.

So the useful unit of measurement here is the round trip, and this module
counts them. It wraps the four Firestore read entry points once at startup and
records, per request:

* how many round trips were made,
* how long each took,
* the application frame that issued it,
* and how much of that time was actually *spent waiting* versus overlapped by
  ``run_parallel_simple``.

That last number is the one that matters: when the sum of query times greatly
exceeds the request's wall time, the view is already parallel and the fix is
fewer/cheaper queries. When the two are equal, the queries are sequential and
the fix is parallelism.

Usage
-----
Off by default; it adds a ``perf_counter`` pair and a stack walk per query, so
it is not free. Enable per environment::

    PROFILE_QUERIES=1

Then every response carries ``X-DB-Queries`` and ``X-DB-Ms``, and any request
over ``SLOW_REQUEST_MS`` logs a full per-query breakdown at WARNING::

    GET /activity-log 5749ms: 6 Firestore round trips, 5709ms in DB
        572ms  query.stream   repositories/users.py:92 in get_my_sub_users
       1268ms  query.stream   repositories/activity.py:209 in get_activity_stats
        558ms  query.stream   repositories/users.py:92 in get_my_sub_users   <-- duplicate
       ...

Duplicated queries within one request are flagged explicitly, because the same
call repeated in one request is always a bug and always costs a full round trip.

For a CPU profile of a single request (rarely the answer here, but occasionally
you do want it) append ``?_profile=1`` to any URL while ``PROFILE_QUERIES`` is
on and the ``cProfile`` table is logged instead of being written to disk.
"""
from __future__ import annotations

import functools
import io
import logging
import pstats
import threading
import time
import traceback
from collections import Counter

from flask import g, request

logger = logging.getLogger('scriptly.profiling')

# Set once, at wrap time, so the wrappers can be installed unconditionally and
# cost nothing more than an attribute load when profiling is off.
_ENABLED = False
_INSTALLED = False
_install_lock = threading.Lock()

# Frames from these paths are library internals; the interesting frame is the
# repository method that issued the query.
_APP_MARKER = '/app/'
_SKIP = ('site-packages', 'core/profiling.py')


def _origin():
    """The application frame that issued the current Firestore call."""
    for frame in reversed(traceback.extract_stack()[:-3]):
        path = frame.filename.replace('\\', '/')
        if _APP_MARKER not in path or any(s in path for s in _SKIP):
            continue
        return '%s:%s in %s' % (path.split(_APP_MARKER)[-1], frame.lineno, frame.name)
    return '<unknown>'


def _record(label, elapsed_ms, origin):
    """Append one round trip to this request's ledger, if one is open."""
    try:
        ledger = g.get('_db_calls')
    except RuntimeError:
        return  # outside a request context: scheduler thread, warm-up, script
    if ledger is not None:
        ledger.append((label, elapsed_ms, origin))


def _wrap_read(cls, method_name, label):
    """Wrap one Firestore read entry point to record its round trip.

    ``stream()`` returns a generator and does not perform the RPC until it is
    consumed, so timing the call alone would measure nothing. The wrapper
    materialises it into a list -- which is what every call site in this
    codebase does anyway, either with ``list()`` or a ``for`` loop -- so the
    recorded duration is the real network cost.
    """
    original = getattr(cls, method_name)

    @functools.wraps(original)
    def wrapper(self, *args, **kwargs):
        if not _ENABLED:
            return original(self, *args, **kwargs)
        started = time.perf_counter()
        result = original(self, *args, **kwargs)
        if hasattr(result, '__iter__') and not isinstance(result, (list, tuple, dict)):
            result = list(result)
        _record(label, (time.perf_counter() - started) * 1000, _origin())
        return result

    setattr(cls, method_name, wrapper)


def _install_wrappers():
    """Patch the Firestore read paths. Idempotent, and safe to call at import."""
    global _INSTALLED
    with _install_lock:
        if _INSTALLED:
            return
        from google.cloud.firestore_v1.aggregation import AggregationQuery
        from google.cloud.firestore_v1.document import DocumentReference
        from google.cloud.firestore_v1.query import Query

        _wrap_read(Query, 'stream', 'query.stream')
        _wrap_read(Query, 'get', 'query.get')
        _wrap_read(DocumentReference, 'get', 'doc.get')
        _wrap_read(AggregationQuery, 'get', 'agg.get')
        _INSTALLED = True


def register_query_profiler(app):
    """Enable per-request query accounting when ``PROFILE_QUERIES`` is set.

    Called from the application factory. When the flag is off this registers
    nothing and patches nothing, so a production deployment pays exactly zero.
    """
    global _ENABLED

    if not app.config.get('PROFILE_QUERIES'):
        return

    _install_wrappers()
    _ENABLED = True
    slow_ms = app.config.get('SLOW_REQUEST_MS', 2000)

    app.logger.warning(
        'Query profiler is ON (PROFILE_QUERIES). Every response carries '
        'X-DB-Queries / X-DB-Ms. Do not leave this enabled in production.'
    )

    @app.before_request
    def _open_ledger():
        g._db_calls = []
        g._profile_started = time.perf_counter()
        if request.args.get('_profile') == '1':
            import cProfile
            g._cprofile = cProfile.Profile()
            g._cprofile.enable()

    @app.after_request
    def _report(response):
        calls = g.pop('_db_calls', None)
        if calls is None:
            return response

        started = g.pop('_profile_started', None)
        wall_ms = (time.perf_counter() - started) * 1000 if started else 0.0
        db_ms = sum(ms for _, ms, _ in calls)

        response.headers['X-DB-Queries'] = str(len(calls))
        response.headers['X-DB-Ms'] = '%.0f' % db_ms

        if wall_ms > slow_ms and calls:
            # Same origin twice in one request is a duplicate query: a second
            # full round trip for an answer the request already had.
            repeats = {o: n for o, n in Counter(o for _, _, o in calls).items() if n > 1}
            # Queries that overlap each other spend more total time in the
            # database than the request takes end to end; queries issued one
            # after another add up to roughly the request's own duration. So
            # the ratio, not the absolute figure, says which fix applies:
            # parallelise the view, or make the queries themselves cheaper.
            overlapped = len(calls) > 1 and db_ms > wall_ms * 1.2
            lines = [
                '%s %s %.0fms: %d Firestore round trips, %.0fms in DB '
                '(%s)' % (
                    request.method, request.path, wall_ms, len(calls), db_ms,
                    'overlapped -- reduce or cache them'
                    if overlapped else 'sequential -- parallelise them',
                )
            ]
            for label, ms, origin in calls:
                flag = '   <-- duplicated x%d' % repeats[origin] if origin in repeats else ''
                lines.append('    %7.0fms  %-13s %s%s' % (ms, label, origin, flag))
            logger.warning('\n'.join(lines))

        profiler = g.pop('_cprofile', None)
        if profiler is not None:
            profiler.disable()
            stream = io.StringIO()
            pstats.Stats(profiler, stream=stream).sort_stats('cumulative').print_stats(30)
            logger.warning('cProfile for %s\n%s', request.path, stream.getvalue())

        return response
