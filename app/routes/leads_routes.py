from flask import Blueprint, render_template, request, jsonify, session
from app.firebase.firestore_service import FirestoreService
from app.core.security import admin_required

leads_bp = Blueprint('leads', __name__)
db_service = FirestoreService()

@leads_bp.route('/leads')
@admin_required
def leads_page():
    """The stats row and the first page of contact submissions.

    Both read through ``ContactRepository._owner_submissions``, which is
    memoised for the request, so this page now costs one Firestore round trip
    where it previously cost two full scans of the same collection.
    """
    admin_id = session.get('user_id')

    stats = db_service.get_contact_stats(admin_id)
    result = db_service.get_contact_submissions(
        user_id=admin_id,
        page=1,
        per_page=10,
        status_filter='all',
        search=''
    )

    submissions = result.get('submissions', [])
    for sub in submissions:
        for field in ['created_at', 'updated_at']:
            val = sub.get(field)
            if val and hasattr(val, 'isoformat'):
                sub[field] = val.isoformat()

    return render_template(
        'leads.html',
        stats=stats,
        initial_leads=submissions,
        initial_total=result.get('total', 0),
        initial_page=result.get('page', 1),
        initial_total_pages=result.get('total_pages', 1)
    )


@leads_bp.route('/api/leads', methods=['GET'])
@admin_required
def api_get_leads():
    admin_id = session.get('user_id')
    status_filter = request.args.get('status', 'all')
    search = request.args.get('search', '').strip()
    page = int(request.args.get('page', 1))
    per_page = 10

    result = db_service.get_contact_submissions(
        user_id=admin_id,
        page=page,
        per_page=per_page,
        status_filter=status_filter,
        search=search
    )
    return jsonify({"success": True, **result})


@leads_bp.route('/api/leads/stats', methods=['GET'])
@admin_required
def api_get_leads_stats():
    admin_id = session.get('user_id')
    stats = db_service.get_contact_stats(admin_id)
    return jsonify({"success": True, "stats": stats})


@leads_bp.route('/api/leads/<submission_id>/read', methods=['POST'])
@admin_required
def api_mark_lead_read(submission_id):
    success = db_service.mark_contact_read(submission_id)
    if success:
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Failed to mark as read"}), 500


@leads_bp.route('/api/leads/<submission_id>/delete', methods=['POST'])
@admin_required
def api_delete_lead(submission_id):
    success = db_service.delete_contact_submission(submission_id)
    if success:
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Failed to delete"}), 500
