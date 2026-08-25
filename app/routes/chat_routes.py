"""The Studio: sessions, turns, approvals and confirmations.

Serves ``/create`` -- the create screen, which is now a conversation. There is no
separate chat tab, because creating a post, revising it and clearing out an old
one are all the same activity now, and two screens offering two ways to do it
would mean the reader has to decide which one a request belongs to before making
it.

Its own blueprint rather than more routes in ``blog_routes`` (already 2,000
lines across fifteen concerns), because nothing here touches a blog document
directly. It moves conversations around and starts turns; the turns call tools,
and the tools touch blogs.

The request/response shape, and why it is not one request
---------------------------------------------------------

Sending a message returns ``202`` with a ``turn_id``, not the reply. A turn can
search the web, plan a post, write eleven hundred words and file it -- minutes of
work, far longer than any sensible HTTP timeout, and the browser may close
halfway through. So the turn runs in the shared task pool and the browser
*attaches* to it:

``GET .../turns/<id>/stream``
    Server-sent events, the normal path. Bounded to
    :data:`~app.agent.events.SSE_MAX_SECONDS` and then it asks the client to
    reconnect from its cursor. That bound is not a limitation to work around --
    it is what keeps a small gthread pool from having every worker parked on an
    open connection. ``task_manager``'s module docstring explains why this app
    cannot hold one thread per waiting user.

``GET .../turns/<id>``
    The same event log by cursor poll. For a client that cannot use SSE, and for
    the reattach case where the browser needs a snapshot before it starts
    tailing.

Both read one :class:`~app.agent.events.TurnLog`, so they cannot disagree.

Two endpoints exist purely to keep authority with the user
----------------------------------------------------------

:func:`approve_outline` and :func:`confirm_action` are the *only* ways an outline
becomes approved or a post is deleted. The agent cannot reach either. That is
the human-in-the-loop guarantee expressed as routing: the model's tools can
propose, and a request from the user's own browser is what disposes.
"""
from __future__ import annotations

import json
import time

from flask import (
    Blueprint, Response, current_app, jsonify, redirect, render_template,
    request, session, url_for,
)

from app.agent.events import SSE_MAX_SECONDS, turns
from app.agent.loop import run_turn
from app.agent.tools.blogs import execute_confirmed_delete
from app.core.errors import ValidationError
from app.core.extensions import limiter
from app.core.logging import get_logger
from app.core.security import api_login_required, current_user
from app.firebase.firestore_service import FirestoreService
from app.repositories.chat import DEFAULT_HISTORY_LIMIT, MAX_MESSAGE_CHARS
from app.services.search_service import search
from app.utils.task_manager import task_manager

logger = get_logger(__name__)

chat_bp = Blueprint('chat', __name__)
db_service = FirestoreService()

# Ceiling on one chat message. Higher than the 2,000-character limit the
# single-shot composer used, because a message here can legitimately be a long
# editing brief -- but still bounded: an unbounded field is a token bill and a
# memory footprint.
MAX_CHAT_MESSAGE = MAX_MESSAGE_CHARS

# Sessions in the sidebar's first page.
SESSION_PAGE_SIZE = 25

# How often the SSE loop checks the log for new events. Fast enough that
# streamed text reads as typing, slow enough that a parked connection is not a
# spin loop -- at 120ms a whole turn is a few hundred cheap lock acquisitions.
SSE_TICK_SECONDS = 0.12

# A comment frame every few seconds. Proxies and load balancers close idle
# connections, and a heartbeat is the difference between a stream that survives
# a minute of the model thinking and one that dies silently.
SSE_HEARTBEAT_SECONDS = 15


@chat_bp.before_request
def require_login():
    """Blueprint-wide gate, as everywhere else in the dashboard.

    A hook rather than a decorator per route: the whole blueprint is behind the
    session, and a hook cannot be forgotten on the next route somebody adds.
    """
    if not session.get('logged_in'):
        if request.path.startswith('/api/'):
            return jsonify({'success': False, 'error': 'unauthenticated'}), 401
        return redirect(url_for('auth_bp.login'))


