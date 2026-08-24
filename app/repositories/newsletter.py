"""Newsletter subscribers, send history and drafts.

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
from app.utils.date_utils import utcnow
from firebase_admin import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from app.core.logging import get_logger

logger = get_logger(__name__)


class NewsletterRepository:
    """Newsletter subscribers, send history and drafts."""

    def save_newsletter_subscriber(self, user_id, email):
        """
        Saves a newsletter subscriber to Firestore.
        Uses email as part of doc ID to prevent duplicates.
        Returns tuple: (doc_id, is_new_subscriber)
        """
        try:
            email_clean = email.strip().lower()
            # Create unique doc ID to prevent duplicates
            doc_id = f"{user_id}_{email_clean.replace('@', '_at_').replace('.', '_')}"

            # Check if subscriber already exists
            doc_ref = self.db.collection('newsletter_subscribers').document(doc_id)
            existing_doc = doc_ref.get()

            if existing_doc.exists:
                existing_data = existing_doc.to_dict()
                # If already active subscriber, return as existing
                if existing_data.get('active', False):
                    return (doc_id, False)  # Already subscribed
                # If was unsubscribed, resubscribe them
                doc_ref.update({
                    'active': True,
                    'resubscribed_at': firestore.SERVER_TIMESTAMP
                })
                return (doc_id, True)  # Resubscribed

            # New subscriber
            subscriber = {
                'site_owner_id': user_id,
                'email': email_clean,
                'subscribed_at': firestore.SERVER_TIMESTAMP,
                'active': True
            }
            doc_ref.set(subscriber)
            return (doc_id, True)  # New subscriber
        except Exception:
            logger.exception("Error saving newsletter subscriber")
            return (None, False)

    def get_newsletter_subscribers(self, user_id, limit=100):
        """
        Fetches newsletter subscribers for a site owner.
        """
        try:
            # Simple query without order_by to avoid composite index requirement
            docs = self.db.collection('newsletter_subscribers')\
                .where(filter=FieldFilter('site_owner_id', '==', user_id))\
                .where(filter=FieldFilter('active', '==', True))\
                .limit(limit)\
                .stream()

            subscribers = []
            for doc in docs:
                data = doc.to_dict()
                data['id'] = doc.id
                # Convert timestamp to ISO string for JSON serialization
                if data.get('subscribed_at'):
                    data['subscribed_at'] = data['subscribed_at'].isoformat()
                subscribers.append(data)

            # Sort by subscribed_at in Python (newest first)
            subscribers.sort(
                key=lambda x: x.get('subscribed_at') or '',
                reverse=True
            )
            return subscribers
        except Exception:
            logger.exception("Error fetching newsletter subscribers")
            return []

    def get_subscriber_count(self, user_id):
        """Get total count of active subscribers."""
        try:
            count_query = self.db.collection('newsletter_subscribers')\
                .where(filter=FieldFilter('site_owner_id', '==', user_id))\
                .where(filter=FieldFilter('active', '==', True))\
                .count()
            result = count_query.get()
            return result[0][0].value
        except Exception:
            logger.exception("Error counting subscribers")
            return 0

    def unsubscribe_newsletter(self, user_id, email):
        """Mark subscriber as inactive (unsubscribed)."""
        try:
            email_clean = email.strip().lower()
            doc_id = f"{user_id}_{email_clean.replace('@', '_at_').replace('.', '_')}"
            doc_ref = self.db.collection('newsletter_subscribers').document(doc_id)

            doc = doc_ref.get()
            if not doc.exists:
                return False

            doc_ref.update({
                'active': False,
                'unsubscribed_at': utcnow()
            })
            return True
        except Exception:
            logger.exception("Error unsubscribing")
            return False

    def resubscribe_newsletter(self, user_id, email):
        """Reactivate a previously unsubscribed email."""
        try:
            email_clean = email.strip().lower()
            doc_id = f"{user_id}_{email_clean.replace('@', '_at_').replace('.', '_')}"
            doc_ref = self.db.collection('newsletter_subscribers').document(doc_id)

            doc = doc_ref.get()
            if not doc.exists:
                return False

            doc_ref.update({
                'active': True,
                'resubscribed_at': utcnow()
            })
            return True
        except Exception:
            logger.exception("Error resubscribing")
            return False

    def log_newsletter_send(self, user_id, recipient_count, subject, content_preview="", html_content=""):
        """Log a newsletter send for history tracking."""
        try:
            self.db.collection('newsletter_history').add({
                'user_id': user_id,
                'recipient_count': recipient_count,
                'subject': subject,
                'content_preview': content_preview[:500],
                'html_content': html_content,
                'sent_at': firestore.SERVER_TIMESTAMP,
                'status': 'sent'
            })
            return True
        except Exception:
            logger.exception("Error logging newsletter")
            return False

    def count_newsletter_history(self, user_id):
        """How many newsletters this user has sent.

        The newsletter screen showed this figure as ``len(get_newsletter_history())``,
        which fetched up to twenty full history documents to produce one number
        -- and silently capped the number at twenty, so an account that had sent
        more would under-report. A ``count()`` aggregation is one round trip
        that returns an integer and is exact.
        """
        try:
            query = (self.db.collection('newsletter_history')
                     .where(filter=FieldFilter('user_id', '==', user_id))
                     .count())
            return query.get()[0][0].value
        except Exception:
            logger.exception("Error counting newsletter history")
            return 0

    def get_newsletter_history(self, user_id, limit=20):
        """Get newsletter send history."""
        try:
            # Simple query without order_by to avoid composite index requirement
            docs = self.db.collection('newsletter_history')\
                .where(filter=FieldFilter('user_id', '==', user_id))\
                .limit(limit)\
                .stream()

            history = []
            for doc in docs:
                data = doc.to_dict()
                data['id'] = doc.id
                # Convert timestamp to ISO string for JSON serialization
                if data.get('sent_at'):
                    data['sent_at'] = data['sent_at'].isoformat()
                history.append(data)

            # Sort by sent_at in Python (newest first)
            history.sort(
                key=lambda x: x.get('sent_at') or '',
                reverse=True
            )
            return history
        except Exception:
            logger.exception("Error fetching newsletter history")
            return []

    def get_newsletter_by_id(self, newsletter_id, user_id):
        """Get a single newsletter by ID."""
        try:
            doc = self.db.collection('newsletter_history').document(newsletter_id).get()
            if not doc.exists:
                return None
            data = doc.to_dict()
            # Verify ownership
            if data.get('user_id') != user_id:
                return None
            data['id'] = doc.id
            # Convert timestamp to ISO string for JSON serialization
            if data.get('sent_at'):
                data['sent_at'] = data['sent_at'].isoformat()
            return data
        except Exception:
            logger.exception("Error fetching newsletter by ID")
            return None

    def delete_newsletter(self, newsletter_id, user_id):
        """Delete a newsletter from history."""
        try:
            doc_ref = self.db.collection('newsletter_history').document(newsletter_id)
            doc = doc_ref.get()
            if not doc.exists:
                return False
            # Verify ownership
            if doc.to_dict().get('user_id') != user_id:
                return False
            doc_ref.delete()
            return True
        except Exception:
            logger.exception("Error deleting newsletter")
            return False

    def save_newsletter_draft(self, user_id, draft_data):
        """Save a newsletter draft for later editing."""
        try:
            draft = {
                'user_id': user_id,
                'subject': draft_data.get('subject', ''),
                'intro': draft_data.get('intro', ''),
                'posts': draft_data.get('posts', []),
                'cta_text': draft_data.get('cta_text', 'Read More'),
                'closing': draft_data.get('closing', ''),
                'html_content': draft_data.get('html_content', ''),
                'created_at': firestore.SERVER_TIMESTAMP,
                'updated_at': utcnow(),
                'status': 'draft'
            }
            doc_ref = self.db.collection('newsletter_drafts').add(draft)
            return doc_ref[1].id
        except Exception:
            logger.exception("Error saving newsletter draft")
            return None

    def get_newsletter_drafts(self, user_id, limit=10):
        """Get newsletter drafts."""
        try:
            # Simple query without order_by to avoid composite index requirement
            docs = self.db.collection('newsletter_drafts')\
                .where(filter=FieldFilter('user_id', '==', user_id))\
                .where(filter=FieldFilter('status', '==', 'draft'))\
                .limit(limit)\
                .stream()

            drafts = []
            for doc in docs:
                data = doc.to_dict()
                data['id'] = doc.id
                drafts.append(data)

            # Sort by updated_at in Python (newest first)
            drafts.sort(
                key=lambda x: x.get('updated_at') or '',
                reverse=True
            )
            return drafts
        except Exception:
            logger.exception("Error fetching newsletter drafts")
            return []

    def delete_newsletter_draft(self, draft_id, user_id):
        """Delete a newsletter draft."""
        try:
            doc_ref = self.db.collection('newsletter_drafts').document(draft_id)
            doc = doc_ref.get()
            if not doc.exists:
                return False
            if doc.to_dict().get('user_id') != user_id:
                return False
            doc_ref.delete()
            return True
        except Exception:
            logger.exception("Error deleting newsletter draft")
            return False
