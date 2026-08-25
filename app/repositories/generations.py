"""Blog generation runs: the transcript of every conversation with the agent.

One slice of what used to be a single 3,300-line ``FirestoreService`` class.
That class was imported by every blueprint, so any data-layer change risked the
whole application, and its size made it effectively untestable -- there was no
way to exercise one domain without loading all of them.

This is a mixin, not a standalone repository object, because the methods call
each other across domain lines (creating a draft updates a category count;
listing published posts backfills slugs). Composing mixins keeps those calls
working with no rewiring, so the split is a pure move: same method set, same
behaviour, reviewable units. ``FirestoreService`` composes every mixin, so all
existing call sites are unchanged.

``self.db`` (the Firestore client) and the collection names come from
``FirestoreService.__init__``.

Why the collection exists
-------------------------

A generation was, until now, entirely ephemeral. The prompt lived in the
browser's ``sessionStorage``, the agent's plan and the streaming draft lived in
:mod:`app.utils.task_manager` -- an in-process dict that evicts a task 600
seconds after it ends -- and the only durable trace was a blog document with no
memory of what was asked for, plus one line in the audit log that says
"generated a blog as DRAFT".

So the question "what did I ask for last Tuesday, and what did it decide to
write?" had no answer anywhere in the product. This collection is that answer:
one document per run, written once when the run reaches a terminal state, and
read back as a conversation.

Shape of a document
-------------------

Records are written once and never updated, so there is no partial-write state
to reason about. Sizes are capped at write time rather than at read time --
``excerpt`` to a few paragraphs and ``thoughts`` to the pipeline's own ceiling
-- because a generation is a *log entry*, not a second copy of the post. The
post itself is one ``blog_id`` away, and duplicating a 7 KB body into a second
collection would double the storage for every draft the app has ever made.
"""
from google.cloud.firestore_v1.base_query import FieldFilter
from firebase_admin import firestore

from app.utils.date_utils import ensure_aware, utcnow

from app.core.logging import get_logger

logger = get_logger(__name__)


# The transcript keeps the opening of the draft, not the draft. Enough to
# recognise the piece and read its first idea; the rest is in the blog document
# the record points at. ~2 short paragraphs.
MAX_EXCERPT_CHARS = 900

# Matches ``task_manager._MAX_THOUGHTS``: the reasoning panel cannot show more
# than the pipeline can produce, so storing more would be storing nothing.
MAX_THOUGHTS = 40

# One reasoning line, trimmed the same way ``task_manager.add_thought`` trims it.
MAX_THOUGHT_CHARS = 400

# A prompt is already bounded at the route by ``MAX_PROMPT_LENGTH``. This is the
# backstop for anything reaching the repository by another path.
MAX_PROMPT_CHARS = 2000

# The rail lists this many runs per request. Deliberately small: the list is a
# scrollable conversation index, and "Load older" is one more round trip rather
# than a first paint that waits on a hundred documents.
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50

# Everything the conversation rail renders: enough to draw a row and decide
# whether to open it, and nothing else. `thoughts` and `excerpt` are the bulk
# of a record and neither is on screen until a run is selected -- projecting
# them away is the difference between a list request that carries twenty
# transcripts and one that carries twenty labels.
GENERATION_LIST_FIELDS = (
    'prompt',
    'title',
    'status',
    'category',
    'blog_id',
    'blog_status',
    'word_count',
    'created_at',
)


def _clip(value, limit):
    """Trim a string to ``limit`` characters, tolerating ``None``."""
    text = (value or '')
    if not isinstance(text, str):
        text = str(text)
    return text[:limit]