@chat_bp.after_request
def add_cache_headers(response):
    if request.headers.get('X-Pjax') and response.status_code == 200:
        response.headers['Cache-Control'] = 'private, max-age=10, stale-while-revalidate=30'
    return response


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

@chat_bp.route('/create')
def studio_page():
    """The Studio: the chat panel, its sidebar, and one conversation open in it.

    Served at ``/create`` because this **is** the create screen. There is no
    separate chat tab: creating a post, revising it, and clearing out an old one
    are all things you now do by talking, so a second page offering a different
    way to create would be two answers to one question -- and the reader would
    have to know which screen a request belonged to before making it.

    The old single-shot composer that lived here is gone. Its backend
    (``/api/generate``) is untouched and still tested; only the UI is replaced.

    The first page of sessions and the open conversation's messages are both
    server-rendered. They are the whole content of the screen, and shipping an
    empty shell that then asks for its own contents costs two round trips to
    show what the first response already knew -- on a Firestore round trip of
    0.5-3.5s, that is the difference between a page and a wait.
    """
    user = current_user()

    sessions = db_service.list_chat_sessions(user.id, limit=SESSION_PAGE_SIZE)

    # `?s=<id>` deep-links a conversation so it survives a reload and can be
    # linked to from the blog list.
    requested = (request.args.get('s') or '').strip()
    active = db_service.get_chat_session(requested, user.id) if requested else None

    messages = []
    if active:
        messages = db_service.get_chat_messages(active['id'], user.id,
                                                limit=DEFAULT_HISTORY_LIMIT)

    return render_template(
        # The template, the page script and the stylesheet keep their `chat`
        # names: the *feature* is the chat agent (chat_bp, /api/chat/*), and the
        # *page* it is presented as is the Studio. Renaming only the template
        # would split that naming for no gain.
        'chat.html',
        # Passed explicitly -- there is no context processor supplying it, and
        # the empty state greets the reader by name.
        username=user.name or 'there',
        sessions=sessions['items'],
        sessions_has_more=sessions['has_more'],
        sessions_cursor=sessions['next_cursor'],
        active_session=active,
        messages=messages,
        search_available=search.is_available,
    )


@chat_bp.route('/chat')
def chat_redirect():
    """``/chat`` was the studio's address for one release. Keep it working.

    Cheap to keep and it costs nothing: a bookmark, a link in a message, or a
    docs page written against the old URL lands on the page it meant instead of
    a 404. The query string carries over so a deep link to one conversation
    survives too.
    """
    return redirect(url_for('chat.studio_page', **request.args.to_dict()))


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

@chat_bp.route('/api/chat/sessions', methods=['GET'])
@api_login_required
def list_sessions():
    """The next page of the conversation rail, keyset-paged on ``before``."""
    user = current_user()
    page = db_service.list_chat_sessions(
        user.id,
        limit=request.args.get('limit', SESSION_PAGE_SIZE, type=int),
        before=request.args.get('before', ''),
    )
    return jsonify({'success': True, **page})


@chat_bp.route('/api/chat/sessions', methods=['POST'])
@api_login_required
def create_session():
    """Open a new conversation."""
    user = current_user()
    session_id = db_service.create_chat_session(user.id)
    if not session_id:
        return jsonify({'success': False,
                        'error': 'Could not start a conversation.'}), 500

    logger.info('Chat session created', extra={'session_id': session_id,
                                               'user_id': user.id})
    return jsonify({
        'success': True,
        'session': db_service.get_chat_session(session_id, user.id),
    }), 201


