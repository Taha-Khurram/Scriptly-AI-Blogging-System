from flask import Blueprint, render_template, request, jsonify, session
from app.firebase.firestore_service import FirestoreService
import os
import time
import uuid
from app.core.security import login_required
from app.core.logging import get_logger

logger = get_logger(__name__)

gallery_bp = Blueprint('gallery', __name__)
db_service = FirestoreService()

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
MAX_BULK_DELETE = 100
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'uploads', 'gallery')

# The whole vocabulary the grid can be filtered and ordered by, kept here so an
# unknown ?sort= or ?type= falls back to the default instead of reaching the
# data layer as an unhandled key.
SORT_OPTIONS = {'newest', 'oldest', 'name', 'name_desc', 'largest', 'smallest'}
TYPE_OPTIONS = {'all', 'jpg', 'png', 'gif', 'webp', 'other'}
PER_PAGE = 24


@gallery_bp.after_request
def add_cache_headers(response):
    if request.headers.get('X-Pjax') and response.status_code == 200:
        response.headers['Cache-Control'] = 'private, max-age=10, stale-while-revalidate=30'
    return response

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def read_query():
    """The grid's view state, normalised, from the query string.

    The page route and the API route read it the same way, so a link carrying
    ?search=&type=&sort= renders server-side exactly as the JS would have
    rendered it — which is what makes a filtered gallery shareable and
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


def image_file_path(url):
    """Absolute path on disk for a stored image URL, or None if it escapes.

    `url` is generated at upload time, but it is read back from Firestore
    before being joined onto the uploads folder, so it is treated as untrusted:
    a value containing `..` must never resolve to a delete outside the gallery.
    """
    if not url:
        return None
    app_dir = os.path.dirname(os.path.dirname(__file__))
    candidate = os.path.normpath(os.path.join(app_dir, url.lstrip('/').replace('/', os.sep)))
    root = os.path.normpath(UPLOAD_FOLDER)
    try:
        # commonpath raises on Windows when the two sit on different drives,
        # which is itself a reason to refuse.
        if os.path.commonpath([candidate, root]) != root:
            return None
    except ValueError:
        return None
    return candidate


def remove_image_file(url):
    path = image_file_path(url)
    if not path:
        return
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError as e:
        # The metadata is already gone, so the image has left the library
        # either way; a stranded file is a cleanup problem, not a failed delete.
        logger.exception("File delete error (metadata already removed)")


@gallery_bp.route('/gallery')
@login_required
def gallery_page():
    user_id = session.get('user_id')
    q = read_query()
    initial_data = db_service.get_gallery_images(
        user_id,
        page=q['page'],
        per_page=PER_PAGE,
        search=q['search'],
        file_type=q['file_type'],
        sort=q['sort'],
    )
    return render_template('gallery.html', initial_data=initial_data, query=q)


@gallery_bp.route('/api/gallery/upload', methods=['POST'])
@login_required
def upload_image():
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No file provided'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'error': 'No file selected'}), 400

    if not allowed_file(file.filename):
        return jsonify({'success': False, 'error': 'File type not allowed. Use: png, jpg, jpeg, gif, webp'}), 400

    file_data = file.read()
    if len(file_data) > MAX_FILE_SIZE:
        return jsonify({'success': False, 'error': 'File too large. Maximum 5MB'}), 400

    user_id = session.get('user_id')
    ext = file.filename.rsplit('.', 1)[1].lower()
    unique_name = f"{int(time.time())}_{uuid.uuid4().hex[:8]}.{ext}"

    user_folder = os.path.join(UPLOAD_FOLDER, user_id)
    os.makedirs(user_folder, exist_ok=True)

    file_path = os.path.join(user_folder, unique_name)

    try:
        with open(file_path, 'wb') as f:
            f.write(file_data)

        public_url = f"/static/uploads/gallery/{user_id}/{unique_name}"

        image_id = db_service.save_gallery_image(
            user_id=user_id,
            filename=file.filename,
            url=public_url,
            size=len(file_data),
            content_type=file.content_type
        )

        # created_at is echoed back so the client can insert the new tile into
        # the grid with its full metadata instead of reloading the whole page
        # to learn one timestamp.
        saved = db_service.get_gallery_image(image_id) if image_id else None

        return jsonify({
            'success': True,
            'image': {
                'id': image_id,
                'url': public_url,
                'filename': file.filename,
                'size': len(file_data),
                'content_type': file.content_type,
                'created_at': (saved or {}).get('created_at', '')
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'error': 'Upload failed'}), 500


@gallery_bp.route('/api/gallery/images', methods=['GET'])
@login_required
def get_images():
    user_id = session.get('user_id')
    q = read_query()
    per_page = request.args.get('per_page', PER_PAGE, type=int)

    result = db_service.get_gallery_images(
        user_id,
        page=q['page'],
        per_page=max(1, min(per_page or PER_PAGE, 100)),
        search=q['search'],
        file_type=q['file_type'],
        sort=q['sort'],
    )
    return jsonify({'success': True, **result})


@gallery_bp.route('/api/gallery/images/<image_id>', methods=['DELETE'])
@login_required
def delete_image(image_id):
    user_id = session.get('user_id')

    # Ownership is checked *before* the document is removed. The previous order
    # — delete, then compare the returned user_id — meant any signed-in account
    # could destroy another account's image metadata and only be told "403"
    # after the fact.
    image_data = db_service.get_gallery_image(image_id)
    if not image_data:
        return jsonify({'success': False, 'error': 'Image not found'}), 404

    if image_data.get('user_id') != user_id:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403

    if not db_service.delete_gallery_image(image_id):
        return jsonify({'success': False, 'error': 'Delete failed'}), 500

    remove_image_file(image_data.get('url'))
    return jsonify({'success': True, 'deleted': [image_id]})


@gallery_bp.route('/api/gallery/images/bulk-delete', methods=['POST'])
@login_required
def bulk_delete_images():
    """Delete a selection in one round trip.

    A media library is where multi-select earns its keep, and firing N separate
    DELETEs for a 40-image sweep is both slow and half-atomic from the reader's
    point of view. Anything the caller does not own is skipped and reported
    back rather than aborting the whole batch.
    """
    user_id = session.get('user_id')
    payload = request.get_json(silent=True) or {}
    ids = payload.get('ids')

    if not isinstance(ids, list) or not ids:
        return jsonify({'success': False, 'error': 'No images selected'}), 400

    ids = [str(i) for i in ids if i][:MAX_BULK_DELETE]

    deleted, failed = [], []
    for image_id in ids:
        image_data = db_service.get_gallery_image(image_id)
        if not image_data or image_data.get('user_id') != user_id:
            failed.append(image_id)
            continue
        if not db_service.delete_gallery_image(image_id):
            failed.append(image_id)
            continue
        remove_image_file(image_data.get('url'))
        deleted.append(image_id)

    return jsonify({
        'success': bool(deleted),
        'deleted': deleted,
        'failed': failed,
        'error': None if deleted else 'Nothing could be deleted'
    })
