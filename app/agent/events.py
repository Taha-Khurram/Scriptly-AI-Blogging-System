"""The event log for one agent turn, and the registry of live turns.

A turn is not a request. The user sends a message, the agent may search the
web, propose an outline, write a 1,100-word post and file it -- that is minutes
of work with a dozen things worth showing on screen, and the HTTP request that
started it will not survive it. So a turn runs in the background task pool and
writes here; the browser *attaches* to the log rather than holding the work
open.

Why an event log rather than a stream
-------------------------------------

The obvious design is to write tokens straight into an SSE response. It is also
the design that loses the turn whenever the connection blinks -- and on a
several-minute turn, a blink is the normal case, not the edge case. A log with
cursors gives three properties a raw stream cannot:

* **Reattachment.** A browser that navigated away, slept, or dropped its
  connection asks for "everything after event 41" and gets it. It replays the
  turn instead of joining it blind or, worse, showing an empty screen while the
  agent is mid-post.
* **Two transports over one truth.** The SSE endpoint tails this; the polling
  endpoint slices it. They cannot disagree about what happened, because there
  is only one record of what happened.
* **A bounded SSE lifetime.** This application runs gthread with a small fixed
  thread count (see :mod:`app.utils.task_manager`), so an SSE response held
  open for a whole turn pins a worker thread for minutes. Because the log is
  the source of truth, an SSE response can instead expire after
  :data:`SSE_MAX_SECONDS` and tell the client to reconnect from its cursor --
  the same mechanism reattachment already needs, so it costs no extra code and
  no pinned threads.

Ordering is by insertion index, and the index is the cursor. Nothing here is
keyed by time: two events in the same millisecond are common (a tool result and
the status change it caused), and a cursor that cannot distinguish them either
repeats or skips.

Lifetime
--------

In-process and deliberately ephemeral. The *durable* record of a turn is what
:mod:`app.repositories.chat` stores when it finishes -- the messages, the tool
audit trail, the cards. This log is the live view, and it is evicted on the same
retention terms as the task record it belongs to. A user who reloads an hour
later reads the conversation from Firestore; a user who reloads mid-turn
reattaches here.
"""
from __future__ import annotations

import threading
import time
import uuid

from app.core.errors import NotFoundError

from app.core.logging import get_logger

logger = get_logger(__name__)


# --- Event types ----------------------------------------------------------
#
# One name per thing the UI does with an event. They are strings rather than an
# enum because they are serialised into JSON and read by a template and a
# script -- a value that has to be spelled the same in three languages is
# clearer as a constant than as a member.

STATUS = 'status'          # what the agent is doing now: {stage, label}
THOUGHT = 'thought'        # a line of reasoning: {text}
TOOL_START = 'tool_start'  # {name, label, args}
TOOL_END = 'tool_end'      # {name, ok, summary, duration_ms}
TOKEN = 'token'            # a delta of the agent's prose reply: {text}
DRAFT = 'draft'            # a delta of blog text being written: {text}
CARD = 'card'              # a structured attachment: {kind, data}
MESSAGE = 'message'        # the reply was persisted: {message_id, seq}
DONE = 'done'              # terminal: {status, blog_id?, ...}
ERROR = 'error'            # terminal: {message, code}

TERMINAL_TYPES = frozenset((DONE, ERROR))


# --- Bounds ---------------------------------------------------------------

# A turn that produces more events than this is looping, and the loop guard
# will have stopped it long before -- this is the backstop for the buffer, not
# the policy for the agent.
MAX_EVENTS = 2_000

# Prose and draft deltas are coalesced (see `_append_text`), so this bounds the
# total text one turn may hold, not the number of chunks. One long blog post is
# ~8 KB; the ceiling is for a model that will not stop.
MAX_TEXT_CHARS = 80_000

# Largest slice one read may carry. A client further behind than this catches up
# across consecutive reads, which is what the cursor is for.
MAX_EVENTS_PER_READ = 400

# How long an SSE response stays open before asking the client to reconnect.
# Short enough that a worker thread is never held for a whole turn; long enough
# that a normal turn needs no reconnect at all.
SSE_MAX_SECONDS = 90

# Live turns tracked at once, and how long a finished one stays attachable.
# A finished turn is worth keeping briefly: the browser's last poll may not have
# arrived yet, and a reload seconds after completion should show the reply
# rather than an empty pane.
MAX_TRACKED_TURNS = 200
RETENTION_SECONDS = 900