@chat_bp.route('/api/chat/sessions/<session_id>', methods=['GET'])
@api_login_required
def get_session(session_id):
    """One conversation and its messages, plus any turn still running in it.

    ``active_turn`` is what a browser returning to a conversation needs: the
    messages tell it what has been said, and this tells it whether the agent is
    still mid-sentence.
    """
    user = current_user()
    record = db_service.get_chat_session(session_id, user.id)
    if not record:
        return jsonify({'success': False,
                        'error': 'That conversation is no longer available.'}), 404

    messages = db_service.get_chat_messages(
        session_id, user.id,
        limit=request.args.get('limit', DEFAULT_HISTORY_LIMIT, type=int),
    )

    live = turns.latest_for_session(session_id, user.id)
    active_turn = None
    if live is not None and not live.is_terminal:
        active_turn = live.turn_id

    return jsonify({
        'success': True,
        'session': record,
        'messages': messages,
        'active_turn': active_turn,
    })


@chat_bp.route('/api/chat/sessions/<session_id>', methods=['POST'])
@api_login_required
def rename_session(session_id):
    """Rename a conversation. The only field a user may set directly."""
    user = current_user()
    data = request.get_json(silent=True) or {}
    title = (data.get('title') or '').strip()
    if not title:
        raise ValidationError('A title is required.')

    if not db_service.update_chat_session(session_id, user.id, title=title):
        return jsonify({'success': False,
                        'error': 'That conversation is no longer available.'}), 404
    return jsonify({'success': True, 'title': title[:120]})


@chat_bp.route('/api/chat/sessions/<session_id>', methods=['DELETE'])
@api_login_required
def delete_session(session_id):
    """Delete a conversation and its messages. Blogs are untouched.

    Said explicitly in the response, because a delete that quietly took more
    than the reader expected is the one they cannot undo -- and a conversation
    that produced five published posts looks, from the sidebar, like the thing
    those posts live in.
    """
    user = current_user()
    if not db_service.delete_chat_session(session_id, user.id):
        return jsonify({'success': False,
                        'error': 'That conversation is no longer available.'}), 404

    return jsonify({
        'success': True,
        'message': 'Conversation deleted. The posts it produced are untouched.',
    })


# ---------------------------------------------------------------------------
# Turns
# ---------------------------------------------------------------------------

@chat_bp.route('/api/chat/sessions/<session_id>/messages', methods=['POST'])
@api_login_required
@limiter.limit(lambda: current_app.config.get('RATELIMIT_CHAT', '90 per hour'))
def send_message(session_id):
    """Accept a message and start a turn. Returns ``202`` with a ``turn_id``.

    Rate-limited per user rather than per IP: a turn can spend minutes of model
    time, so the budget belongs to the account -- keying by address would let one
    user multiply their allowance by changing networks.

    ``create_task`` raises ``CapacityError`` when the pool's queue is full, which
    the error handler turns into a ``503`` with a ``Retry-After``. Deliberately
    not caught: "we are busy, try in a minute" is true and actionable, and a
    generic 500 is neither.
    """
    user = current_user()
    record = db_service.get_chat_session(session_id, user.id)
    if not record:
        return jsonify({'success': False,
                        'error': 'That conversation is no longer available.'}), 404

    data = request.get_json(silent=True) or {}
    message = (data.get('message') or '').strip()
    if not message:
        raise ValidationError('A message is required.')
    if len(message) > MAX_CHAT_MESSAGE:
        raise ValidationError(
            f'Message must be {MAX_CHAT_MESSAGE} characters or fewer.'
        )

    # One turn at a time per conversation. Two concurrent turns would both write
    # the focus pointer and both append replies, and the user would read an
    # interleaving of two answers to two questions.
    live = turns.latest_for_session(session_id, user.id)
    if live is not None and not live.is_terminal:
        return jsonify({
            'success': False,
            'error': 'agent_busy',
            'message': 'The agent is still working on the previous message.',
            'turn_id': live.turn_id,
        }), 409

    return _start_turn(record, user, message)


