"""Background scheduler for scheduled publishing and periodic maintenance.

The auto-publisher must run in exactly one process. Previously it started
unconditionally inside every web worker, which is why the deployment was pinned
to ``--workers 1``: a second worker would run the same job on the same interval
and race it -- two workers flipping the same post to ``PUBLISHED`` and writing
two activity-log entries and two "published" notifications for one post.

The fix is a lease rather than a deployment constraint. Before each run a
worker must hold a short-lived lock; only the holder does the work, and the
lease expires on its own so a worker that is killed mid-run does not block
publishing forever.

* **With Redis** (``REDIS_URL`` set): a ``SET NX EX`` lease shared across every
  worker and instance. Safe to run ``--workers 4``.
* **Without Redis**: an OS file lock, which still guarantees one runner per
  machine -- correct for a single-instance deployment, and the honest limit is
  logged at startup so it is not mistaken for cluster-wide safety.

Jobs are also made non-overlapping (``max_instances=1``) and given a
``misfire_grace_time``, so a run that takes longer than the interval queues
rather than starting a second copy on top of itself.
"""
from __future__ import annotations

import atexit
import logging
import os
import signal
import threading

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler(
    daemon=True,
    job_defaults={
        # A slow Firestore round-trip must not stack a second copy of the
        # publisher on top of the first.
        'max_instances': 1,
        'coalesce': True,
        # If the process was busy and a fire time slipped, still run it when
        # within grace; beyond that, skip rather than replay a backlog.
        'misfire_grace_time': 30,
    },
    timezone='UTC',
)

_LOCK_KEY = 'scheduler:publisher:lease'
_lock_backend = None


class _RedisLease:
    """Cluster-wide single-runner lease on Redis.

    ``SET key value NX EX ttl`` is atomic, so exactly one worker acquires it.
    The lease is *not* renewed while work is in progress: the TTL is set well
    above the expected run time, and letting it lapse is preferable to a
    renewal loop that can keep a lease alive after its holder has died.
    """

    def __init__(self, url, ttl):
        import redis
        self._client = redis.Redis.from_url(
            url, socket_timeout=1.0, socket_connect_timeout=1.0,
            decode_responses=True,
        )
        self._client.ping()
        self._ttl = ttl
        self._owner = f'{os.getpid()}@{threading.get_ident()}'

    def acquire(self):
        try:
            return bool(self._client.set(_LOCK_KEY, self._owner, nx=True, ex=self._ttl))
        except Exception as exc:
            # Redis being unreachable must not stop publishing entirely. Running
            # is the safer failure here: a duplicate publish is idempotent
            # (status is set to the same value) whereas a post that never
            # publishes is a silent content failure the user cannot see.
            logger.warning('Scheduler lease unavailable, running anyway: %s', exc)
            return True

    def release(self):
        try:
            # Only delete our own lease: a lease that already expired may now
            # belong to another worker, and deleting it would let a third run
            # concurrently.
            if self._client.get(_LOCK_KEY) == self._owner:
                self._client.delete(_LOCK_KEY)
        except Exception:
            pass

    @property
    def scope(self):
        return 'cluster (Redis lease)'


class _FileLease:
    """Single-runner lease within one machine, via an OS file lock.

    Non-blocking, so a second worker fails immediately instead of piling up
    waiting threads. Correct for a single-instance deployment; across instances
    each machine would still get one runner, which is why the scope is reported
    honestly at startup.
    """

    def __init__(self):
        import tempfile
        self._path = os.path.join(tempfile.gettempdir(), 'scriptly-scheduler.lock')
        self._handle = None

    def acquire(self):
        try:
            self._handle = open(self._path, 'a+b')
        except OSError as exc:
            logger.warning('Scheduler lock file unavailable: %s', exc)
            return True

        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            self._handle.close()
            self._handle = None
            return False

    def release(self):
        if self._handle is None:
            return
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            self._handle.close()
            self._handle = None

    @property
    def scope(self):
        return 'single machine (file lock)'


def _with_lease(job):
    """Wrap a job so it only runs while this process holds the lease."""
    def guarded():
        backend = _lock_backend
        if backend is None:
            job()
            return
        if not backend.acquire():
            logger.debug('Skipping %s: another worker holds the lease', job.__name__)
            return
        try:
            job()
        finally:
            backend.release()
    guarded.__name__ = job.__name__
    return guarded


def publish_due_blogs():
    """Publish every blog whose scheduled time has passed."""
    from app.firebase.firestore_service import FirestoreService
    from app.utils.cache import cache

    try:
        db = FirestoreService()
        due = db.get_due_scheduled_blogs()
    except Exception:
        logger.exception('Scheduler could not read due blogs')
        return

    if not due:
        return

    published = 0
    for blog in due:
        blog_id = blog.get('id')
        title = blog.get('title', 'Untitled')
        try:
            if not db.update_blog_status(blog_id, 'PUBLISHED'):
                logger.warning(
                    'Scheduled publish reported no change',
                    extra={'blog_id': blog_id},
                )
                continue

            db.update_schedule_entry_status(blog_id, 'PUBLISHED')

            # The public site serves cached blog lists; without this the post
            # stays invisible for up to the cache TTL after publishing.
            site_owner_id = blog.get('site_owner_id') or blog.get('author_id')
            if site_owner_id:
                cache.clear_prefix(f'published_blogs:{site_owner_id}')

            db.log_activity(
                user_id=blog.get('scheduled_by', 'system'),
                user_name='Scheduler',
                type='status_change',
                action_text='auto-published (scheduled)',
                blog_title=title,
            )
            published += 1
            logger.info(
                'Auto-published scheduled blog',
                extra={'blog_id': blog_id, 'title': title[:80]},
            )
        except Exception:
            # One bad document must not stop the rest of the batch.
            logger.exception(
                'Failed to auto-publish blog', extra={'blog_id': blog_id}
            )

    if published:
        logger.info('Scheduler published %s of %s due blogs', published, len(due))


