"""Health, readiness and liveness probes.

The platform health check pointed at ``/``, which redirects to the login page.
That returns 302 whether or not Firestore is reachable, so a broken instance
kept passing its check and kept receiving traffic. These endpoints answer the
three genuinely different questions an orchestrator asks:

``/livez``
    Is the process alive? No dependency checks -- a failing dependency must not
    cause a restart loop, because restarting does not fix Firestore being down.

``/readyz``
    Should this instance receive traffic? Checks the dependencies a request
    actually needs. Returns 503 when it cannot serve, so the load balancer
    drains it instead of sending users into errors.

``/healthz``
    Full component breakdown for humans and dashboards. Same 200/503 semantics
    as ``/readyz`` plus per-component detail and timings.

Every probe is exempt from rate limiting -- throttling the orchestrator's own
health check would make it declare the instance dead -- and every check is
individually time-boxed so one slow dependency cannot make the probe itself
time out and be read as a total failure.
"""
from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

from flask import Blueprint, current_app, jsonify

from app.core.extensions import limiter
from app.utils.cache import cache

logger = logging.getLogger(__name__)

health_bp = Blueprint('health', __name__)

# Per-check ceiling. Generous enough for a cold gRPC channel, short enough that
# the whole probe stays inside a typical 5s orchestrator timeout.
_CHECK_TIMEOUT_SECONDS = 3.0

# Recorded at import, which is close enough to process start for an uptime
# figure and avoids threading a timestamp through the factory.
_PROCESS_STARTED = time.time()

# A dedicated pool: running checks on the request thread would serialise them,
# and borrowing the AI task pool would let a saturated queue block the probe.
_probe_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='health')


def _timed(fn):
    """Run ``fn``, returning its result dict with a duration attached."""
    started = time.perf_counter()
    try:
        result = fn()
    except Exception as exc:
        result = {'status': 'fail', 'error': str(exc)[:200]}
    result['duration_ms'] = round((time.perf_counter() - started) * 1000, 1)
    return result


def _check_firestore():
    """Cheapest possible proof that Firestore answers.

    A settings document read rather than a write: it exercises the same
    credentials, gRPC channel and network path, costs one read, and cannot
    corrupt anything if the probe runs every few seconds forever.
    """
    from app.firebase.firebase_admin import FirebaseLoader

    client = FirebaseLoader.get_instance()
    list(client.collection('app_settings').limit(1).stream())
    return {'status': 'ok'}


def _check_cache():
    """Round-trip a value so a silently-degraded Redis is visible."""
    probe_key = '__health_probe__'
    cache.set(probe_key, 'ok', ttl=10)
    value = cache.get(probe_key)
    cache.delete(probe_key)

    if value != 'ok':
        return {'status': 'fail', 'error': 'cache round-trip returned no value'}

    stats = cache.stats()
    return {
        'status': 'ok' if cache.healthy() else 'degraded',
        'backend': stats.get('backend'),
        'shared_across_workers': cache.is_shared,
        'hit_rate': stats.get('hit_rate'),
    }


def _check_ai():
    """Report AI configuration only.

    Deliberately does *not* call the model: a probe that spends a Gemini
    request per health check would consume the quota it is meant to protect,
    and quota exhaustion is exactly the failure it would then report.
    """
    from app.services.gemini_client import gemini

    if not gemini.is_configured:
        return {'status': 'fail', 'error': 'GEMINI_API_KEY is not configured'}
    return {'status': 'ok', 'model': gemini.default_model}


def _check_tasks():
    """Background worker pool depth -- the AI throughput ceiling."""
    from app.utils.task_manager import task_manager

    snapshot = task_manager.stats()
    saturated = snapshot.get('queued', 0) >= snapshot.get('max_queue_depth', 0)
    return {
        'status': 'degraded' if saturated else 'ok',
        **snapshot,
    }


# Only Firestore is required to serve a request; everything else degrades.
# The cache falling back to in-process storage is slower, not broken, and AI
# being unconfigured breaks the AI features rather than the site.
_CRITICAL_CHECKS = {'firestore'}

_CHECKS = {
    'firestore': _check_firestore,
    'cache': _check_cache,
    'ai': _check_ai,
    'tasks': _check_tasks,
}


def _run_checks(names):
    """Run the named checks concurrently, each with its own timeout."""
    futures = {name: _probe_pool.submit(_timed, _CHECKS[name]) for name in names}
    results = {}
    for name, future in futures.items():
        try:
            results[name] = future.result(timeout=_CHECK_TIMEOUT_SECONDS)
        except FutureTimeout:
            results[name] = {
                'status': 'fail',
                'error': f'check exceeded {_CHECK_TIMEOUT_SECONDS}s',
            }
        except Exception as exc:
            results[name] = {'status': 'fail', 'error': str(exc)[:200]}
    return results


def _overall(results):
    """Aggregate per-component states into one verdict."""
    for name in _CRITICAL_CHECKS:
        if results.get(name, {}).get('status') == 'fail':
            return 'unhealthy'
    if any(r.get('status') in ('fail', 'degraded') for r in results.values()):
        return 'degraded'
    return 'healthy'


@health_bp.route('/livez')
@limiter.exempt
def livez():
    """Process liveness. Never touches a dependency."""
    return jsonify({
        'status': 'alive',
        'uptime_seconds': round(time.time() - _PROCESS_STARTED, 1),
        'pid': os.getpid(),
    }), 200


@health_bp.route('/readyz')
@limiter.exempt
def readyz():
    """Traffic readiness. 503 tells the balancer to route elsewhere."""
    results = _run_checks(_CRITICAL_CHECKS)
    verdict = _overall(results)
    status_code = 200 if verdict != 'unhealthy' else 503

    if status_code != 200:
        logger.error('Readiness check failed', extra={'checks': results})

    return jsonify({'status': verdict, 'checks': results}), status_code


@health_bp.route('/healthz')
@limiter.exempt
def healthz():
    """Full component breakdown, for dashboards and on-call debugging."""
    results = _run_checks(list(_CHECKS))
    verdict = _overall(results)
    status_code = 200 if verdict != 'unhealthy' else 503

    payload = {
        'status': verdict,
        'environment': current_app.config.get('ENV_NAME', 'unknown'),
        'uptime_seconds': round(time.time() - _PROCESS_STARTED, 1),
        'pid': os.getpid(),
        'checks': results,
    }

    if verdict == 'unhealthy':
        logger.error('Health check unhealthy', extra={'checks': results})
    elif verdict == 'degraded':
        logger.warning('Health check degraded', extra={'checks': results})

    return jsonify(payload), status_code
