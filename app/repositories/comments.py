"""Visitor comments and their AI/admin moderation state.

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


class CommentRepository:
    """Visitor comments and their AI/admin moderation state."""

    def create_comment(self, comment_data):
        """Save a new comment to Firestore. Returns document ID."""
        try:
            comment = {
                'site_owner_id': comment_data['site_owner_id'],
                'blog_id': comment_data['blog_id'],
                'blog_title': comment_data.get('blog_title', '')[:200],
                'commenter_name': comment_data['commenter_name'][:100],
                'commenter_email': comment_data['commenter_email'][:100],
                'original_text': comment_data['original_text'][:5000],
                'moderated_text': comment_data.get('moderated_text', '')[:5000],
                'display_text': comment_data.get('display_text', '')[:5000],
                'ai_action': comment_data.get('ai_action', 'approved'),
                'ai_reason': comment_data.get('ai_reason'),
                'ai_moderated_at': comment_data.get('ai_moderated_at'),
                'admin_edits': [],
                'status': comment_data.get('status', 'published'),
                'removed_by': comment_data.get('removed_by'),
                'removed_at': comment_data.get('removed_at'),
                'removed_reason': comment_data.get('removed_reason'),
                'created_at': firestore.SERVER_TIMESTAMP,
                'updated_at': utcnow()
            }
            doc_ref = self.db.collection('comments').add(comment)
            return doc_ref[1].id
        except Exception:
            logger.exception("Error creating comment")
            return None

    def get_comments_for_blog(self, blog_id):
        """Get published comments for a blog post (public-facing)."""
        try:
            docs = list(
                self.db.collection('comments')
                .where(filter=FieldFilter('blog_id', '==', blog_id))
                .where(filter=FieldFilter('status', '==', 'published'))
                .stream()
            )
            # Sort client-side to avoid composite index requirement
            def _sort_key(doc):
                val = doc.to_dict().get('created_at')
                if val is None:
                    return 0
                if hasattr(val, 'timestamp'):
                    return val.timestamp()
                return 0
            docs.sort(key=_sort_key, reverse=True)
            comments = []
            for doc in docs:
                data = doc.to_dict()
                data['id'] = doc.id
                comments.append(data)
            return comments
        except Exception:
            logger.exception("Error fetching blog comments")
            return []

    def get_comments_for_dashboard(self, site_owner_id, status_filter=None, ai_filter=None, page=1, per_page=20):
        """Get all comments for the dashboard moderation view."""
        try:
            query = self.db.collection('comments').where(
                filter=FieldFilter('site_owner_id', '==', site_owner_id)
            )

            if status_filter and status_filter != 'all':
                if status_filter == 'edited':
                    query = query.where(filter=FieldFilter('ai_action', '==', 'edited'))
                else:
                    query = query.where(filter=FieldFilter('status', '==', status_filter))

            docs = list(query.stream())
            # Sort client-side to avoid requiring composite indexes
            def _sort_key(doc):
                val = doc.to_dict().get('created_at')
                if val is None:
                    return 0
                if hasattr(val, 'timestamp'):
                    return val.timestamp()
                if hasattr(val, 'isoformat'):
                    return val.timestamp() if hasattr(val, 'timestamp') else 0
                return 0
            docs.sort(key=_sort_key, reverse=True)
            total = len(docs)

            # Paginate
            start = (page - 1) * per_page
            end = start + per_page
            page_docs = docs[start:end]

            comments = []
            for doc in page_docs:
                data = doc.to_dict()
                data['id'] = doc.id
                comments.append(data)

            return {
                'comments': comments,
                'total': total,
                'page': page,
                'per_page': per_page,
                'total_pages': (total + per_page - 1) // per_page
            }
        except Exception:
            logger.exception("Error fetching dashboard comments")
            return {'comments': [], 'total': 0, 'page': 1, 'per_page': per_page, 'total_pages': 0}

    def get_comment_by_id(self, comment_id):
        """Fetch a single comment document by ID."""
        try:
            doc = self.db.collection('comments').document(comment_id).get()
            if doc.exists:
                data = doc.to_dict()
                data['id'] = doc.id
                return data
            return None
        except Exception:
            logger.exception("Error fetching comment")
            return None

    def update_comment_display_text(self, comment_id, new_text, admin_id, admin_name, reason=""):
        """Admin edit: updates display_text and appends to admin_edits history."""
        try:
            doc_ref = self.db.collection('comments').document(comment_id)
            doc = doc_ref.get()
            if not doc.exists:
                return False

            current_data = doc.to_dict()
            edit_entry = {
                'admin_id': admin_id,
                'admin_name': admin_name,
                'previous_text': current_data.get('display_text', ''),
                'new_text': new_text[:5000],
                'edited_at': utcnow(),
                'reason': reason[:500] if reason else ''
            }

            doc_ref.update({
                'display_text': new_text[:5000],
                'admin_edits': firestore.ArrayUnion([edit_entry]),
                'updated_at': utcnow()
            })
            return True
        except Exception:
            logger.exception("Error updating comment")
            return False

    def update_comment_status(self, comment_id, new_status, removed_by=None, reason=None):
        """Change comment status (published/removed/pending_delete)."""
        try:
            update_data = {
                'status': new_status,
                'updated_at': utcnow()
            }

            if new_status == 'removed':
                update_data['removed_by'] = removed_by or 'admin'
                update_data['removed_at'] = utcnow()
                update_data['removed_reason'] = reason
            elif new_status == 'published':
                update_data['removed_by'] = None
                update_data['removed_at'] = None
                update_data['removed_reason'] = None

            self.db.collection('comments').document(comment_id).update(update_data)
            return True
        except Exception:
            logger.exception("Error updating comment status")
            return False

    def delete_comment_permanently(self, comment_id):
        """Hard delete a comment document from Firestore."""
        try:
            self.db.collection('comments').document(comment_id).delete()
            return True
        except Exception:
            logger.exception("Error deleting comment")
            return False

    def get_comment_stats(self, site_owner_id):
        """Get comment counts for dashboard stats cards."""
        try:
            docs = list(
                self.db.collection('comments')
                .where(filter=FieldFilter('site_owner_id', '==', site_owner_id))
                .stream()
            )

            total = len(docs)
            published = 0
            ai_edited = 0
            removed = 0

            for doc in docs:
                data = doc.to_dict()
                if data.get('status') == 'published':
                    published += 1
                if data.get('ai_action') == 'edited':
                    ai_edited += 1
                if data.get('status') == 'removed':
                    removed += 1

            return {
                'total': total,
                'published': published,
                'ai_edited': ai_edited,
                'removed': removed
            }
        except Exception:
            logger.exception("Error fetching comment stats")
            return {'total': 0, 'published': 0, 'ai_edited': 0, 'removed': 0}
