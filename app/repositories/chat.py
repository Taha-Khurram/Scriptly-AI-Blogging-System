"""Agent conversations: sessions, messages, outline approvals, confirmations.

The state the conversational agent needs and the rest of the application had no
place for. ``generations`` (see :mod:`app.repositories.generations`) records one
finished single-shot run as a transcript written once and never updated; a
conversation is the opposite shape -- long-lived, appended to on both sides, and
carrying decisions ("this outline is approved", "this delete is confirmed") that
later requests have to be able to *check*.

Four collections, and why they are four
---------------------------------------

``chat_sessions``
    One document per conversation. Holds the focus pointers -- ``focus_blog_id``
    and ``focus_outline_id`` -- which are what make "make the intro punchier"
    resolve without the user naming an id. Kept on the session rather than
    recomputed from the message log because they are a *decision* about what
    "that one" means, and re-deriving them from text on every turn would let the
    answer drift between turns.

``chat_messages``
    One document per message. Not an array on the session: Firestore caps a
    document at 1 MiB and rewrites the whole document on every array append, so
    a long conversation would get quadratically more expensive to extend and
    would eventually stop being extendable at all. ``seq`` is assigned inside a
    transaction against the session, so two messages written in the same
    millisecond still have a total order -- ``created_at`` alone does not
    guarantee one, and a chat log that occasionally renders out of order is
    indistinguishable from a broken agent.

``blog_outlines``
    The human-in-the-loop gate, made durable. The agent must never go from a
    topic to a finished post without approval, and a rule that lives only in a
    prompt is a request, not a guarantee: one confused turn and the model
    writes anyway. So approval is a stored field on a stored outline, the
    ``create_blog`` tool refuses an outline whose ``status`` is not
    ``approved``, and only a user-initiated request can set it (see
    :meth:`approve_outline` and the route that calls it). The prompt asks; this
    collection enforces.

``agent_confirmations``
    Two-phase destructive actions. A ``delete_blog`` call does not delete
    anything: it records what *would* be deleted and returns a token. The delete
    happens on a second, separate request carrying that token -- one the user
    made by clicking a confirm button. Tokens are single-use and time-boxed,
    consumed inside a transaction, so a double-click or a replayed request
    cannot delete twice, and a token that leaks after expiry is inert.

Why the confirmations live in Firestore rather than in memory: the task pool is
per-process (see :mod:`app.utils.task_manager`), so an in-memory token issued by
one worker is invisible to the worker that handles the confirm click. A user
would click Delete and be told the confirmation had expired, at random, in
proportion to worker count.

``self.db`` and the collection names come from ``FirestoreService.__init__``.
"""
from datetime import datetime, timedelta, timezone

from google.api_core.exceptions import FailedPrecondition
from google.cloud.firestore_v1.base_query import FieldFilter
from firebase_admin import firestore

from app.utils.date_utils import ensure_aware, utcnow

from app.core.logging import get_logger

logger = get_logger(__name__)


# --- Bounds ---------------------------------------------------------------
#
# Everything a user or a model can put into a document is clipped at write
# time. A conversation is append-only and unbounded in length, so the only
# place a size limit can be enforced cheaply is on the way in.

MAX_MESSAGE_CHARS = 8_000       # one message body; a blog draft is linked, not inlined
MAX_TITLE_CHARS = 120           # session title, derived from the first message
MAX_PREVIEW_CHARS = 160         # what the sidebar row shows
MAX_TOOL_CALLS_PER_MESSAGE = 24 # matches the loop's own per-turn ceiling
MAX_TOOL_ARG_CHARS = 600        # per tool call, in the audit record
MAX_CARDS_PER_MESSAGE = 8

# Outline shape. Deliberately small: an outline is a plan to approve, not a
# draft. If it needs more than this it is a blog post and should be written.
MAX_OUTLINE_SECTIONS = 12
MAX_OUTLINE_POINTS_PER_SECTION = 8
MAX_OUTLINE_SOURCES = 12
MAX_OUTLINE_TEXT_CHARS = 400

