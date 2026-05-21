from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, abort
from app.firebase.firestore_service import FirestoreService
from functools import wraps

activity_bp = Blueprint('activity', __name__)
db_service = FirestoreService()


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('auth_bp.login'))
        if session.get('user_role') != 'ADMIN':
            abort(404)
        return f(*args, **kwargs)
    return decorated_function


@activity_bp.route('/activity-log')
@admin_required
def activity_page():
    admin_id = session.get('user_id')
    stats = db_service.get_activity_stats(admin_id)

    result = db_service.get_all_activity_for_admin(
        admin_id=admin_id,
        type_filter='all',
        user_filter='all',
        search='',
        date_from='',
        date_to='',
        page=1,
        per_page=10
    )

    activities = result.get('activities', [])
    for act in activities:
        ts = act.get('timestamp')
        if ts and hasattr(ts, 'isoformat'):
            act['timestamp'] = ts.isoformat()

    sub_users = db_service.get_my_sub_users(admin_id)
    admin_user = db_service.get_user_by_id(admin_id)
    users = [{"uid": admin_id, "name": admin_user.get("name", "Admin") if admin_user else "Admin"}]
    for u in sub_users:
        users.append({"uid": u.get("uid"), "name": u.get("name", u.get("email", "User"))})

    return render_template(
        'activity.html',
        stats=stats,
        initial_activities=activities,
        initial_total=result.get('total', 0),
        initial_page=result.get('page', 1),
        initial_per_page=result.get('per_page', 10),
        initial_users=users
    )


@activity_bp.route('/api/activity', methods=['GET'])
@admin_required
def api_get_activities():
    admin_id = session.get('user_id')

    type_filter = request.args.get('type', 'all')
    user_filter = request.args.get('user', 'all')
    search = request.args.get('search', '').strip()
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    page = int(request.args.get('page', 1))
    per_page = 10

    result = db_service.get_all_activity_for_admin(
        admin_id=admin_id,
        type_filter=type_filter,
        user_filter=user_filter,
        search=search,
        date_from=date_from,
        date_to=date_to,
        page=page,
        per_page=per_page
    )

    return jsonify({"success": True, **result})


@activity_bp.route('/api/activity/stats', methods=['GET'])
@admin_required
def api_get_activity_stats():
    admin_id = session.get('user_id')
    stats = db_service.get_activity_stats(admin_id)
    return jsonify({"success": True, "stats": stats})


@activity_bp.route('/api/activity/users', methods=['GET'])
@admin_required
def api_get_activity_users():
    admin_id = session.get('user_id')
    sub_users = db_service.get_my_sub_users(admin_id)
    admin_user = db_service.get_user_by_id(admin_id)

    users = [{"uid": admin_id, "name": admin_user.get("name", "Admin") if admin_user else "Admin"}]
    for u in sub_users:
        users.append({"uid": u.get("uid"), "name": u.get("name", u.get("email", "User"))})

    return jsonify({"success": True, "users": users})
