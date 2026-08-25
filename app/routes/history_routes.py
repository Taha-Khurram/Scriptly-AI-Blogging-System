"""Creation history — every conversation with the blog agent, kept.

A generation used to leave no trace of itself. The prompt lived in the
browser's ``sessionStorage``, the agent's plan and the streaming draft lived in
:mod:`app.utils.task_manager` (an in-process dict that evicts a task ten
minutes after it ends), and the only durable record was a blog document that
does not remember what was asked for. Close the tab and the exchange was gone.

This blueprint is the durable side of that: :mod:`app.repositories.generations`
writes one document per finished run, and these routes read them back as a
conversation -- the prompt as the reader's message, the agent's reasoning and
what it produced as the reply.

Its own blueprint rather than four more routes in ``blog_routes`` (already 2000
lines across fifteen unrelated concerns), because nothing here touches blog
documents: it reads a separate collection, and the only thing it knows about a
post is the id it links to.

Everything is scoped to the signed-in user. A transcript holds the prompt
somebody typed, which is more personal than the post it produced -- an admin
has no more claim on a writer's drafts-in-progress thinking than on their
inbox, so unlike the audit log there is no team-wide view of this.
"""
from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for

from app.core.security import api_login_required, current_user
from app.firebase.firestore_service import FirestoreService
from app.repositories.generations import DEFAULT_PAGE_SIZE

from app.core.logging import get_logger

logger = get_logger(__name__)

history_bp = Blueprint('history', __name__)
db_service = FirestoreService()

# How many transcripts the page renders before the reader asks for more. The
# rail is a scrollable index, not a report -- a first paint that waits on a
# hundred documents to show ten rows of them is the wrong trade.
PAGE_SIZE = DEFAULT_PAGE_SIZE


@history_bp.before_request
def require_login():
    """Same gate as the rest of the dashboard.

    A blueprint-wide hook rather than a decorator per route: the whole
    blueprint is behind the session, and a hook cannot be forgotten on the next
    route somebody adds.
    """
    if not session.get('logged_in'):
        return redirect(url_for('auth_bp.login'))


@history_bp.after_request
def add_cache_headers(response):
    if request.headers.get('X-Pjax') and response.status_code == 200:
        response.headers['Cache-Control'] = 'private, max-age=10, stale-while-revalidate=30'
    return response


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

@history_bp.route('/history')
def history_page():
    """The transcript index and the reading pane beside it.

    The first page of the rail is server-rendered rather than fetched: it is
    the whole content of the screen, and shipping an empty shell that then
    asks for its own rows costs a second round trip to show what the first
    response already knew.

    The selected conversation is *not* rendered here. It is fetched on demand
    (see :func:`api_get_generation`) because a transcript carries the agent's
    reasoning and an excerpt of the draft, and inlining twenty of those to show
    one would make the page twenty times its useful size.
    """
    user_id = session.get('user_id')

    page = db_service.get_generation_history(user_id, limit=PAGE_SIZE)

    return render_template(
        'history.html',
        runs=page['items'],
        has_more=page['has_more'],
        next_cursor=page['next_cursor'],
        # `?run=<id>` deep-links one conversation, so a transcript can be
        # linked to and survives a reload on the row it was opened at.
        selected_id=request.args.get('run', ''),
    )


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@history_bp.route('/api/history', methods=['GET'])
@api_login_required
def api_list_history():
    """The next page of the rail.

    Keyset-paged on ``before`` (the ``created_at`` of the oldest row the caller
    holds) rather than on a page number: Firestore charges for every document
    an offset skips, so offset-paging a long history re-reads the whole history
    on every page.
    """
    user = current_user()
    page = db_service.get_generation_history(
        user.id,
        limit=request.args.get('limit', PAGE_SIZE, type=int),
        before=request.args.get('before', ''),
    )
    return jsonify({'success': True, **page})


@history_bp.route('/api/history/<generation_id>', methods=['GET'])
@api_login_required
def api_get_generation(generation_id):
    """One conversation in full: the prompt, the reasoning, and the outcome."""
    user = current_user()
    record = db_service.get_generation(generation_id, user.id)
    if not record:
        # Missing and not-yours answer identically on purpose. A transcript is
        # keyed by a guessable-length id and holds text somebody typed; a 403
        # would confirm that another user's id exists.
        return jsonify({'success': False, 'error': 'That conversation is no longer available.'}), 404

    return jsonify({'success': True, 'run': record})


@history_bp.route('/api/history/<generation_id>', methods=['DELETE'])
@api_login_required
def api_delete_generation(generation_id):
    """Remove one conversation from the history.

    Deletes the record of the exchange, never the blog it produced -- those are
    separate objects, and tidying a transcript is not a request to lose a
    published post. The response says so, because a delete that quietly took
    more than the reader expected is the one they cannot undo.
    """
    user = current_user()
    if not db_service.delete_generation(generation_id, user.id):
        return jsonify({'success': False, 'error': 'That conversation is no longer available.'}), 404

    return jsonify({
        'success': True,
        'message': 'Conversation removed. The blog it produced is untouched.',
    })


@history_bp.route('/api/history', methods=['DELETE'])
@api_login_required
def api_clear_history():
    """Delete the signed-in user's whole history.

    Bounded per call by the repository (a Firestore write batch is 500
    operations, and an unbounded loop here is a request that runs for as long
    as the account is old), so the count comes back and a history longer than
    the bound simply needs the button pressed again.
    """
    user = current_user()
    deleted = db_service.clear_generation_history(user.id)
    return jsonify({
        'success': True,
        'deleted': deleted,
        'message': (
            f'Removed {deleted} conversation{"" if deleted == 1 else "s"}. '
            'Your blogs are untouched.'
        ),
    })