class TurnLog:
    """The ordered events of one turn, sliceable by cursor.

    Every method takes the lock. Writes come from the worker thread running the
    turn; reads come from whichever request thread is serving the browser, and
    those are different threads by construction.
    """

    __slots__ = ('turn_id', 'session_id', 'user_id', '_events', '_lock',
                 'created_at', 'finished_at', 'status', '_chars', '_truncated')

    def __init__(self, turn_id, session_id, user_id):
        self.turn_id = turn_id
        self.session_id = session_id
        self.user_id = user_id
        self._events = []
        self._lock = threading.RLock()
        self.created_at = time.time()
        self.finished_at = None
        self.status = 'running'
        self._chars = 0
        self._truncated = False

    # --- Writing ----------------------------------------------------------

    def emit(self, event_type, **data):
        """Append one event. Returns its index, or ``-1`` if it was dropped.

        Text events (:data:`TOKEN`, :data:`DRAFT`) are coalesced into the
        previous event of the same type when one is adjacent. A model emits
        tokens in bursts of a few characters; without coalescing a 1,000-word
        post is several thousand log entries, and a reattaching client pays for
        every one of them.
        """
        with self._lock:
            if self.status != 'running' and event_type not in TERMINAL_TYPES:
                # After a terminal event the log is closed. A late write from a
                # worker that has not noticed it failed must not append after
                # `done`, or a client would see the turn end and then continue.
                return -1

            if event_type in (TOKEN, DRAFT):
                text = data.get('text') or ''
                if not text:
                    return -1
                room = MAX_TEXT_CHARS - self._chars
                if room <= 0:
                    self._truncated = True
                    return -1
                if len(text) > room:
                    text = text[:room]
                    self._truncated = True
                self._chars += len(text)
                data = dict(data, text=text)

                if self._events and self._events[-1]['type'] == event_type:
                    self._events[-1]['data']['text'] += text
                    return self._events[-1]['i']

            if len(self._events) >= MAX_EVENTS:
                self._truncated = True
                return -1

            event = {
                'i': len(self._events),
                'type': event_type,
                'at': round(time.time() - self.created_at, 2),
                'data': data,
            }
            self._events.append(event)

            if event_type in TERMINAL_TYPES:
                self.status = 'failed' if event_type == ERROR else 'completed'
                self.finished_at = time.time()

            return event['i']

    # Convenience writers. They exist so the loop and the tools read as prose
    # rather than as a series of string literals, and so a typo in an event
    # name is an AttributeError here instead of an event the UI silently drops.

    def status_(self, stage, label=''):
        return self.emit(STATUS, stage=stage, label=label)

    def thought(self, text):
        return self.emit(THOUGHT, text=str(text)[:400])

    def token(self, text):
        return self.emit(TOKEN, text=text)

    def draft(self, text):
        return self.emit(DRAFT, text=text)

    def card(self, kind, data):
        return self.emit(CARD, kind=kind, data=data or {})

    def tool_start(self, name, label='', args=None):
        return self.emit(TOOL_START, name=name, label=label, args=args or {})

    def tool_end(self, name, ok, summary='', duration_ms=0):
        return self.emit(TOOL_END, name=name, ok=bool(ok), summary=summary,
                         duration_ms=round(float(duration_ms or 0), 1))

    def done(self, **data):
        return self.emit(DONE, status='completed', **data)

    def fail(self, message, code=None):
        return self.emit(ERROR, message=str(message)[:400], code=code)

    # --- Reading ----------------------------------------------------------

    def since(self, cursor=0, limit=MAX_EVENTS_PER_READ):
        """Events after ``cursor``, plus where the caller now is.

        ``cursor`` is the index of the last event the caller has, so the read
        starts at ``cursor + 1``; ``-1`` (the default for a client that has
        nothing) returns the log from the beginning. A cursor beyond the end is
        clamped rather than an error: the worst outcome is an empty page, and
        raising in the middle of an otherwise healthy turn is worse.

        The events are deep-ish copied -- the ``data`` dict of a coalesced text
        event is still being appended to by the worker, and handing out the live
        dict lets it grow mid-serialisation.
        """
        with self._lock:
            start = max(0, int(cursor) + 1) if cursor is not None else 0
            start = min(start, len(self._events))
            window = self._events[start:start + max(1, int(limit))]
            events = [
                {'i': e['i'], 'type': e['type'], 'at': e['at'], 'data': dict(e['data'])}
                for e in window
            ]
            return {
                'events': events,
                'cursor': events[-1]['i'] if events else max(-1, int(cursor if cursor is not None else -1)),
                'status': self.status,
                'has_more': (start + len(window)) < len(self._events),
                'truncated': self._truncated,
                'turn_id': self.turn_id,
                'session_id': self.session_id,
            }

    @property
    def is_terminal(self):
        with self._lock:
            return self.status != 'running'

    def snapshot(self):
        """Everything the log holds, for diagnostics. Not a hot path."""
        with self._lock:
            return {
                'turn_id': self.turn_id,
                'session_id': self.session_id,
                'status': self.status,
                'events': len(self._events),
                'chars': self._chars,
                'truncated': self._truncated,
                'age_s': round(time.time() - self.created_at, 1),
            }


