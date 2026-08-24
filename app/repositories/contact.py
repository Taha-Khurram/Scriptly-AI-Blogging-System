"""Contact-form submissions from public site visitors.

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
"""
from copy import deepcopy

from firebase_admin import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from app.core.logging import get_logger
from app.repositories._helpers import owner_listing
from app.utils.cache import cache

logger = get_logger(__name__)

CONTACT_CACHE_PREFIX = 'contact_by_owner'


def _invalidate_contact():
    """Drop every cached submissions listing after a write.

    Prefix-wide for the same reason as the comment listings: mark-read and
    delete are given a submission id, not an owner, and re-reading the document
    to learn the owner would add a round trip to every click in the inbox.
    """
    cache.clear_prefix(CONTACT_CACHE_PREFIX)


def _created_at_key(submission):
    """Sort key for a submission's creation time, tolerant of a missing value."""
    value = submission.get('created_at')
    if value is None:
        return 0.0
    if hasattr(value, 'timestamp'):
        try:
            return value.timestamp()
        except (ValueError, OSError):
            return 0.0
    return 0.0


class ContactRepository:
    """Contact-form submissions from public site visitors."""

    @owner_listing(CONTACT_CACHE_PREFIX)
    def _owner_submissions(self, user_id):
        """Every contact submission for one site owner, newest first.

        The leads screen renders a stats row and a table, and each used to run
        its own full scan of this collection -- two round trips for one page,
        the second of them returning documents the first had already fetched.
        Read once here, shared for the request, and held briefly across requests
        so the read/unread tabs, the search box and the pager do not each pay
        for a fresh scan of the same set.

        Note the original sort: ``sort(key=lambda d: d.to_dict().get('created_at') or '')``
        compared ``DatetimeWithNanoseconds`` against ``''`` whenever any
        document was missing ``created_at``, which raises ``TypeError`` and --
        because the whole method is wrapped in ``except Exception`` -- surfaced
        as an empty leads list rather than an error. Comparing floats fixes
        that as well as the round trip.
        """
        try:
            docs = list(
                self.db.collection('contact_submissions')
                .where(filter=FieldFilter('site_owner_id', '==', user_id))
                .stream()
            )
        except Exception:
            logger.exception("Error fetching contact submissions for owner")
            return []

        submissions = []
        for doc in docs:
            data = doc.to_dict()
            data['id'] = doc.id
            submissions.append(data)
        submissions.sort(key=_created_at_key, reverse=True)
        return submissions

    def save_contact_submission(self, user_id, data):
        """
        Saves a contact form submission to Firestore.
        Stores in 'contact_submissions' collection.
        """
        try:
            submission = {
                'site_owner_id': user_id,
                'name': data.get('name', '').strip()[:100],
                'email': data.get('email', '').strip()[:100],
                'subject': data.get('subject', '').strip()[:200],
                'message': data.get('message', '').strip()[:5000],
                'created_at': firestore.SERVER_TIMESTAMP,
                'read': False
            }
            doc_ref = self.db.collection('contact_submissions').add(submission)
            _invalidate_contact()
            return doc_ref[1].id
        except Exception:
            logger.exception("Error saving contact submission")
            return None

    def get_contact_submissions(self, user_id, page=1, per_page=10, status_filter='all', search=''):
        """Get paginated contact submissions for a site owner.

        The previous version also called ``to_dict()`` inside every filter and
        every comparison, re-deserialising each document up to four times per
        request. That is cheap next to a round trip but free to avoid: the
        documents are deserialised once in :meth:`_owner_submissions`.
        """
        try:
            submissions = self._owner_submissions(user_id)

            if status_filter == 'unread':
                submissions = [s for s in submissions if not s.get('read', False)]
            elif status_filter == 'read':
                submissions = [s for s in submissions if s.get('read', False)]

            if search:
                needle = search.lower()
                submissions = [
                    s for s in submissions
                    if needle in (s.get('name') or '').lower()
                    or needle in (s.get('email') or '').lower()
                    or needle in (s.get('subject') or '').lower()
                ]

            total = len(submissions)
            start = (page - 1) * per_page
            # Deep-copied: the timestamp below is rewritten in place, and these
            # dicts are shared with every other reader in this request.
            page_items = deepcopy(submissions[start:start + per_page])

            for data in page_items:
                created = data.get('created_at')
                if created:
                    data['created_at'] = (
                        created.isoformat() if hasattr(created, 'isoformat') else str(created)
                    )

            return {
                'submissions': page_items,
                'total': total,
                'page': page,
                'per_page': per_page,
                'total_pages': (total + per_page - 1) // per_page
            }
        except Exception:
            logger.exception("Error fetching contact submissions")
            return {'submissions': [], 'total': 0, 'page': 1, 'per_page': per_page, 'total_pages': 0}

    def get_contact_stats(self, user_id):
        """Get contact submission statistics.

        Counted from the same shared read the table uses, so the stats row no
        longer costs a round trip of its own.
        """
        try:
            submissions = self._owner_submissions(user_id)
            total = len(submissions)
            unread = sum(1 for s in submissions if not s.get('read', False))
            return {'total': total, 'unread': unread, 'read': total - unread}
        except Exception:
            logger.exception("Error fetching contact stats")
            return {'total': 0, 'unread': 0, 'read': 0}

    def mark_contact_read(self, submission_id):
        """Mark a contact submission as read."""
        try:
            self.db.collection('contact_submissions').document(submission_id).update({'read': True})
            _invalidate_contact()
            return True
        except Exception:
            logger.exception("Error marking contact read")
            return False

    def delete_contact_submission(self, submission_id):
        """Delete a contact submission."""
        try:
            self.db.collection('contact_submissions').document(submission_id).delete()
            _invalidate_contact()
            return True
        except Exception:
            logger.exception("Error deleting contact submission")
            return False