class GenerationRepository:
    """Blog generation runs: the transcript of every conversation with the agent."""

    # --- Writes -----------------------------------------------------------

    def record_generation(self, user_id, prompt, **fields):
        """Store one finished run. Returns the new document id, or ``None``.

        Called from the background task at the end of the pipeline, in both the
        success and the failure path -- a run that failed is the one a reader is
        most likely to come looking for, since it is the one that left nothing
        in Drafts to explain itself.

        Never raises. A transcript is a record *about* work that already
        happened; failing to write it must not turn a generation the user can
        see in their drafts into an error on their screen.
        """
        try:
            thoughts = []
            for entry in (fields.get('thoughts') or [])[:MAX_THOUGHTS]:
                if isinstance(entry, dict):
                    text = _clip(entry.get('text'), MAX_THOUGHT_CHARS)
                    kind = entry.get('kind') or 'note'
                else:
                    text = _clip(entry, MAX_THOUGHT_CHARS)
                    kind = 'note'
                if text:
                    thoughts.append({'text': text, 'kind': kind})

            doc = {
                'user_id': user_id,
                'user_name': fields.get('user_name') or '',
                'prompt': _clip(prompt, MAX_PROMPT_CHARS),
                # 'completed' or 'failed' -- the run's own outcome, which is not
                # the blog's status. A completed run can still be sitting in
                # `blog_status: DRAFT`.
                'status': fields.get('status') or 'completed',
                'destination': fields.get('destination') or 'draft',
                'title': _clip(fields.get('title'), 300),
                'category': _clip(fields.get('category'), 120),
                'blog_id': fields.get('blog_id') or '',
                'blog_status': fields.get('blog_status') or '',
                'blog_slug': _clip(fields.get('blog_slug'), 300),
                'word_count': int(fields.get('word_count') or 0),
                'section_count': int(fields.get('section_count') or 0),
                'reading_time': _clip(fields.get('reading_time'), 40),
                'model_used': _clip(fields.get('model_used'), 80),
                'duration_seconds': round(float(fields.get('duration_seconds') or 0), 1),
                'excerpt': _clip(fields.get('excerpt'), MAX_EXCERPT_CHARS),
                'thoughts': thoughts,
                'error': _clip(fields.get('error'), 500),
                'error_code': _clip(fields.get('error_code'), 80),
                'created_at': utcnow(),
                'server_created_at': firestore.SERVER_TIMESTAMP,
            }

            doc_ref = self.db.collection(self.generation_collection).document()
            doc_ref.set(doc)
            return doc_ref.id
        except Exception:
            logger.exception('Error recording generation history')
            return None

    # --- Reads ------------------------------------------------------------

    def get_generation_history(self, user_id, limit=DEFAULT_PAGE_SIZE, before=None):
        """One page of a user's runs, newest first.

        Paged by keyset rather than by offset: ``before`` is the ``created_at``
        of the oldest row the caller already has, and the next page starts
        strictly after it. Firestore charges for every document an ``offset``
        skips, so offset paging through a long history costs the whole history
        again on each page -- a keyset costs one page.

        ``before`` may be an ISO-8601 string (what the browser sends back) or a
        datetime. An unparseable value is treated as absent, which returns the
        first page: a bad cursor should show the reader the top of their
        history, not an error.
        """
        try:
            limit = max(1, min(int(limit or DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE))

            query = (self.db.collection(self.generation_collection)
                     .where(filter=FieldFilter('user_id', '==', user_id))
                     .order_by('created_at', direction=firestore.Query.DESCENDING)
                     .select(list(GENERATION_LIST_FIELDS)))

            cursor = _as_datetime(before)
            if cursor is not None:
                query = query.start_after({'created_at': cursor})

            # One extra row, purely to answer "is there another page?" without a
            # second query or a count. It is dropped before returning.
            docs = list(query.limit(limit + 1).stream())
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
                # The cursor for the next page, so the caller never has to know
                # which field the keyset is built on.
                'next_cursor': items[-1]['created_at'] if (items and has_more) else '',
            }
        except Exception:
            logger.exception('Error fetching generation history')
            return {'items': [], 'has_more': False, 'next_cursor': ''}

    def get_generation(self, generation_id, user_id):
        """One run in full, or ``None`` if it does not exist or is not theirs.

        The ownership check and the missing-document case deliberately share a
        return value: a transcript holds the prompt a user typed, and answering
        403 for someone else's id confirms that the id exists.
        """
        try:
            doc = (self.db.collection(self.generation_collection)
                   .document(generation_id).get())
            if not doc.exists:
                return None

            data = doc.to_dict() or {}
            if data.get('user_id') != user_id:
                return None

            data['id'] = doc.id
            _serialize_dates(data)
            return data
        except Exception:
            logger.exception('Error fetching generation record')
            return None

    def count_generations(self, user_id, cap=200):
        """How many runs a user has, counted up to ``cap``.

        Projected to a single field and capped, because this only exists to
        label the rail ("12 conversations") -- a number nobody scrolls past a
        couple of hundred of, and not worth an unbounded scan to make exact.
        """
        try:
            docs = (self.db.collection(self.generation_collection)
                    .where(filter=FieldFilter('user_id', '==', user_id))
                    .select(['user_id'])
                    .limit(max(1, int(cap)))
                    .stream())
            return sum(1 for _ in docs)
        except Exception:
            logger.exception('Error counting generation history')
            return 0

    # --- Deletes ----------------------------------------------------------

    def delete_generation(self, generation_id, user_id):
        """Remove one run from the history. Returns whether anything was removed.

        Deletes the *record of the conversation*, never the blog it produced --
        those are separate objects with separate lifetimes, and a reader tidying
        their history is not asking to lose a published post.
        """
        try:
            doc_ref = (self.db.collection(self.generation_collection)
                       .document(generation_id))
            doc = doc_ref.get()
            if not doc.exists:
                return False
            if (doc.to_dict() or {}).get('user_id') != user_id:
                return False
            doc_ref.delete()
            return True
        except Exception:
            logger.exception('Error deleting generation record')
            return False

    def clear_generation_history(self, user_id, max_deletes=500):
        """Delete a user's whole history. Returns how many rows went.

        Batched, and bounded by ``max_deletes``: a Firestore write batch takes
        500 operations, and an unbounded loop here would be a request that runs
        for as long as the account is old. The route reports the count, so a
        history longer than the bound simply needs the button pressed twice.
        """
        try:
            docs = list(self.db.collection(self.generation_collection)
                        .where(filter=FieldFilter('user_id', '==', user_id))
                        .select(['user_id'])
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
            logger.exception('Error clearing generation history')
            return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _as_datetime(value):
    """Coerce a cursor to an aware datetime, or ``None`` if it is not one.

    ``ensure_aware`` already parses ISO-8601 (including the trailing ``Z`` the
    browser's ``toISOString()`` produces) and returns ``None`` for anything it
    cannot read, which is exactly the "ignore a bad cursor" behaviour the caller
    wants -- so this is a thin guard around it rather than a second parser.
    """
    if not value:
        return None
    return ensure_aware(value if hasattr(value, 'isoformat') else str(value).strip())


def _serialize_dates(data):
    """Rewrite datetime fields in place as ISO-8601 strings.

    Done in the repository rather than in the route because both the list and
    the detail read feed ``jsonify``, and a Firestore ``DatetimeWithNanoseconds``
    is not JSON-serialisable -- a fact worth knowing in exactly one place.
    """
    for key in ('created_at', 'server_created_at'):
        value = data.get(key)
        if value is None:
            data[key] = ''
        elif hasattr(value, 'isoformat'):
            data[key] = value.isoformat()
        else:
            data[key] = str(value)
    return data