def _start_turn(record, user, message, *, role='user'):
    """Persist the incoming message and hand the turn to the task pool.

    History is read *before* the new message is appended, so the loop gets the
    conversation as it stood plus the new message separately -- which is the
    shape the model's ``contents`` needs, and avoids the new message appearing
    twice.
    """
    session_id = record['id']

    history = db_service.get_chat_messages(session_id, user.id,
                                           limit=DEFAULT_HISTORY_LIMIT)

    written = db_service.append_chat_message(session_id, user.id, role, message)
    if not written:
        return jsonify({'success': False,
                        'error': 'Your message could not be saved.'}), 500

    # The log is opened here, on the request thread, so the 202 can hand the
    # browser a turn id it can attach to immediately. Opening it inside the
    # worker would leave a window where the client has an id for a log that does
    # not exist yet.
    log = turns.open(session_id, user.id)

    app = current_app._get_current_object()
    task_id = task_manager.create_task(user.id, kind='chat_turn')
    task_manager.submit(
        task_id, _run_turn_task,
        app=app,
        log=log,
        session=record,
        user_id=user.id,
        user_name=user.name or 'there',
        user_role=user.role,
        message=message,
        history=history,
    )

    logger.info(
        'Chat turn queued',
        extra={'session_id': session_id, 'turn_id': log.turn_id,
               'task_id': task_id, 'user_id': user.id,
               'message_chars': len(message)},
    )

    return jsonify({
        'success': True,
        'turn_id': log.turn_id,
        'task_id': task_id,
        'message': {
            'id': written['id'],
            'seq': written['seq'],
            'role': role,
            'text': message,
        },
        'queue_position': task_manager.queue_position(task_id),
    }), 202


def _run_turn_task(task_id, app, log, session, user_id, user_name, user_role,
                   message, history):
    """The background worker: run one turn inside an app context.

    Its own ``FirestoreService`` rather than the module-level one, matching
    ``_run_generation_task``: construction is cheap (the client is a
    process-wide singleton) and a per-task instance keeps the request-scoped
    memo cache out of a job that outlives every request.

    Nothing here raises. ``run_turn`` handles its own failures and always writes
    a reply; this wrapper exists for the case where something outside it goes
    wrong -- and even then the turn log has to reach a terminal state, or a
    browser attaches to it forever.
    """
    with app.app_context():
        try:
            task_manager.update_task(task_id, 'thinking', 10)
            result = run_turn(
                FirestoreService(),
                session=session,
                user_id=user_id,
                user_name=user_name,
                user_role=user_role,
                message=message,
                history=history,
                search=search,
                log=log,
                max_iterations=app.config.get('AGENT_MAX_ITERATIONS', 7),
                deadline_seconds=app.config.get('AGENT_TURN_DEADLINE_SECONDS', 420),
            )
            if result.status == 'failed':
                task_manager.fail_task(task_id, result.error or 'The turn failed.')
            else:
                task_manager.complete_task(task_id, {
                    'message_id': result.message_id,
                    'blog_ids': result.blog_ids,
                })
        except Exception as exc:
            logger.exception('Chat turn task crashed',
                             extra={'turn_id': log.turn_id})
            task_manager.fail_task(task_id, 'The turn failed unexpectedly.', exc=exc)
            if not log.is_terminal:
                log.fail('Something went wrong on our side. Nothing was lost.')


@chat_bp.route('/api/chat/turns/<turn_id>', methods=['GET'])
@api_login_required
def poll_turn(turn_id):
    """Events after ``cursor``. The non-SSE path, and the reattach snapshot.

    ``cursor`` is the index of the last event the caller holds; omitting it
    means "from the beginning", which is what a browser that just reloaded
    wants -- it replays the turn rather than joining it blind.
    """
    user = current_user()
    log = turns.get_for_user(turn_id, user.id)
    payload = log.since(request.args.get('cursor', -1, type=int))
    return jsonify({'success': True, **payload})


