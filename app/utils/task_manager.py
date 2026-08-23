"""Bounded background task pool for long-running AI work.

Blog generation and humanisation take minutes, so they run off-request: the
route submits a task and returns ``202`` with a task id, and the browser polls
for progress. The original implementation had three problems that only show up
under concurrency.

**A hardcoded ceiling of two workers.** Two AI generations platform-wide,
across every user, with no way to change it without editing code.

**An unbounded, invisible queue.** ``ThreadPoolExecutor.submit`` always
accepts. The third concurrent user was silently queued behind two multi-minute
jobs with no feedback -- their request looked accepted and simply never
progressed. Under a burst, hundreds of jobs could queue and each holds its
captured arguments in memory. Now the queue has a depth limit, submission past
it raises :class:`CapacityError` so the caller gets an honest ``503``, and the
polling response reports queue position.

**Unbounded task retention.** The task dict only shrank when the periodic
cleanup ran; a burst between two cleanups grew it without limit. There is now a
hard cap with oldest-terminal-first eviction, so memory cannot run away even if
the cleanup job stops.

Task state is still per-process, which is correct only while the AI work itself
runs in-process. The trade-off, and the migration path to a shared broker, is
documented in :meth:`TaskManager.stats` and in the deployment notes.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from app.core.errors import CapacityError, NotFoundError

logger = logging.getLogger(__name__)

# Terminal states: a task in one of these will never change again, so it is
# eligible for eviction and its result is safe to cache client-side.
TERMINAL_STATES = frozenset(('completed', 'failed', 'cancelled'))

# Absolute ceiling on tracked tasks, independent of the TTL sweep. Each record
# is small, but the pool's pending work holds whole prompts and draft bodies.
_MAX_TRACKED_TASKS = 500


class TaskManager:
    """Fixed-size worker pool with a bounded admission queue."""

    def __init__(self, max_workers=4, max_queue_depth=20, retention_seconds=1800):
        self._tasks = {}
        self._lock = threading.RLock()
        self._max_workers = max(1, max_workers)
        self._max_queue_depth = max(1, max_queue_depth)
        self._retention_seconds = retention_seconds
        self._executor = ThreadPoolExecutor(
            max_workers=self._max_workers, thread_name_prefix='task'
        )
        # Tracked explicitly because ThreadPoolExecutor exposes no reliable
        # count of running-versus-queued work across Python versions.
        self._running = 0
        self._queued = 0
        self._submitted = 0
        self._rejected = 0
        self._shutdown = False

    # --- Configuration ----------------------------------------------------

    def configure(self, *, max_workers=None, max_queue_depth=None,
                  retention_seconds=None):
        """Apply settings from config. Called once from the app factory.

        Resizing the pool replaces the executor. Any in-flight task keeps
        running on the old one, which is drained without waiting -- a resize is
        a startup action, so in practice there is nothing in flight.
        """
        with self._lock:
            if retention_seconds is not None:
                self._retention_seconds = retention_seconds
            if max_queue_depth is not None:
                self._max_queue_depth = max(1, max_queue_depth)
            if max_workers is not None and max_workers != self._max_workers:
                old = self._executor
                self._max_workers = max(1, max_workers)
                self._executor = ThreadPoolExecutor(
                    max_workers=self._max_workers, thread_name_prefix='task'
                )
                old.shutdown(wait=False)
            logger.info(
                'Task pool configured',
                extra={'max_workers': self._max_workers,
                       'max_queue_depth': self._max_queue_depth,
                       'retention_seconds': self._retention_seconds},
            )

    # --- Admission --------------------------------------------------------

    @property
    def has_capacity(self):
        with self._lock:
            return self._queued < self._max_queue_depth

    def create_task(self, user_id, *, kind='generation'):
        """Register a task and return its id.

        Raises :class:`CapacityError` when the queue is full, so the route can
        answer ``503`` with a ``Retry-After`` instead of accepting work it
        cannot start. Admission is checked here rather than in :meth:`submit`
        because the id must not be handed out for work that was refused.
        """
        with self._lock:
            if self._shutdown:
                raise CapacityError('The server is shutting down. Please retry shortly.')

            if self._queued >= self._max_queue_depth:
                self._rejected += 1
                logger.warning(
                    'Task rejected: queue full',
                    extra={'user_id': user_id, 'queued': self._queued,
                           'max_queue_depth': self._max_queue_depth},
                )
                raise CapacityError(
                    f'{self._queued} jobs are already waiting. '
                    'Please try again in a minute.'
                )

            self._evict_if_needed_locked()

            task_id = uuid.uuid4().hex
            now = time.time()
            self._tasks[task_id] = {
                'id': task_id,
                'user_id': user_id,
                'kind': kind,
                'status': 'pending',
                'progress': 0,
                'stage': 'queued',
                'result': None,
                'error': None,
                'error_code': None,
                'created_at': now,
                'updated_at': now,
                'started_at': None,
                'finished_at': None,
            }
            self._queued += 1
            self._submitted += 1
            return task_id

    def submit(self, task_id, fn, *args, **kwargs):
        """Hand ``fn`` to the pool, wrapped so no failure is ever silent.

        The wrapper guarantees three things the original did not: the task is
        marked ``running`` when a worker actually picks it up (not when it was
        queued), the queue counter is decremented exactly once, and an
        exception that escapes ``fn`` marks the task failed instead of being
        swallowed by the executor's future -- which nothing was ever reading.
        """
        def runner():
            with self._lock:
                self._queued = max(0, self._queued - 1)
                self._running += 1
                task = self._tasks.get(task_id)
                if task is not None:
                    task['status'] = 'running'
                    task['stage'] = 'starting'
                    task['started_at'] = time.time()
                    task['updated_at'] = task['started_at']
            started = time.perf_counter()
            try:
                fn(task_id, *args, **kwargs)
            except Exception as exc:
                logger.exception(
                    'Background task crashed',
                    extra={'task_id': task_id, 'kind': kwargs.get('kind')},
                )
                self.fail_task(task_id, 'The job failed unexpectedly.', exc=exc)
            finally:
                with self._lock:
                    self._running = max(0, self._running - 1)
                logger.info(
                    'Background task finished',
                    extra={'task_id': task_id,
                           'duration_ms': round((time.perf_counter() - started) * 1000, 1)},
                )

        self._executor.submit(runner)

    # --- Progress ---------------------------------------------------------

    def update_task(self, task_id, stage, progress):
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task['status'] in TERMINAL_STATES:
                return
            task.update({
                'status': 'running',
                'stage': stage,
                # Clamp so a caller passing 120 cannot make the UI show a
                # progress bar past its end.
                'progress': max(0, min(100, int(progress))),
                'updated_at': time.time(),
            })

    def complete_task(self, task_id, result):
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            now = time.time()
            task.update({
                'status': 'completed',
                'progress': 100,
                'stage': 'completed',
                'result': result,
                'error': None,
                'updated_at': now,
                'finished_at': now,
            })

    def fail_task(self, task_id, error, *, code=None, exc=None):
        """Mark a task failed with a message the user is allowed to see.

        ``error`` is user-facing; ``exc`` is logged in full and never returned.
        Keeping those separate is what stops a raw Firestore or gRPC message
        from reaching the browser.
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            now = time.time()
            task.update({
                'status': 'failed',
                'stage': 'failed',
                'error': str(error)[:300],
                'error_code': code,
                'updated_at': now,
                'finished_at': now,
            })
        if exc is not None:
            logger.error('Task %s failed: %s', task_id, exc, exc_info=exc)

    def cancel_task(self, task_id):
        """Mark a queued task cancelled.

        Cannot interrupt work already running -- Python has no safe thread
        cancellation -- so a running task is left alone and reported as such.
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            if task['status'] in TERMINAL_STATES:
                return False
            if task['status'] == 'running':
                return False
            now = time.time()
            task.update({
                'status': 'cancelled', 'stage': 'cancelled',
                'updated_at': now, 'finished_at': now,
            })
            self._queued = max(0, self._queued - 1)
            return True

    # --- Reads ------------------------------------------------------------

    def get_task(self, task_id):
        """A copy of the task record, or ``None``.

        A copy because the caller serialises it outside the lock, and handing
        out the live dict lets a worker mutate it mid-serialisation.
        """
        with self._lock:
            task = self._tasks.get(task_id)
            return dict(task) if task else None

    def get_task_for_user(self, task_id, user_id):
        """Fetch a task, enforcing ownership.

        Raises :class:`NotFoundError` for both "no such task" and "not yours",
        so polling cannot be used to discover that another user's task id
        exists.
        """
        task = self.get_task(task_id)
        if task is None or task.get('user_id') != user_id:
            raise NotFoundError('That job was not found or has expired.')
        return task

    def queue_position(self, task_id):
        """1-based position among pending tasks, or ``None`` if not pending.

        Lets the polling response say "3rd in line" instead of leaving the
        user watching a bar that has not moved.
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task['status'] != 'pending':
                return None
            pending = sorted(
                (t for t in self._tasks.values() if t['status'] == 'pending'),
                key=lambda t: t['created_at'],
            )
            for index, candidate in enumerate(pending, start=1):
                if candidate['id'] == task_id:
                    return index
            return None

    def stats(self):
        """Pool telemetry for /healthz.

        ``shared_across_workers`` is False by design: this pool lives inside the
        web process. Multiple workers each get their own, so the effective
        concurrency is ``workers x max_workers`` and a task submitted to one
        worker is invisible to the others. That is why the polling endpoint must
        be sticky or the deployment single-worker. Moving to Celery/RQ is the
        fix when AI throughput needs to scale past one instance.
        """
        with self._lock:
            by_status = {}
            for task in self._tasks.values():
                by_status[task['status']] = by_status.get(task['status'], 0) + 1
            return {
                'max_workers': self._max_workers,
                'max_queue_depth': self._max_queue_depth,
                'running': self._running,
                'queued': self._queued,
                'tracked': len(self._tasks),
                'submitted_total': self._submitted,
                'rejected_total': self._rejected,
                'by_status': by_status,
                'shared_across_workers': False,
            }

    # --- Maintenance ------------------------------------------------------

    def cleanup_expired(self, max_age=None):
        """Drop terminal tasks older than the retention window.

        Only terminal ones: a job legitimately running for longer than the
        window must not have its status record deleted out from under the
        browser that is polling it.
        """
        cutoff = time.time() - (max_age or self._retention_seconds)
        with self._lock:
            expired = [
                task_id for task_id, task in self._tasks.items()
                if task['status'] in TERMINAL_STATES
                and (task.get('finished_at') or task['created_at']) < cutoff
            ]
            for task_id in expired:
                del self._tasks[task_id]
        if expired:
            logger.debug('Cleaned up %s expired tasks', len(expired))
        return len(expired)

    def _evict_if_needed_locked(self):
        """Enforce the hard tracking cap, oldest terminal task first."""
        if len(self._tasks) < _MAX_TRACKED_TASKS:
            return
        terminal = sorted(
            (t for t in self._tasks.values() if t['status'] in TERMINAL_STATES),
            key=lambda t: t.get('finished_at') or t['created_at'],
        )
        for task in terminal[: max(1, _MAX_TRACKED_TASKS // 10)]:
            self._tasks.pop(task['id'], None)
        if len(self._tasks) >= _MAX_TRACKED_TASKS:
            # Every tracked task is still active. Refusing is the only honest
            # answer; accepting would grow memory without bound.
            logger.error(
                'Task table full with %s non-terminal tasks', len(self._tasks)
            )
            raise CapacityError()

    def shutdown(self, wait=True, timeout=30):
        """Stop accepting work and let in-flight tasks finish.

        Called from the SIGTERM handler. Without it, a deploy kills running
        generations mid-write, leaving a half-saved draft and a browser polling
        a task id that no longer exists anywhere.
        """
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
            in_flight = self._running
        logger.info('Task pool draining (%s in flight)', in_flight)
        try:
            self._executor.shutdown(wait=wait, cancel_futures=True)
        except TypeError:
            # cancel_futures landed in 3.9; keep working on anything older.
            self._executor.shutdown(wait=wait)
        logger.info('Task pool drained')


# Module-level singleton, reconfigured (never replaced) by the app factory so
# imports taken at module scope stay valid.
task_manager = TaskManager()
