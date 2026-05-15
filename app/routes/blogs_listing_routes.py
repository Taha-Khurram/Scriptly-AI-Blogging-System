from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, abort
from app.firebase.firestore_service import FirestoreService
from functools import wraps

blogs_bp = Blueprint('blogs_listing', __name__)
db_service = FirestoreService()


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('auth_bp.login'))
        return f(*args, **kwargs)
    return decorated_function


@blogs_bp.after_request
def add_cache_headers(response):
    if request.headers.get('X-Pjax') and response.status_code == 200:
        response.headers['Cache-Control'] = 'private, max-age=10, stale-while-revalidate=30'
    return response


@blogs_bp.route('/all-blogs')
@login_required
def all_blogs_page():
    user_id = session.get('user_id')
    user_role = session.get('user_role', 'USER')

    if user_role == 'ADMIN':
        categories = db_service.get_all_categories(user_id=user_id)
        sub_users = db_service.get_my_sub_users(user_id)
        user_ids = [user_id] + [u.get('uid') for u in sub_users if u.get('uid')]
    else:
        categories = db_service.get_user_blog_categories(user_id)
        user_ids = [user_id]

    initial_blogs = db_service.get_all_blogs_filtered(
        user_ids=user_ids,
        status_filter='all',
        category_filter='all',
        search='',
        date_from='',
        date_to='',
        page=1,
        per_page=10
    )

    return render_template('all_blogs.html', categories=categories, initial_data=initial_blogs)


@blogs_bp.route('/api/all-blogs', methods=['GET'])
@login_required
def api_get_all_blogs():
    user_id = session.get('user_id')
    user_role = session.get('user_role', 'USER')

    # Determine which user IDs to query
    if user_role == 'ADMIN':
        sub_users = db_service.get_my_sub_users(user_id)
        user_ids = [user_id] + [u.get('uid') for u in sub_users if u.get('uid')]
    else:
        user_ids = [user_id]

    status_filter = request.args.get('status', 'all')
    category_filter = request.args.get('category', 'all')
    search = request.args.get('search', '').strip()
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    page = int(request.args.get('page', 1))
    per_page = 10

    result = db_service.get_all_blogs_filtered(
        user_ids=user_ids,
        status_filter=status_filter,
        category_filter=category_filter,
        search=search,
        date_from=date_from,
        date_to=date_to,
        page=page,
        per_page=per_page
    )

    return jsonify({"success": True, **result})