@chat_bp.route('/api/chat/turns/<turn_id>/stream', methods=['GET'])
@api_login_required
def stream_turn(turn_id):
    """Tail a turn as server-sent events, for a bounded time.

    Three details that are load-bearing rather than decorative:

    * **Everything the generator needs is captured before it starts.** A Flask
      streaming response is consumed *after* the request context has been torn
      down, so touching ``request`` or ``session`` inside the loop would raise
      at the first tick. The log object and the cursor are resolved here.
    * **It ends on purpose.** After :data:`SSE_MAX_SECONDS` it sends a
      ``reconnect`` event and returns, freeing the worker thread. The client
      reopens from its cursor, which it must be able to do anyway for a dropped
      connection -- so the bound costs no extra client code.
    * **``Last-Event-ID`` is honoured.** The browser sends it automatically on
      an unclean reconnect, so a connection that dies mid-turn resumes exactly
      where it stopped without the client tracking anything.
    """
    user = current_user()
    log = turns.get_for_user(turn_id, user.id)

    cursor = request.args.get('cursor', type=int)
    if cursor is None:
        last_seen = request.headers.get('Last-Event-ID')
        try:
            cursor = int(last_seen) if last_seen is not None else -1
        except (TypeError, ValueError):
            cursor = -1

    def generate(cursor):
        started = time.time()
        last_beat = started

        # The retry hint the browser uses if it has to reconnect on its own.
        yield 'retry: 1500\n\n'

        while True:
            page = log.since(cursor)
            for event in page['events']:
                cursor = event['i']
                yield _sse(event['i'], event['type'], event)
                last_beat = time.time()

            if page['status'] != 'running' and not page['has_more']:
                # Terminal and drained. The client stops on this rather than on
                # the connection closing, so a close it did not expect can
                # still be told apart from a turn that finished.
                yield _sse(cursor, 'end', {'status': page['status']})
                return

            now = time.time()
            if now - started > SSE_MAX_SECONDS:
                # Handing the cursor back explicitly: the client reconnects with
                # it rather than inferring where it got to.
                yield _sse(cursor, 'reconnect', {'cursor': cursor})
                return

            if now - last_beat > SSE_HEARTBEAT_SECONDS:
                # A comment frame. Keeps intermediaries from reaping an idle
                # connection while the model is thinking.
                yield ': keep-alive\n\n'
                last_beat = now

            if not page['has_more']:
                time.sleep(SSE_TICK_SECONDS)

    return Response(
        generate(cursor),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache, no-transform',
            'Connection': 'keep-alive',
            # nginx buffers proxied responses by default, which would hold the
            # whole stream until the turn ended -- turning SSE into a slow
            # single response.
            'X-Accel-Buffering': 'no',
        },
    )


def _sse(event_id, event_type, data):
    """One SSE frame. ``id`` is the cursor, which is what makes resume work."""
    return (
        f'id: {event_id}\n'
        f'event: {event_type}\n'
        f'data: {json.dumps(data, default=str)}\n\n'
    )


# ---------------------------------------------------------------------------
# The two endpoints the agent cannot reach
# ---------------------------------------------------------------------------

