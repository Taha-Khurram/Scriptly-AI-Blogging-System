"""Metadata for the media library. The bytes live in storage_service.

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


class GalleryRepository:
    """Metadata for the media library. The bytes live in storage_service."""

    # (key function, reverse) per sort option the grid offers. Sorting happens
    # in Python rather than Firestore because the grid also filters by filename
    # substring and by file type, neither of which Firestore can express in the
    # same query as an ordering.
    GALLERY_SORTS = {
        'newest': (lambda i: i.get('created_at') or '', True),
        'oldest': (lambda i: i.get('created_at') or '', False),
        'name': (lambda i: (i.get('filename') or '').lower(), False),
        'name_desc': (lambda i: (i.get('filename') or '').lower(), True),
        'largest': (lambda i: int(i.get('size') or 0), True),
        'smallest': (lambda i: int(i.get('size') or 0), False),
    }

    def save_gallery_image(self, user_id, filename, url, size, content_type):
        try:
            doc_data = {
                'user_id': user_id,
                'filename': filename,
                'url': url,
                'size': size,
                'content_type': content_type,
                'created_at': utcnow().isoformat()
            }
            doc_ref = self.db.collection('gallery_images').add(doc_data)
            return doc_ref[1].id
        except Exception:
            logger.exception("Error saving gallery image")
            return None

    @staticmethod
    def _gallery_ext(image):
        """Normalised file type for one image: jpg | png | gif | webp | other.

        Read from the stored filename and, failing that, from the generated
        URL — the URL always carries an extension because upload builds it,
        while `filename` is whatever the visitor's file was called.
        """
        for candidate in (image.get('filename'), image.get('url')):
            if candidate and '.' in candidate:
                ext = candidate.rsplit('.', 1)[1].lower().strip()
                if ext == 'jpeg':
                    return 'jpg'
                if ext in ('jpg', 'png', 'gif', 'webp'):
                    return ext
        return 'other'

    def get_gallery_images(self, user_id, page=1, per_page=20,
                           search=None, file_type='all', sort='newest'):
        try:
            query = self.db.collection('gallery_images').where(
                filter=FieldFilter('user_id', '==', user_id)
            )

            images = []
            for doc in query.stream():
                data = doc.to_dict()
                data['id'] = doc.id
                images.append(data)

            library_total = len(images)

            term = (search or '').strip().lower()
            if term:
                images = [i for i in images if term in (i.get('filename') or '').lower()]

            # Facet counts are taken after the search and *before* the type
            # filter, so each tab reports what choosing it would actually show
            # rather than collapsing to 0 the moment another tab is active.
            type_counts = {}
            for image in images:
                key = self._gallery_ext(image)
                type_counts[key] = type_counts.get(key, 0) + 1

            matched_size = sum(int(i.get('size') or 0) for i in images)

            wanted = (file_type or 'all').lower()
            if wanted != 'all':
                images = [i for i in images if self._gallery_ext(i) == wanted]

            key_fn, reverse = self.GALLERY_SORTS.get(sort, self.GALLERY_SORTS['newest'])
            images.sort(key=key_fn, reverse=reverse)

            total = len(images)
            per_page = max(1, per_page)
            total_pages = (total + per_page - 1) // per_page

            # Clamp rather than trust: deleting the last item on the last page
            # would otherwise leave the caller asking for a page that no longer
            # exists and getting an empty grid back.
            page = max(1, min(int(page or 1), total_pages or 1))
            start = (page - 1) * per_page

            return {
                'images': images[start:start + per_page],
                'total': total,
                'library_total': library_total,
                'matched_size': matched_size,
                'type_counts': type_counts,
                'page': page,
                'per_page': per_page,
                'total_pages': total_pages,
            }
        except Exception:
            logger.exception("Error fetching gallery images")
            return {
                'images': [], 'total': 0, 'library_total': 0, 'matched_size': 0,
                'type_counts': {}, 'page': 1, 'per_page': per_page, 'total_pages': 0,
            }

    def get_gallery_image(self, image_id):
        """Read one image's metadata without touching it.

        Exists so a caller can check ownership *before* deleting rather than
        after — `delete_gallery_image` removes the document and then hands back
        what it removed, which is too late to refuse the request.
        """
        try:
            doc = self.db.collection('gallery_images').document(image_id).get()
            if not doc.exists:
                return None
            data = doc.to_dict()
            data['id'] = doc.id
            return data
        except Exception:
            logger.exception("Error fetching gallery image")
            return None

    def delete_gallery_image(self, image_id):
        try:
            doc_ref = self.db.collection('gallery_images').document(image_id)
            doc = doc_ref.get()
            if doc.exists:
                data = doc.to_dict()
                doc_ref.delete()
                return data
            return None
        except Exception:
            logger.exception("Error deleting gallery image")
            return None
