"""Media library: upload, browse and delete a user's images.

Storage moved out of this module. It previously wrote to the container's local
filesystem, which is wiped on every deploy -- leaving Firestore metadata
pointing at images that no longer exist, with no recovery path -- and made
horizontal scaling impossible, since an image uploaded to one instance is a 404
on every other. :mod:`app.services.storage_service` owns durable storage,
content-type detection and path safety; this module handles request shape,
ownership and pagination.
"""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, render_template, request, session

from app.core.errors import ValidationError
from app.core.extensions import limiter
from app.core.logging import get_logger
from app.core.security import login_required, owns_resource_or_admin
from app.firebase.firestore_service import FirestoreService
from app.services.storage_service import ALLOWED_IMAGE_TYPES, storage

logger = get_logger(__name__)

gallery_bp = Blueprint('gallery', __name__)
db_service = FirestoreService()

MAX_BULK_DELETE = 100
PER_PAGE = 24

# The whole vocabulary the grid can be filtered and ordered by, kept here so an
# unknown ?sort= or ?type= falls back to the default instead of reaching the
# data layer as an unhandled key.
SORT_OPTIONS = {'newest', 'oldest', 'name', 'name_desc', 'largest', 'smallest'}
TYPE_OPTIONS = {'all', 'jpg', 'png', 'gif', 'webp', 'other'}


@gallery_bp.after_request
def add_cache_headers(response):
    if request.headers.get('X-Pjax') and response.status_code == 200:
        response.headers['Cache-Control'] = 'private, max-age=10, stale-while-revalidate=30'
    return response


def read_query():
    """The grid's view state, normalised, from the query string.

    The page route and the API route read it the same way, so a link carrying
    ?search=&type=&sort= renders server-side exactly as the JS would have
    rendered it -- which is what makes a filtered gallery shareable and
    survivable across a reload.
    """
    sort = (request.args.get('sort') or 'newest').lower()
    file_type = (request.args.get('type') or 'all').lower()
    return {
        'search': (request.args.get('search') or '').strip()[:120],
        'file_type': file_type if file_type in TYPE_OPTIONS else 'all',
        'sort': sort if sort in SORT_OPTIONS else 'newest',
        'page': max(1, request.args.get('page', 1, type=int) or 1),
    }


@gallery_bp.route('/gallery')
@login_required
def gallery_page():
    query = read_query()
    initial_data = db_service.get_gallery_images(
        session['user_id'],
        page=query['page'],
        per_page=PER_PAGE,
        search=query['search'],
        file_type=query['file_type'],
        sort=query['sort'],
    )
    return render_template('gallery.html', initial_data=initial_data, query=query)


@gallery_bp.route('/api/gallery/upload', methods=['POST'])
@login_required
@limiter.limit('30 per minute; 300 per hour')
def upload_image():
    """Store one image and record its metadata.

    Rate-limited because each call is a write to both object storage and
    Firestore. The body-size ceiling is enforced by ``MAX_CONTENT_LENGTH`` at
    the WSGI layer, *before* this function runs -- the original read the whole
    body into memory and checked its length afterwards, so a single oversized
    POST could exhaust the instance before the guard was ever evaluated.
    """
    if 'file' not in request.files:
        raise ValidationError('No file provided.')

    upload = request.files['file']
    if not upload.filename:
        raise ValidationError('No file selected.')

    data = upload.read()
    user_id = session['user_id']

    # validate_image raises ValidationError / PayloadTooLargeError with a
    # user-safe message, and determines the content type from the file's magic
    # bytes rather than the client-supplied header.
    url, content_type, size = storage.save_image(user_id, data, upload.filename)

    image_id = db_service.save_gallery_image(
        user_id=user_id,
        filename=upload.filename,
        url=url,
        size=size,
        content_type=content_type,
    )

    if not image_id:
        # Metadata failed after the bytes landed. Remove the orphan rather than
        # leaving a file nothing references and nobody can delete.
        storage.delete(url)
        logger.error('Gallery metadata write failed', extra={'user_id': user_id})
        raise ValidationError('Upload failed. Please try again.')

    # created_at is echoed back so the client can insert the new tile with its
    # full metadata instead of reloading the page to learn one timestamp.
    saved = db_service.get_gallery_image(image_id) or {}

    logger.info(
        'Gallery upload stored',
        extra={'user_id': user_id, 'image_id': image_id,
               'bytes': size, 'backend': storage.backend_name},
    )

    return jsonify({
        'success': True,
        'image': {
            'id': image_id,
            'url': url,
            'filename': upload.filename,
            'size': size,
            'content_type': content_type,
            'created_at': saved.get('created_at', ''),
        },
    })


