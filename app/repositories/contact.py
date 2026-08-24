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
from firebase_admin import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from app.core.logging import get_logger

logger = get_logger(__name__)


class ContactRepository:
    """Contact-form submissions from public site visitors."""

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
            return doc_ref[1].id
        except Exception:
            logger.exception("Error saving contact submission")
            return None

    def get_contact_submissions(self, user_id, page=1, per_page=10, status_filter='all', search=''):
        """Get paginated contact submissions for a site owner."""
        try:
            query = self.db.collection('contact_submissions')\
                .where(filter=FieldFilter('site_owner_id', '==', user_id))

            docs = list(query.stream())
            docs.sort(key=lambda d: d.to_dict().get('created_at') or '', reverse=True)

            if status_filter == 'unread':
                docs = [d for d in docs if not d.to_dict().get('read', False)]
            elif status_filter == 'read':
                docs = [d for d in docs if d.to_dict().get('read', False)]

            if search:
                search_lower = search.lower()
                filtered = []
                for d in docs:
                    data = d.to_dict()
                    if (search_lower in data.get('name', '').lower() or
                        search_lower in data.get('email', '').lower() or
                        search_lower in data.get('subject', '').lower()):
                        filtered.append(d)
                docs = filtered

            total = len(docs)
            start = (page - 1) * per_page
            page_docs = docs[start:start + per_page]

            submissions = []
            for doc in page_docs:
                data = doc.to_dict()
                data['id'] = doc.id
                if data.get('created_at'):
                    data['created_at'] = data['created_at'].isoformat() if hasattr(data['created_at'], 'isoformat') else str(data['created_at'])
                submissions.append(data)

            return {
                'submissions': submissions,
                'total': total,
                'page': page,
                'per_page': per_page,
                'total_pages': (total + per_page - 1) // per_page
            }
        except Exception:
            logger.exception("Error fetching contact submissions")
            return {'submissions': [], 'total': 0, 'page': 1, 'per_page': per_page, 'total_pages': 0}

    def get_contact_stats(self, user_id):
        """Get contact submission statistics."""
        try:
            docs = list(
                self.db.collection('contact_submissions')
                .where(filter=FieldFilter('site_owner_id', '==', user_id))
                .stream()
            )
            total = len(docs)
            unread = sum(1 for d in docs if not d.to_dict().get('read', False))
            return {'total': total, 'unread': unread, 'read': total - unread}
        except Exception:
            logger.exception("Error fetching contact stats")
            return {'total': 0, 'unread': 0, 'read': 0}

    def mark_contact_read(self, submission_id):
        """Mark a contact submission as read."""
        try:
            self.db.collection('contact_submissions').document(submission_id).update({'read': True})
            return True
        except Exception:
            logger.exception("Error marking contact read")
            return False

    def delete_contact_submission(self, submission_id):
        """Delete a contact submission."""
        try:
            self.db.collection('contact_submissions').document(submission_id).delete()
            return True
        except Exception:
            logger.exception("Error deleting contact submission")
            return False