def cleanup_expired_tasks():
    """Drop finished background-task records and agent turn logs.

    Both tables are per-process in-memory state, so this runs without the lease
    -- each worker owns its own and must sweep it. They are swept together
    because they describe the same work from two angles: the task record is
    "was it running", the turn log is "what did it say".
    """
    try:
        from app.utils.task_manager import task_manager
        removed = task_manager.cleanup_expired()
        if removed:
            logger.debug('Reclaimed %s finished task records', removed)
    except Exception:
        logger.exception('Task cleanup failed')

    try:
        from app.agent.events import turns
        dropped = turns.cleanup()
        if dropped:
            logger.debug('Reclaimed %s finished agent turn logs', dropped)
    except Exception:
        logger.exception('Agent turn cleanup failed')


def purge_agent_confirmations():
    """Delete spent and expired destructive-action tokens.

    Firestore, so it is lease-guarded: every worker running it would issue the
    same query and pay for the same reads. Unlike the sweeps above this is not
    a correctness control -- a consumed token is inert and an expired one is
    refused at redemption -- so its only job is keeping a collection that only
    grows from doing so forever.
    """
    try:
        from app.firebase.firestore_service import FirestoreService
        purged = FirestoreService().purge_expired_confirmations()
        if purged:
            logger.debug('Purged %s expired agent confirmations', purged)
    except Exception:
        logger.exception('Agent confirmation purge failed')


def init_scheduler(app):
    """Start the scheduler unless disabled, and register graceful shutdown."""
    global _lock_backend

    if not app.config.get('SCHEDULER_ENABLED', True):
        app.logger.info('Scheduler disabled by configuration')
        return

    # Flask's reloader runs the module twice; only the child process (where
    # WERKZEUG_RUN_MAIN is set) should own the jobs, or every debug session
    # publishes twice.
    if app.debug and os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        app.logger.debug('Scheduler deferred to the reloader child process')
        return

    if scheduler.running:
        app.logger.debug('Scheduler already running')
        return

    ttl = app.config.get('SCHEDULER_LOCK_TTL_SECONDS', 90)
    redis_url = app.config.get('REDIS_URL')
    if redis_url:
        try:
            _lock_backend = _RedisLease(redis_url, ttl)
        except Exception as exc:
            app.logger.warning(
                'Redis lease unavailable (%s); falling back to a file lock', exc
            )
            _lock_backend = _FileLease()
    else:
        _lock_backend = _FileLease()

    interval = app.config.get('SCHEDULER_INTERVAL_SECONDS', 60)

    scheduler.add_job(
        func=_with_lease(publish_due_blogs),
        trigger=IntervalTrigger(seconds=interval),
        id='publish_scheduled_blogs',
        name='Publish scheduled blogs',
        replace_existing=True,
    )
    # Local bookkeeping only -- each process owns its own task table, so this
    # one deliberately runs without the lease.
    scheduler.add_job(
        func=cleanup_expired_tasks,
        trigger=IntervalTrigger(seconds=300),
        id='cleanup_expired_tasks',
        name='Clean up finished background tasks',
        replace_existing=True,
    )

    # Firestore-backed and therefore lease-guarded: without the lease every
    # instance would pay for the same query. Half-hourly, because the tokens it
    # removes are already inert -- this is cost control, not a safety control.
    scheduler.add_job(
        func=_with_lease(purge_agent_confirmations),
        trigger=IntervalTrigger(seconds=1800),
        id='purge_agent_confirmations',
        name='Purge expired agent confirmations',
        replace_existing=True,
    )

    scheduler.start()
    app.logger.info(
        'Scheduler started: publishing every %ss, single-runner scope: %s',
        interval, _lock_backend.scope,
    )

    _register_shutdown(app)


def _register_shutdown(app):
    """Drain the scheduler and the task pool on SIGTERM/SIGINT.

    A deploy sends SIGTERM. Without this the process dies mid-job: a blog can
    be marked ``PUBLISHED`` with its schedule entry left ``SCHEDULED``, and an
    in-flight generation is lost with no record of why.
    """
    def shutdown(*_args):
        logger.info('Shutdown signal received; draining')
        try:
            if scheduler.running:
                scheduler.shutdown(wait=False)
        except Exception:
            pass
        try:
            from app.utils.task_manager import task_manager
            task_manager.shutdown(wait=True, timeout=25)
        except Exception:
            pass
        if _lock_backend is not None:
            _lock_backend.release()

    atexit.register(shutdown)

    # Signal handlers can only be installed on the main thread; under gunicorn
    # the arbiter owns them, so this is best-effort and atexit is the backstop.
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            previous = signal.getsignal(sig)

            def handler(signum, frame, _previous=previous):
                shutdown()
                if callable(_previous) and _previous not in (
                    signal.SIG_DFL, signal.SIG_IGN
                ):
                    _previous(signum, frame)

            signal.signal(sig, handler)
        except (ValueError, OSError, AttributeError):
            app.logger.debug('Could not install handler for %s', sig)