@gallery_bp.route('/api/gallery/images', methods=['GET'])
@login_required
def get_images():
    query = read_query()
    per_page = request.args.get('per_page', PER_PAGE, type=int)

    result = db_service.get_gallery_images(
        session['user_id'],
        page=query['page'],
        # Bounded: an unclamped per_page is a way to ask for the whole
        # collection in one request.
        per_page=max(1, min(per_page or PER_PAGE, 100)),
        search=query['search'],
        file_type=query['file_type'],
        sort=query['sort'],
    )
    return jsonify({'success': True, **result})


@gallery_bp.route('/api/gallery/images/<image_id>', methods=['DELETE'])
@login_required
def delete_image(image_id):
    """Delete one image the caller owns."""
    image = db_service.get_gallery_image(image_id)
    if not image:
        return jsonify({'success': False, 'error': 'Image not found'}), 404

    # Ownership is checked *before* the document is removed. The original order
    # -- delete, then compare the returned user_id -- meant any signed-in
    # account could destroy another account's image metadata and only be told
    # "403" after the fact.
    owns_resource_or_admin(image.get('user_id'))

    if not db_service.delete_gallery_image(image_id):
        return jsonify({'success': False, 'error': 'Delete failed'}), 500

    storage.delete(image.get('url'))
    logger.info('Gallery image deleted', extra={'image_id': image_id})
    return jsonify({'success': True, 'deleted': [image_id]})


@gallery_bp.route('/api/gallery/images/bulk-delete', methods=['POST'])
@login_required
@limiter.limit('10 per minute')
def bulk_delete_images():
    """Delete a selection in one round trip.

    A media library is where multi-select earns its keep, and firing N separate
    DELETEs for a 40-image sweep is both slow and half-atomic from the reader's
    point of view. Anything the caller does not own is skipped and reported
    back rather than aborting the whole batch.
    """
    user_id = session['user_id']
    payload = request.get_json(silent=True) or {}
    ids = payload.get('ids')

    if not isinstance(ids, list) or not ids:
        raise ValidationError('No images selected.')

    ids = [str(i) for i in ids if i][:MAX_BULK_DELETE]

    deleted, failed = [], []
    for image_id in ids:
        image = db_service.get_gallery_image(image_id)
        if not image or image.get('user_id') != user_id:
            failed.append(image_id)
            continue
        if not db_service.delete_gallery_image(image_id):
            failed.append(image_id)
            continue
        storage.delete(image.get('url'))
        deleted.append(image_id)

    if failed:
        logger.info(
            'Bulk delete partially skipped',
            extra={'user_id': user_id, 'deleted': len(deleted), 'skipped': len(failed)},
        )

    return jsonify({
        'success': bool(deleted),
        'deleted': deleted,
        'failed': failed,
        'error': None if deleted else 'Nothing could be deleted',
    })


@gallery_bp.route('/api/gallery/limits', methods=['GET'])
@login_required
def upload_limits():
    """What the client should enforce before attempting an upload.

    Served rather than duplicated in JS so the browser-side check can never
    drift from the server's actual limit -- which is how a user ends up with a
    file the UI accepted and the server rejected.
    """
    return jsonify({
        'success': True,
        'max_bytes': storage.max_bytes,
        'allowed_extensions': sorted(ALLOWED_IMAGE_TYPES),
        'durable_storage': storage.is_durable,
        'request_max_bytes': current_app.config.get('MAX_CONTENT_LENGTH'),
    })
