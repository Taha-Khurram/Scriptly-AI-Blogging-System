"""Gunicorn configuration.

Previously the server was configured by a command-line string in the platform
blueprint: ``--workers 1 --threads 8 --timeout 300``. That put tuning decisions
somewhere they could not be commented, and pinned the deployment to a single
worker because three pieces of mutable state lived inside the web process -- the
APScheduler auto-publisher, the in-memory cache, and the background task pool.
A second worker meant duplicate scheduled publishes and a split cache.

Two of those three are now fixed: the cache is shared through Redis when
``REDIS_URL`` is set, and the scheduler holds a single-runner lease. So the
worker count is a real knob again, and it lives here with the reasoning
attached.

The remaining constraint is the AI task pool, which is still per-process. See
``WEB_CONCURRENCY`` below.
"""
import os


def _env_int(name, default):
    try:
        return int(os.getenv(name, ''))
    except ValueError:
        return default


# --- Socket ---------------------------------------------------------------
bind = f"0.0.0.0:{os.getenv('PORT', '8000')}"

# Depth of the kernel's accept queue. Above the worker capacity so a burst
# queues briefly instead of being refused at the TCP level.
backlog = 2048


# --- Worker processes ------------------------------------------------------
#
# Threads, not processes, are the primary lever here: nearly every request is
# waiting on Firestore or Gemini, so a thread costs a stack and buys a
# concurrent I/O wait. Processes cost a full interpreter -- roughly 250-350 MB
# resident for this dependency set (flask + firebase-admin + grpc + numpy +
# google-generativeai), which is most of a small instance on its own.
#
# WEB_CONCURRENCY must stay at 1 unless the AI task pool has been moved to a
# shared broker (Celery/RQ). Task state lives in the worker that accepted the
# job, so with N workers a status poll has an (N-1)/N chance of landing on a
# worker that has never heard of that task id.
workers = _env_int('WEB_CONCURRENCY', 1)

threads = _env_int('GUNICORN_THREADS', 8)

# gthread: the sync worker handles one request per thread with no concurrency,
# and gevent would need every blocking library in the stack (grpc especially)
# to be monkey-patch clean, which firebase-admin is not.
worker_class = 'gthread'

# Restart a worker after this many requests, with jitter so several workers do
# not recycle at the same instant. A defence against slow leaks in long-lived
# gRPC channels rather than a fix for one; the jitter is what keeps a recycle
# from becoming a coordinated capacity dip.
max_requests = _env_int('MAX_REQUESTS', 1000)
max_requests_jitter = _env_int('MAX_REQUESTS_JITTER', 100)


# --- Timeouts -------------------------------------------------------------
#
# A blog generation or humanisation can legitimately run for minutes. Most of
# that work is dispatched to the background pool and polled, so requests
# themselves stay short -- but the SEO and formatting routes still run inline,
# and a 30s default killed them mid-flight.
timeout = _env_int('GUNICORN_TIMEOUT', 300)

# Time a worker gets to finish in-flight requests after SIGTERM. Long enough
# for a Firestore write to complete so a deploy cannot truncate one halfway.
graceful_timeout = _env_int('GUNICORN_GRACEFUL_TIMEOUT', 30)

# Must exceed the load balancer's idle timeout, or the balancer reuses a
# connection the worker has already closed and the client sees a 502.
keepalive = _env_int('GUNICORN_KEEPALIVE', 65)


# --- Request limits (defence in depth) ------------------------------------
#
# Werkzeug enforces MAX_CONTENT_LENGTH on the body; these bound the parts of a
# request that arrive before the application sees it at all.
limit_request_line = 8190
limit_request_fields = 100
limit_request_field_size = 16380


# --- Logging --------------------------------------------------------------
#
# Both streams go to stdout/stderr for the platform to collect. The access log
# is disabled: app/core/logging.py already emits one structured record per
# request with a request id, duration and status, and gunicorn's own line would
# duplicate every one of them in a different format.
accesslog = None
errorlog = '-'
loglevel = os.getenv('GUNICORN_LOG_LEVEL', 'info')
capture_output = True


# --- Process naming -------------------------------------------------------
proc_name = 'scriptly'

# Load the application before forking. The workers then share the interpreter's
# copy-on-write pages, which meaningfully lowers total memory for an import
# footprint this large -- and a configuration error surfaces once at boot
# instead of once per worker.
preload_app = True


# --- Lifecycle hooks ------------------------------------------------------

def on_starting(server):
    server.log.info(
        'Scriptly starting: %s worker(s) x %s threads, %ss timeout',
        workers, threads, timeout,
    )
    if workers > 1 and not os.getenv('REDIS_URL'):
        server.log.warning(
            'WEB_CONCURRENCY=%s with no REDIS_URL. The cache and rate-limit '
            'counters will be per-worker, so cache invalidation will not '
            'propagate and effective rate limits are multiplied by %s.',
            workers, workers,
        )
    if workers > 1:
        server.log.warning(
            'WEB_CONCURRENCY=%s: background AI task state is per-worker, so a '
            'status poll may hit a worker that does not know the task id. Use '
            'sticky sessions, or move the task pool to a shared broker.',
            workers,
        )


def worker_int(worker):
    """SIGINT/SIGQUIT: let the app drain its own background work."""
    worker.log.info('Worker %s interrupted, draining', worker.pid)


def worker_abort(worker):
    """Fires when a worker exceeds `timeout` and is about to be killed.

    Logged loudly because this is almost always an inline AI call that ran past
    its budget, and it is otherwise invisible -- the client just sees the
    connection drop.
    """
    worker.log.error(
        'Worker %s exceeded the %ss timeout and is being killed. A request was '
        'blocked longer than expected; check for an inline AI call that should '
        'be running in the background pool.',
        worker.pid, timeout,
    )


def child_exit(server, worker):
    server.log.info('Worker %s exited', worker.pid)
