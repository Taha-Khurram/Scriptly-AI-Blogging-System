"""Vector embeddings that back semantic search over published posts.

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
from google.cloud.firestore_v1.base_query import FieldFilter

from app.core.logging import get_logger

logger = get_logger(__name__)


class EmbeddingRepository:
    """Vector embeddings that back semantic search over published posts."""

    def update_blog_embedding(self, blog_id, embedding):
        """
        Store embedding vector for a blog post.
        Called when blog is published or updated.
        """
        try:
            doc_ref = self.db.collection(self.collection_name).document(blog_id)
            doc_ref.update({
                'embedding': embedding,
                'embedding_updated_at': utcnow()
            })
            return True
        except Exception:
            logger.exception("Error storing embedding")
            return False

    def get_blogs_with_embeddings(self, user_id, limit=100):
        """
        Fetch published blogs that have embeddings stored.
        Returns blogs with embedding vectors for semantic search.
        """
        try:
            blogs = []
            blog_ids = set()

            # Query by site_owner_id
            site_owner_query = self.db.collection(self.collection_name)\
                .where(filter=FieldFilter('site_owner_id', '==', user_id))\
                .where(filter=FieldFilter('status', '==', 'PUBLISHED'))

            for doc in site_owner_query.stream():
                data = doc.to_dict()
                # Only include blogs with embeddings
                if data.get('embedding'):
                    data['id'] = doc.id
                    blog_ids.add(doc.id)
                    blogs.append(data)

            # Fallback: also fetch by author_id for older blogs
            fallback_query = self.db.collection(self.collection_name)\
                .where(filter=FieldFilter('author_id', '==', user_id))\
                .where(filter=FieldFilter('status', '==', 'PUBLISHED'))

            for doc in fallback_query.stream():
                if doc.id not in blog_ids:
                    data = doc.to_dict()
                    if data.get('embedding'):
                        data['id'] = doc.id
                        blogs.append(data)

            return blogs[:limit]
        except Exception:
            logger.exception("Error fetching blogs with embeddings")
            return []

    def get_blogs_without_embeddings(self, user_id=None, limit=100):
        """
        Fetch published blogs that don't have embeddings yet.
        Used for backfilling embeddings.
        """
        try:
            blogs = []

            if user_id:
                # Query for specific user
                query = self.db.collection(self.collection_name)\
                    .where(filter=FieldFilter('site_owner_id', '==', user_id))\
                    .where(filter=FieldFilter('status', '==', 'PUBLISHED'))
            else:
                # Query all published blogs
                query = self.db.collection(self.collection_name)\
                    .where(filter=FieldFilter('status', '==', 'PUBLISHED'))

            for doc in query.stream():
                data = doc.to_dict()
                # Only include blogs without embeddings
                if not data.get('embedding'):
                    data['id'] = doc.id
                    blogs.append(data)

            return blogs[:limit]
        except Exception:
            logger.exception("Error fetching blogs without embeddings")
            return []