class TurnRegistry:
    """Live turns, addressable by id and evicted on the same terms as tasks.

    Not part of :class:`~app.utils.task_manager.TaskManager` on purpose. That
    class owns *concurrency* -- a bounded pool, an admission queue, queue
    position. This owns *what one turn said*. Keeping them apart is what lets
    the chat feature have a typed event log without pushing chat-shaped fields
    into the machinery that also runs humanisation jobs.

    Shares the task manager's per-process limitation, and for the same reason:
    the work runs in this process. A turn started on one worker is invisible to
    another, which is why the deployment is single-worker (``WEB_CONCURRENCY=1``,
    documented in ``.env.example``) until the AI work moves to a shared broker.
    """

    def __init__(self):
        self._turns = {}
        self._lock = threading.RLock()

    def open(self, session_id, user_id):
        """Create and register a log. Returns it."""
        turn_id = uuid.uuid4().hex
        log = TurnLog(turn_id, session_id, user_id)
        with self._lock:
            self._evict_locked()
            self._turns[turn_id] = log
        return log

    def get(self, turn_id):
        with self._lock:
            return self._turns.get(turn_id)

    def get_for_user(self, turn_id, user_id):
        """Fetch a log, enforcing ownership.

        Raises :class:`NotFoundError` for both "no such turn" and "not yours",
        so a caller cannot use attach requests to discover that another user's
        turn id exists -- the same rule ``task_manager.get_task_for_user``
        follows.
        """
        log = self.get(turn_id)
        if log is None or log.user_id != user_id:
            raise NotFoundError('That turn was not found or has expired.')
        return log

    def latest_for_session(self, session_id, user_id):
        """The most recent live turn in a session, or ``None``.

        What a reloading browser needs: it knows which conversation it is in
        but not which turn was in flight when the tab was closed.
        """
        with self._lock:
            candidates = [
                log for log in self._turns.values()
                if log.session_id == session_id and log.user_id == user_id
            ]
        if not candidates:
            return None
        return max(candidates, key=lambda log: log.created_at)

    def cleanup(self, max_age=RETENTION_SECONDS):
        """Drop finished turns past the retention window.

        Only finished ones: a turn legitimately running longer than the window
        must not have its log deleted out from under the browser attached to it.
        """
        cutoff = time.time() - max_age
        with self._lock:
            expired = [
                turn_id for turn_id, log in self._turns.items()
                if log.is_terminal and (log.finished_at or log.created_at) < cutoff
            ]
            for turn_id in expired:
                del self._turns[turn_id]
        if expired:
            logger.debug('Cleaned up %s finished agent turns', len(expired))
        return len(expired)

    def _evict_locked(self):
        """Enforce the hard cap, oldest finished turn first."""
        if len(self._turns) < MAX_TRACKED_TURNS:
            return
        finished = sorted(
            (log for log in self._turns.values() if log.is_terminal),
            key=lambda log: log.finished_at or log.created_at,
        )
        for log in finished[: max(1, MAX_TRACKED_TURNS // 10)]:
            self._turns.pop(log.turn_id, None)
        if len(self._turns) >= MAX_TRACKED_TURNS:
            # Every tracked turn is still running. Evicting the oldest live one
            # is the least-bad answer: it is the one most likely to be
            # abandoned, and its work continues -- only the live view is lost,
            # and the durable record is written either way.
            oldest = min(self._turns.values(), key=lambda log: log.created_at)
            logger.warning(
                'Turn registry full with live turns; dropping the live view of '
                'the oldest (%s). Its result is still written to Firestore.',
                oldest.turn_id,
            )
            self._turns.pop(oldest.turn_id, None)

    def stats(self):
        """Reported by /healthz, so a leak here is visible before it is RSS."""
        with self._lock:
            logs = list(self._turns.values())
        return {
            'tracked': len(logs),
            'running': sum(1 for log in logs if not log.is_terminal),
            'shared_across_workers': False,
        }


#: Module-level singleton, mirroring `task_manager`: imported at module scope by
#: the routes and the loop, never replaced.
turns = TurnRegistry()