# How much history is read back for the model. The window is a token budget:
# every message here is re-sent on every turn of the loop, so it is the single
# biggest driver of the cost of a long conversation.
DEFAULT_HISTORY_LIMIT = 30
MAX_HISTORY_LIMIT = 100

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 50
FALLBACK_SCAN_LIMIT = 200

# A confirmation is a decision the user is about to make on screen. Long enough
# to read the dialog and think; short enough that a token found in a log later
# is worthless.
CONFIRMATION_TTL_SECONDS = 600

# Everything the conversation sidebar renders. Projected away: the focus
# pointers and counters that only matter once a session is opened.
SESSION_LIST_FIELDS = (
    'title',
    'preview',
    'status',
    'message_count',
    'blog_count',
    'created_at',
    'updated_at',
)


def _clip(value, limit):
    """Trim to ``limit`` characters, tolerating ``None`` and non-strings."""
    text = value if isinstance(value, str) else ('' if value is None else str(value))
    return text[:limit]


def _clip_list(values, limit, item_limit):
    out = []
    for value in (values or [])[:limit]:
        text = _clip(value, item_limit).strip()
        if text:
            out.append(text)
    return out


class ChatRepository:
    """Agent conversations: sessions, messages, outline approvals, confirmations."""

    # =====================================================================
    # Sessions
    # =====================================================================

    def create_chat_session(self, user_id, *, title='', preview=''):
        """Open a conversation. Returns the new session id, or ``None``.

        ``message_count`` starts at zero and is the sequence counter every
        message in the session draws from -- see :meth:`append_chat_message`.
        """
        try:
            doc = {
                'user_id': user_id,
                'title': _clip(title, MAX_TITLE_CHARS) or 'New conversation',
                'preview': _clip(preview, MAX_PREVIEW_CHARS),
                'status': 'active',
                'message_count': 0,
                'blog_count': 0,
                # What "that one" refers to. Written by the tool layer whenever
                # a tool touches a specific blog or outline, read back into the
                # system prompt on the next turn.
                'focus_blog_id': '',
                'focus_blog_title': '',
                'focus_outline_id': '',
                'created_at': utcnow(),
                'updated_at': utcnow(),
                'server_created_at': firestore.SERVER_TIMESTAMP,
            }
            ref = self.db.collection(self.chat_session_collection).document()
            ref.set(doc)
            return ref.id
        except Exception:
            logger.exception('Error creating chat session')
            return None

    def get_chat_session(self, session_id, user_id):
        """One session, or ``None`` when it is missing or not theirs.

        Missing and not-yours share a return value for the same reason the
        generation transcripts do: a conversation holds text somebody typed,
        and a distinguishable 403 confirms that another user's id exists.
        """
        if not session_id:
            return None
        try:
            doc = (self.db.collection(self.chat_session_collection)
                   .document(session_id).get())
            if not doc.exists:
                return None
            data = doc.to_dict() or {}
            if data.get('user_id') != user_id:
                return None
            data['id'] = doc.id
            _serialize_dates(data)
            return data
        except Exception:
            logger.exception('Error fetching chat session')
            return None

    def list_chat_sessions(self, user_id, limit=DEFAULT_PAGE_SIZE, before=None):
        """A page of the user's conversations, most recently used first.

        Ordered by ``updated_at``, not ``created_at``: the sidebar is a list of
        things to go back to, and the conversation someone was in five minutes
        ago belongs at the top however old it is.

        Keyset-paged, and with the same unindexed fallback as the generation
        history -- for the same reason. Until the composite index is deployed
        the ordered query raises ``FailedPrecondition``, and answering "no
        conversations yet" to a user who has fifty is the worst available
        outcome: wrong, silent, and indistinguishable from a broken feature.
        """
        limit = max(1, min(int(limit or DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE))
        cursor = _as_datetime(before)

        try:
            query = (self.db.collection(self.chat_session_collection)
                     .where(filter=FieldFilter('user_id', '==', user_id))
                     .order_by('updated_at', direction=firestore.Query.DESCENDING)
                     .select(list(SESSION_LIST_FIELDS)))
            if cursor is not None:
                query = query.start_after({'updated_at': cursor})

            docs = list(query.limit(limit + 1).stream())
            return _page(docs, limit, cursor_field='updated_at')

        except FailedPrecondition as exc:
            logger.error(
                'Chat sessions are running unindexed -- deploy the composite '
                'index (user_id ASC, updated_at DESC) on `%s` with '
                '`firebase deploy --only firestore:indexes`. Firestore said: %s',
                self.chat_session_collection, exc,
            )
            return self._session_scan(user_id, limit, cursor)

        except Exception:
            logger.exception('Error listing chat sessions')
            return {'items': [], 'has_more': False, 'next_cursor': ''}

    def _session_scan(self, user_id, limit, cursor):
        """The same page without the composite index. Bounded, and a stopgap."""
        try:
            docs = list(self.db.collection(self.chat_session_collection)
                        .where(filter=FieldFilter('user_id', '==', user_id))
                        .select(list(SESSION_LIST_FIELDS))
                        .limit(FALLBACK_SCAN_LIMIT)
                        .stream())

            rows = [(_sort_key(doc, 'updated_at'), doc) for doc in docs]
            rows.sort(key=lambda row: row[0], reverse=True)
            if cursor is not None:
                rows = [row for row in rows if row[0] < cursor]

            return _page([doc for _, doc in rows[:limit + 1]], limit,
                         cursor_field='updated_at')
        except Exception:
            logger.exception('Error listing chat sessions (unindexed)')
            return {'items': [], 'has_more': False, 'next_cursor': ''}

    def update_chat_session(self, session_id, user_id, **fields):
        """Patch the mutable parts of a session. Returns whether it applied.

        Only an allowlist of fields is writable, so a caller cannot reassign
        ``user_id`` and hand someone else's conversation to themselves.
        """
        allowed = {
            'title': lambda v: _clip(v, MAX_TITLE_CHARS),
            'preview': lambda v: _clip(v, MAX_PREVIEW_CHARS),
            'status': lambda v: _clip(v, 24),
            'focus_blog_id': lambda v: _clip(v, 80),
            'focus_blog_title': lambda v: _clip(v, 300),
            'focus_outline_id': lambda v: _clip(v, 80),
        }
        update = {key: allowed[key](value)
                  for key, value in fields.items() if key in allowed}
        if not update:
            return False

        try:
            ref = self.db.collection(self.chat_session_collection).document(session_id)
            doc = ref.get()
            if not doc.exists or (doc.to_dict() or {}).get('user_id') != user_id:
                return False
            update['updated_at'] = utcnow()
            ref.update(update)
            return True
        except Exception:
            logger.exception('Error updating chat session')
            return False

    def delete_chat_session(self, session_id, user_id, max_messages=500):
        """Delete a conversation and its messages. Returns whether it applied.

        The messages go too -- they are meaningless without their session and
        would otherwise be unreachable rows accruing forever. The blogs the
        conversation produced are untouched: they are separate objects with
        their own lifetimes, and clearing a chat is not a request to lose
        published work. The route says so in its response, because a delete
        that quietly took more than expected is the one that cannot be undone.
        """
        try:
            ref = self.db.collection(self.chat_session_collection).document(session_id)
            doc = ref.get()
            if not doc.exists or (doc.to_dict() or {}).get('user_id') != user_id:
                return False

            messages = list(self.db.collection(self.chat_message_collection)
                            .where(filter=FieldFilter('session_id', '==', session_id))
                            .select(['session_id'])
                            .limit(max(1, int(max_messages)))
                            .stream())

            for start in range(0, len(messages), 400):
                batch = self.db.batch()
                for message in messages[start:start + 400]:
                    batch.delete(message.reference)
                batch.commit()

            ref.delete()
            return True
        except Exception:
            logger.exception('Error deleting chat session')
            return False

    # =====================================================================
    # Messages
    # =====================================================================

    def append_chat_message(self, session_id, user_id, role, text, **fields):
        """Append one message and advance the session in a single transaction.

        The transaction does three things that have to happen together or not
        at all: it reads the session's ``message_count`` to assign this
        message's ``seq``, writes the message, and updates the session's
        counters, preview and focus pointers.

        Doing it atomically is what guarantees a total order. Two turns landing
        in the same millisecond -- a user message and the agent's reply, or two
        browser tabs on one conversation -- would otherwise get identical
        ``created_at`` values and render in whichever order Firestore felt like
        returning, which reads exactly like an agent answering the wrong
        question.

        Returns ``{'id', 'seq'}``, or ``None`` on failure. Never raises: losing
        a message from the log must not fail the turn that produced it, since
        the turn's real output (a draft, a deletion) has already happened.
        """
        role = role if role in ('user', 'agent', 'system') else 'agent'

        message = {
            'session_id': session_id,
            'user_id': user_id,
            'role': role,
            'text': _clip(text, MAX_MESSAGE_CHARS),
            # The audit trail, kept with the message that produced it rather
            # than in a separate log: "what did the agent do when it said
            # that?" is a question about one turn, and joining two collections
            # to answer it would be the only reason to ever join them.
            'tool_calls': _clean_tool_calls(fields.get('tool_calls')),
            # Structured attachments the UI renders as cards -- an outline
            # proposal, a draft preview, a confirmation request. Stored so a
            # reloaded conversation shows the same cards it showed live.
            'cards': _clean_cards(fields.get('cards')),
            'status': _clip(fields.get('status'), 24) or 'complete',
            'error': _clip(fields.get('error'), 500),
            'turn_id': _clip(fields.get('turn_id'), 64),
            'created_at': utcnow(),
        }

        session_ref = self.db.collection(self.chat_session_collection).document(session_id)
        message_ref = self.db.collection(self.chat_message_collection).document()

        @firestore.transactional
        def write(transaction):
            snapshot = session_ref.get(transaction=transaction)
            if not snapshot.exists:
                return None
            data = snapshot.to_dict() or {}
            if data.get('user_id') != user_id:
                return None

            seq = int(data.get('message_count') or 0)
            message['seq'] = seq
            transaction.set(message_ref, message)

            session_update = {
                'message_count': seq + 1,
                'updated_at': utcnow(),
            }
            if message['text']:
                session_update['preview'] = _clip(message['text'], MAX_PREVIEW_CHARS)
            # The first user message names the conversation. Titles are only
            # ever set from a default, so a user who renamed a session keeps
            # their name.
            if (role == 'user' and seq == 0
                    and (data.get('title') or 'New conversation') == 'New conversation'):
                session_update['title'] = _clip(text, MAX_TITLE_CHARS)

            for key in ('focus_blog_id', 'focus_blog_title', 'focus_outline_id'):
                if key in fields and fields[key] is not None:
                    session_update[key] = _clip(fields[key], 300)
            if fields.get('blogs_created'):
                session_update['blog_count'] = (
                    int(data.get('blog_count') or 0) + int(fields['blogs_created'])
                )

            transaction.update(session_ref, session_update)
            return seq

        try:
            seq = write(self.db.transaction())
            if seq is None:
                return None
            return {'id': message_ref.id, 'seq': seq}
        except Exception:
            logger.exception('Error appending chat message')
            return None

    def get_chat_messages(self, session_id, user_id, limit=DEFAULT_HISTORY_LIMIT,
                          after_seq=None):
        """Messages for a session in order, oldest first.

        Two callers, one method. The page render wants the whole visible
        conversation; the agent loop wants the last N as model context. Both
        want the same ordering, so ``limit`` bounds the *tail* -- the newest N,
        returned oldest-first -- rather than the head. A conversation truncated
        from the front loses the question the last answer was about.

        ``after_seq`` is for the incremental read a reattaching browser does:
        "everything after the message I already have".
        """
        limit = max(1, min(int(limit or DEFAULT_HISTORY_LIMIT), MAX_HISTORY_LIMIT))

        # The ownership check is on the session, once, rather than per message:
        # the messages carry `user_id` too, but a caller holding a session id
        # they do not own must not learn how many messages it has either.
        session = self.get_chat_session(session_id, user_id)
        if session is None:
            return []

        try:
            query = (self.db.collection(self.chat_message_collection)
                     .where(filter=FieldFilter('session_id', '==', session_id)))

            if after_seq is not None:
                # Ascending from a known point: this is the resume path and the
                # caller wants everything since, in order.
                docs = list(query
                            .where(filter=FieldFilter('seq', '>', int(after_seq)))
                            .order_by('seq')
                            .limit(limit)
                            .stream())
            else:
                docs = list(query
                            .order_by('seq', direction=firestore.Query.DESCENDING)
                            .limit(limit)
                            .stream())
                docs.reverse()

            return [_message_dict(doc) for doc in docs]

        except FailedPrecondition as exc:
            logger.error(
                'Chat messages are running unindexed -- deploy the composite '
                'index (session_id ASC, seq ASC) on `%s` with `firebase '
                'deploy --only firestore:indexes`. Firestore said: %s',
                self.chat_message_collection, exc,
            )
            return self._message_scan(session_id, limit, after_seq)
        except Exception:
            logger.exception('Error fetching chat messages')
            return []

    def _message_scan(self, session_id, limit, after_seq):
        """Unindexed read of one session's messages. Bounded, and a stopgap."""
        try:
            docs = list(self.db.collection(self.chat_message_collection)
                        .where(filter=FieldFilter('session_id', '==', session_id))
                        .limit(FALLBACK_SCAN_LIMIT)
                        .stream())
            rows = [_message_dict(doc) for doc in docs]
            rows.sort(key=lambda row: row.get('seq') or 0)
            if after_seq is not None:
                rows = [row for row in rows if (row.get('seq') or 0) > int(after_seq)]
                return rows[:limit]
            return rows[-limit:]
        except Exception:
            logger.exception('Error fetching chat messages (unindexed)')
            return []

    # =====================================================================
    # Outlines -- the human-in-the-loop gate
    # =====================================================================

    def create_outline_record(self, user_id, session_id, outline, **fields):
        """Store a proposed outline as ``pending_approval``. Returns its id.

        Written by the ``create_outline`` tool. It is created un-approved and
        there is no code path that creates it any other way, which is what
        makes "the agent cannot skip the outline step" a property of the data
        rather than a hope about the prompt.
        """
        try:
            doc = {
                'user_id': user_id,
                'session_id': session_id or '',
                'topic': _clip(fields.get('topic'), MAX_OUTLINE_TEXT_CHARS),
                'title': _clip(outline.get('title'), MAX_OUTLINE_TEXT_CHARS),
                'angle': _clip(outline.get('angle'), MAX_OUTLINE_TEXT_CHARS),
                'audience': _clip(outline.get('audience'), MAX_OUTLINE_TEXT_CHARS),
                'sections': _clean_sections(outline.get('sections')),
                'sources': _clean_sources(outline.get('sources')),
                'tone': _clip(fields.get('tone'), 60),
                'length': _clip(fields.get('length'), 60),
                'keywords': _clip_list(fields.get('keywords'), 12, 80),
                'researched': bool(fields.get('researched')),
                # 'pending_approval' -> 'approved' | 'superseded'. Only
                # `approve_outline` writes 'approved', and only a user-initiated
                # request calls it.
                'status': 'pending_approval',
                'approved_at': None,
                'approved_via': '',
                'revision_of': _clip(fields.get('revision_of'), 80),
                'revision': int(fields.get('revision') or 1),
                'blog_id': '',
                'created_at': utcnow(),
                'updated_at': utcnow(),
            }
            ref = self.db.collection(self.outline_collection).document()
            ref.set(doc)
            return ref.id
        except Exception:
            logger.exception('Error creating outline record')
            return None

    def get_outline(self, outline_id, user_id):
        """One outline, or ``None`` when missing or not theirs."""
        if not outline_id:
            return None
        try:
            doc = self.db.collection(self.outline_collection).document(outline_id).get()
            if not doc.exists:
                return None
            data = doc.to_dict() or {}
            if data.get('user_id') != user_id:
                return None
            data['id'] = doc.id
            _serialize_dates(data, ('created_at', 'updated_at', 'approved_at'))
            return data
        except Exception:
            logger.exception('Error fetching outline')
            return None

    def approve_outline(self, outline_id, user_id, *, via='ui'):
        """Mark an outline approved. Returns the approved outline, or ``None``.

        The single writer of ``status='approved'`` in the application, called
        only from a route that a signed-in user's own request reached. The model
        has no tool that reaches this: an agent that can approve on the user's
        behalf has no approval step at all, only a slower one.

        Idempotent -- approving twice is what a double-click is, and it must not
        be an error. ``via`` records whether the approval came from the button
        or from the user typing "yes, go ahead", which is the kind of thing that
        matters exactly once, when someone asks how a post got written.
        """
        try:
            ref = self.db.collection(self.outline_collection).document(outline_id)
            doc = ref.get()
            if not doc.exists:
                return None
            data = doc.to_dict() or {}
            if data.get('user_id') != user_id:
                return None
            if data.get('status') == 'superseded':
                # A revised outline is not the plan any more. Approving it would
                # write the post the user asked to change.
                return None

            if data.get('status') != 'approved':
                ref.update({
                    'status': 'approved',
                    'approved_at': utcnow(),
                    'approved_via': _clip(via, 24),
                    'updated_at': utcnow(),
                })
                data = (ref.get().to_dict() or {})

            data['id'] = outline_id
            _serialize_dates(data, ('created_at', 'updated_at', 'approved_at'))
            return data
        except Exception:
            logger.exception('Error approving outline')
            return None

    def supersede_outline(self, outline_id, user_id):
        """Retire an outline because a revision replaced it.

        Retiring the old one is not bookkeeping. Without it, a user who asked
        for changes and then said "yes" could have that approval land on the
        version they rejected -- two live outlines for one topic is one too
        many.
        """
        try:
            ref = self.db.collection(self.outline_collection).document(outline_id)
            doc = ref.get()
            if not doc.exists or (doc.to_dict() or {}).get('user_id') != user_id:
                return False
            ref.update({'status': 'superseded', 'updated_at': utcnow()})
            return True
        except Exception:
            logger.exception('Error superseding outline')
            return False

    def mark_outline_written(self, outline_id, user_id, blog_id):
        """Record which blog an approved outline produced."""
        try:
            ref = self.db.collection(self.outline_collection).document(outline_id)
            doc = ref.get()
            if not doc.exists or (doc.to_dict() or {}).get('user_id') != user_id:
                return False
            ref.update({'blog_id': _clip(blog_id, 80), 'updated_at': utcnow()})
            return True
        except Exception:
            logger.exception('Error marking outline written')
            return False

    # =====================================================================
    # Confirmations -- two-phase destructive actions
    # =====================================================================

    def create_confirmation(self, user_id, *, session_id, action, target_id,
                            summary, payload=None, ttl=CONFIRMATION_TTL_SECONDS):
        """Record a pending destructive action and return its token (the doc id).

        Returns ``None`` on failure, which the caller must treat as "do not
        proceed": a delete whose confirmation could not be recorded has to be
        refused, not performed unconfirmed.
        """
        try:
            now = utcnow()
            doc = {
                'user_id': user_id,
                'session_id': _clip(session_id, 80),
                'action': _clip(action, 40),
                'target_id': _clip(target_id, 80),
                'summary': _clip(summary, 400),
                'payload': payload if isinstance(payload, dict) else {},
                'created_at': now,
                'expires_at': now + timedelta(seconds=max(30, int(ttl))),
                'consumed_at': None,
            }
            ref = self.db.collection(self.confirmation_collection).document()
            ref.set(doc)
            return ref.id
        except Exception:
            logger.exception('Error creating confirmation')
            return None

    def consume_confirmation(self, token, user_id, *, action=None):
        """Redeem a confirmation exactly once. Returns the record, or ``None``.

        The transaction is the whole point. Two clicks on a confirm button, or
        a retried request, would otherwise both read an unconsumed token and
        both proceed -- and "delete ran twice" is only harmless by luck. The
        read and the stamp happen together, so exactly one caller sees the
        token unconsumed.

        ``action`` is checked when supplied so a token minted for one operation
        cannot authorise another.
        """
        if not token:
            return None

        ref = self.db.collection(self.confirmation_collection).document(str(token))

        @firestore.transactional
        def redeem(transaction):
            snapshot = ref.get(transaction=transaction)
            if not snapshot.exists:
                return None
            data = snapshot.to_dict() or {}
            if data.get('user_id') != user_id:
                return None
            if data.get('consumed_at'):
                return None
            if action and data.get('action') != action:
                return None

            expires = ensure_aware(data.get('expires_at'))
            if expires is None or expires < utcnow():
                return None

            transaction.update(ref, {'consumed_at': utcnow()})
            data['id'] = ref.id
            return data

        try:
            record = redeem(self.db.transaction())
            if record is None:
                return None
            _serialize_dates(record, ('created_at', 'expires_at', 'consumed_at'))
            return record
        except Exception:
            logger.exception('Error consuming confirmation')
            return None

    def purge_expired_confirmations(self, max_deletes=200):
        """Drop spent and expired tokens. Called by the maintenance sweep.

        Consumed tokens are inert and expired ones are refused, so this is
        housekeeping rather than a control -- but a collection that only ever
        grows is a cost that only ever grows.
        """
        try:
            docs = list(self.db.collection(self.confirmation_collection)
                        .where(filter=FieldFilter('expires_at', '<', utcnow()))
                        .select(['expires_at'])
                        .limit(max(1, int(max_deletes)))
                        .stream())
            if not docs:
                return 0
            deleted = 0
            for start in range(0, len(docs), 400):
                batch = self.db.batch()
                for doc in docs[start:start + 400]:
                    batch.delete(doc.reference)
                    deleted += 1
                batch.commit()
            return deleted
        except Exception:
            logger.exception('Error purging confirmations')
            return 0


# ---------------------------------------------------------------------------
# Cleaning
# ---------------------------------------------------------------------------

def _clean_tool_calls(entries):
    """Normalise the per-message audit trail of tool calls.

    Arguments are stored as their JSON repr clipped to a few hundred
    characters, not as a nested map: they are read by a human debugging a turn,
    they are model-authored so their shape is not guaranteed, and Firestore
    would otherwise index every key a model ever invented.
    """
    import json

    out = []
    for entry in (entries or [])[:MAX_TOOL_CALLS_PER_MESSAGE]:
        if not isinstance(entry, dict):
            continue
        try:
            args = json.dumps(entry.get('args') or {}, default=str)
        except (TypeError, ValueError):
            args = str(entry.get('args'))
        out.append({
            'name': _clip(entry.get('name'), 60),
            'args': _clip(args, MAX_TOOL_ARG_CHARS),
            'ok': bool(entry.get('ok')),
            'summary': _clip(entry.get('summary'), 300),
            'duration_ms': round(float(entry.get('duration_ms') or 0), 1),
        })
    return out


def _clean_cards(cards):
    """Normalise the structured attachments a message carries.

    Cards are rendered as UI, so what is kept is what the renderer reads: a
    kind, a small payload, and nothing recursive. A model-authored card is not
    a thing -- these are built by the tool layer -- so the shape is known and
    the clipping is a backstop rather than a parser.
    """
    out = []
    for card in (cards or [])[:MAX_CARDS_PER_MESSAGE]:
        if not isinstance(card, dict) or not card.get('kind'):
            continue
        payload = card.get('data')
        out.append({
            'kind': _clip(card.get('kind'), 40),
            'data': payload if isinstance(payload, dict) else {},
        })
    return out


def _clean_sections(sections):
    """Normalise an outline's sections, which are model-authored.

    Accepts either a list of strings (a model that ignored the schema) or a
    list of ``{heading, points}`` maps, and always returns the latter. The
    lenient read is deliberate: an outline that arrives slightly off-schema
    should still reach the user for approval rather than becoming an error they
    cannot act on.
    """
    out = []
    for section in (sections or [])[:MAX_OUTLINE_SECTIONS]:
        if isinstance(section, str):
            heading, points = section, []
        elif isinstance(section, dict):
            heading = section.get('heading') or section.get('title') or ''
            points = section.get('points') or section.get('key_points') or []
            if isinstance(points, str):
                points = [points]
        else:
            continue

        heading = _clip(heading, MAX_OUTLINE_TEXT_CHARS).strip()
        if not heading:
            continue
        out.append({
            'heading': heading,
            'points': _clip_list(points, MAX_OUTLINE_POINTS_PER_SECTION,
                                 MAX_OUTLINE_TEXT_CHARS),
        })
    return out


def _clean_sources(sources):
    """Normalise an outline's cited sources to ``{title, url}`` pairs."""
    out = []
    for source in (sources or [])[:MAX_OUTLINE_SOURCES]:
        if isinstance(source, str):
            title, url = source, ''
        elif isinstance(source, dict):
            title = source.get('title') or source.get('name') or ''
            url = source.get('url') or source.get('link') or ''
        else:
            continue
        title = _clip(title, MAX_OUTLINE_TEXT_CHARS).strip()
        url = _clip(url, 500).strip()
        if not (title or url):
            continue
        out.append({'title': title or url, 'url': url})
    return out


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def _message_dict(doc):
    data = doc.to_dict() or {}
    data['id'] = doc.id
    data['seq'] = int(data.get('seq') or 0)
    _serialize_dates(data, ('created_at',))
    return data


def _sort_key(doc, field):
    """``field`` as something sortable, for the unindexed paths.

    A row missing the field, or carrying something Firestore handed back as a
    non-datetime, sorts to the bottom rather than raising: one malformed
    document must not take out a whole listing.
    """
    value = (doc.to_dict() or {}).get(field)
    aware = ensure_aware(value) if value is not None else None
    return aware or datetime.min.replace(tzinfo=timezone.utc)


def _page(docs, limit, cursor_field='created_at'):
    """``limit + 1`` documents into one page plus its keyset cursor.

    Shared by the indexed and unindexed reads so the two cannot disagree about
    what ``has_more`` means -- the sort of drift that shows up as a page which
    repeats itself.
    """
    has_more = len(docs) > limit

    items = []
    for doc in docs[:limit]:
        data = doc.to_dict() or {}
        data['id'] = doc.id
        _serialize_dates(data)
        items.append(data)

    return {
        'items': items,
        'has_more': has_more,
        'next_cursor': items[-1].get(cursor_field, '') if (items and has_more) else '',
    }


def _as_datetime(value):
    """Coerce a keyset cursor to an aware datetime, or ``None``.

    ``ensure_aware`` already parses ISO-8601 including the trailing ``Z`` that
    ``toISOString()`` produces, and returns ``None`` for anything it cannot
    read -- which is exactly the "ignore a bad cursor and serve page one"
    behaviour wanted here.
    """
    if not value:
        return None
    return ensure_aware(value if hasattr(value, 'isoformat') else str(value).strip())


def _serialize_dates(data, keys=('created_at', 'updated_at', 'server_created_at')):
    """Rewrite datetime fields in place as ISO-8601 strings.

    In the repository rather than the route because every read here feeds
    ``jsonify`` and a Firestore ``DatetimeWithNanoseconds`` is not
    JSON-serialisable -- a fact worth knowing in exactly one place.
    """
    for key in keys:
        if key not in data:
            continue
        value = data.get(key)
        if value is None:
            data[key] = ''
        elif hasattr(value, 'isoformat'):
            data[key] = value.isoformat()
        else:
            data[key] = str(value)
    return data