@chat_bp.route('/api/chat/outlines/<outline_id>/approve', methods=['POST'])
@api_login_required
@limiter.limit(lambda: current_app.config.get('RATELIMIT_CHAT', '90 per hour'))
def approve_outline(outline_id):
    """Approve an outline, and start the turn that writes it.

    This route is the human in the loop. ``approve_outline`` on the repository
    is the only writer of ``status='approved'``, and this is the only caller of
    it that a user's own click reaches -- the agent has no tool that does.

    Writing is then started as a normal turn with a message saying what the user
    did. That is truthful (they clicked Approve), it appears in the transcript
    where a reader expects to see the decision, and it means the write goes
    through exactly the same path as "yes, go ahead" typed into the box -- one
    code path for one outcome.
    """
    user = current_user()

    outline = db_service.get_outline(outline_id, user.id)
    if not outline:
        return jsonify({'success': False,
                        'error': 'That outline is no longer available.'}), 404

    session_record = db_service.get_chat_session(
        outline.get('session_id') or '', user.id)
    if not session_record:
        return jsonify({'success': False,
                        'error': 'That conversation is no longer available.'}), 404

    approved = db_service.approve_outline(outline_id, user.id, via='ui')
    if not approved:
        return jsonify({
            'success': False,
            'error': (
                'That outline was replaced by a newer version — approve the '
                'current one instead.'
                if outline.get('status') == 'superseded'
                else 'The approval could not be saved.'
            ),
        }), 409

    logger.info('Outline approved via UI',
                extra={'outline_id': outline_id, 'user_id': user.id,
                       'session_id': session_record['id']})

    db_service.update_chat_session(session_record['id'], user.id,
                                   focus_outline_id=outline_id)
    # The session record was read before the update, so the copy handed to the
    # turn would still carry the old pointer -- and the turn renders it into the
    # state block.
    session_record['focus_outline_id'] = outline_id

    # `?write=0` approves without writing, for a user who wants the plan
    # locked in but not acted on yet.
    if request.args.get('write', '1') not in ('1', 'true', 'yes'):
        return jsonify({'success': True, 'outline_id': outline_id,
                        'started': False})

    response, status = _start_turn(
        session_record, user,
        'Approved — write the post from that outline.',
    )
    payload = response.get_json()
    payload['outline_id'] = outline_id
    payload['started'] = True
    return jsonify(payload), status


@chat_bp.route('/api/chat/confirm', methods=['POST'])
@api_login_required
def confirm_action():
    """Redeem a confirmation token and perform the destructive action.

    Phase two of every destructive action. The token was minted by a tool that
    deleted nothing; this is the request that does the work, and it exists only
    because the user clicked. Consumption is transactional, so a double-click
    deletes once.

    No new turn is started afterwards. The outcome is appended as a ``system``
    message instead: the result is a fact, not an opinion, and spending a model
    call to have the agent narrate a deletion it did not perform would be slower
    and less reliable than stating it.
    """
    user = current_user()
    data = request.get_json(silent=True) or {}
    token = (data.get('token') or '').strip()
    if not token:
        raise ValidationError('A confirmation token is required.')

    result = execute_confirmed_delete(db_service, user.id, user.name or 'User',
                                      token)

    session_id = (data.get('session_id') or '').strip()
    if session_id and result.get('ok'):
        record = db_service.get_chat_session(session_id, user.id)
        if record:
            db_service.append_chat_message(
                session_id, user.id, 'system', result['message'],
                cards=[{'kind': 'deleted', 'data': {
                    'blog_id': result.get('blog_id', ''),
                    'title': result.get('title', ''),
                }}],
            )
            # The deleted post must stop being "that one" -- otherwise the next
            # "shorten it" resolves to an id that no longer exists.
            if record.get('focus_blog_id') == result.get('blog_id'):
                db_service.update_chat_session(session_id, user.id,
                                               focus_blog_id='',
                                               focus_blog_title='')

    return jsonify({'success': bool(result.get('ok')), **result}), (
        200 if result.get('ok') else 400
    )


# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------

def sweep():
    """Drop finished turn logs and spent confirmation tokens.

    Called from the app factory's periodic maintenance alongside the task
    manager's own cleanup. Both are housekeeping: a finished log is inert and an
    expired token is refused, but a table that only grows is a cost that only
    grows.
    """
    dropped = turns.cleanup()
    purged = db_service.purge_expired_confirmations()
    if dropped or purged:
        logger.debug('Chat sweep: %s turns, %s confirmations', dropped, purged)
    return {'turns': dropped, 'confirmations': purged}
