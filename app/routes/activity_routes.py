from flask import Blueprint, render_template, request, jsonify, session
from app.firebase.firestore_service import FirestoreService
from app.core.security import admin_required
from app.utils.parallel import run_parallel_simple

activity_bp = Blueprint('activity', __name__)
db_service = FirestoreService()

@activity_bp.route('/activity-log')
@admin_required
def activity_page():
    """The audit trail: a stats row, the first page of entries, and the user filter.

    These three reads are independent, so they are issued together. Run back to
    back they cost the sum of their round trips -- and a Firestore round trip
    from this application measures 0.5-3.5 s, which is what made this page one
    of the slowest in the dashboard. ``get_my_sub_users`` is memoised per
    request (see ``app.repositories._helpers.request_cached``), so the team
    lookup that all three of these need happens exactly once even though they
    now run concurrently.
    """
    admin_id = session.get('user_id')

    # Resolve the team first: the three queries below all derive from it, and
    # doing it here means they share one memoised result instead of racing to
    # populate it three times.
    sub_users = db_service.get_my_sub_users(admin_id)

    results = run_parallel_simple([
        (db_service.get_activity_stats, (admin_id,)),
        (lambda: db_service.get_all_activity_for_admin(
            admin_id=admin_id,
            type_filter='all',
            user_filter='all',
            search='',
            date_from='',
            date_to='',
            page=1,
            per_page=10,
        ), ()),
    ], max_workers=2)

    stats = results[0] or {}
    result = results[1] or {}

    activities = result.get('activities', [])
    for act in activities:
        ts = act.get('timestamp')
        if ts and hasattr(ts, 'isoformat'):
            act['timestamp'] = ts.isoformat()

    # The admin's own label comes from the session, not from a third Firestore
    # read. It is the same value every other screen in the dashboard displays
    # for the signed-in user, and this dropdown wants a label, not a fresh
    # authoritative record.
    users = [{"uid": admin_id, "name": session.get('user_name') or "Admin"}]
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
