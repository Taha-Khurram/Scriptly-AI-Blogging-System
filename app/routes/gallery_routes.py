from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from app.firebase.firestore_service import FirestoreService
from functools import wraps
import os
import time
import uuid

gallery_bp = Blueprint('gallery', __name__)
db_service = FirestoreService()

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'uploads', 'gallery')


@gallery_bp.after_request
def add_cache_headers(response):
    if request.headers.get('X-Pjax') and response.status_code == 200:
        response.headers['Cache-Control'] = 'private, max-age=10, stale-while-revalidate=30'
    return response


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            if request.path.startswith('/api/'):
                return jsonify({'success': False, 'error': 'Not authenticated'}), 401
            return redirect(url_for('auth_bp.login'))
        return f(*args, **kwargs)
    return decorated_function


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@gallery_bp.route('/gallery')
@login_required
def gallery_page():
    user_id = session.get('user_id')
    initial_data = db_service.get_gallery_images(user_id, page=1, per_page=20)
    return render_template('gallery.html', initial_data=initial_data)


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

        return jsonify({
            'success': True,
            'image': {
                'id': image_id,
                'url': public_url,
                'filename': file.filename,
                'size': len(file_data)
            }
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': 'Upload failed'}), 500


@gallery_bp.route('/api/gallery/images', methods=['GET'])
@login_required
def get_images():
    user_id = session.get('user_id')
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)

    result = db_service.get_gallery_images(user_id, page, per_page)
    return jsonify({'success': True, **result})


@gallery_bp.route('/api/gallery/images/<image_id>', methods=['DELETE'])
@login_required
def delete_image(image_id):
    user_id = session.get('user_id')

    image_data = db_service.delete_gallery_image(image_id)
    if not image_data:
        return jsonify({'success': False, 'error': 'Image not found'}), 404

    if image_data.get('user_id') != user_id:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403

    if image_data.get('url'):
        try:
            file_path = os.path.join(
                os.path.dirname(os.path.dirname(__file__)),
                image_data['url'].lstrip('/')
            )
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception as e:
            print(f"File delete error (metadata already removed): {e}")

    return jsonify({'success': True})
