from flask import Blueprint, render_template, request, jsonify, session
from app.firebase.firestore_service import FirestoreService
from app.core.security import login_required
from app.utils.parallel import run_parallel_simple

blogs_bp = Blueprint('blogs_listing', __name__)
db_service = FirestoreService()

@blogs_bp.after_request
def add_cache_headers(response):
    if request.headers.get('X-Pjax') and response.status_code == 200:
        response.headers['Cache-Control'] = 'private, max-age=10, stale-while-revalidate=30'
    return response


def _team_scope(user_id, user_role):
    """``(user_ids, author_names)`` for the caller's visible set of authors.

    ``get_my_sub_users`` already returns each team member's whole user document,
    so the display names are in hand the moment the team is known. Returning
    them here is what lets ``get_all_blogs_filtered`` skip one document read per
    team member -- it used to re-fetch each user by id to recover a name it had
    just been handed.
    """
    if user_role != 'ADMIN':
        return [user_id], {user_id: session.get('user_name') or 'Unknown'}

    sub_users = db_service.get_my_sub_users(user_id)
    names = {user_id: session.get('user_name') or 'Unknown'}
    ids = [user_id]
    for u in sub_users:
        uid = u.get('uid')
        if not uid:
            continue
        ids.append(uid)
        names[uid] = (
            u.get('name')
            or (u.get('email') or '').split('@')[0]
            or 'Unknown'
        )
    return ids, names


@blogs_bp.route('/all-blogs')
@login_required
def all_blogs_page():
    """The all-blogs table plus the category filter.

    The category list and the team lookup are independent, so they are issued
    together -- previously the page ran category -> team -> blogs as three
    sequential stages, and each stage is a Firestore round trip costing
    0.5-3.5 s here.
    """
    user_id = session.get('user_id')
    user_role = session.get('user_role', 'USER')

    if user_role == 'ADMIN':
        results = run_parallel_simple([
            (lambda: db_service.get_all_categories(user_id=user_id), ()),
            (_team_scope, (user_id, user_role)),
        ], max_workers=2)
        categories = results[0] or []
        # run_parallel_simple reports a failed task as None, so the scope is
        # unpacked defensively: a team lookup that failed must still leave the
        # page showing the signed-in user's own posts.
        user_ids, author_names = results[1] or ([user_id], {})
    else:
        categories = db_service.get_user_blog_categories(user_id)
        user_ids, author_names = _team_scope(user_id, user_role)

    initial_blogs = db_service.get_all_blogs_filtered(
        user_ids=user_ids,
        status_filter='all',
        category_filter='all',
        search='',
        date_from='',
        date_to='',
        page=1,
        per_page=10,
        author_names=author_names,
    )

    return render_template('all_blogs.html', categories=categories, initial_data=initial_blogs)


@blogs_bp.route('/api/all-blogs', methods=['GET'])
@login_required
def api_get_all_blogs():
    user_id = session.get('user_id')
    user_role = session.get('user_role', 'USER')

    # Names come back with the team, so the blog query does not have to re-read
    # one user document per member to label its rows.
    user_ids, author_names = _team_scope(user_id, user_role)

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
        per_page=per_page,
        author_names=author_names,
    )

    return jsonify({"success": True, **result})
