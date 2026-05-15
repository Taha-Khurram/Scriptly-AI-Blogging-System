"""
Settings Routes - App-level configuration endpoints
Handles general application settings like app name, tagline, etc.
"""

from flask import Blueprint, jsonify, request, session, render_template
from app.firebase.firestore_service import FirestoreService
from app import admin_required

settings_bp = Blueprint('settings', __name__)
db_service = FirestoreService()


@settings_bp.after_request
def add_cache_headers(response):
    if request.headers.get('X-Pjax') and response.status_code == 200:
        response.headers['Cache-Control'] = 'private, max-age=10, stale-while-revalidate=30'
    return response


@settings_bp.route('/app-settings')
@admin_required
def app_settings_page():
    """Render the App Settings admin page"""
    try:
        settings = db_service.get_app_settings()
        return render_template('app_settings.html', settings=settings)
    except Exception as e:
        print(f"Error loading app settings page: {e}")
        return render_template('app_settings.html', settings={
            'app_name': 'Scriptly',
            'tagline': '',
            'app_logo': '',
            'app_favicon': ''
        })


@settings_bp.route('/settings/general', methods=['GET'])
@admin_required
def get_general_settings():
    """
    GET /settings/general
    Returns app-level settings (app_name, tagline, etc.)
    """
    try:
        settings = db_service.get_app_settings()
        return jsonify({
            "success": True,
            "data": settings
        })
    except Exception as e:
        print(f"Error fetching general settings: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@settings_bp.route('/settings/general', methods=['PATCH'])
@admin_required
def update_general_settings():
    """
    PATCH /settings/general
    Updates app-level settings (partial update)
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({
                "success": False,
                "error": "No data provided"
            }), 400

        # Allowed fields for general settings
        allowed_fields = ['app_name', 'tagline', 'app_logo', 'app_favicon']

        # Filter only allowed fields
        update_data = {k: v for k, v in data.items() if k in allowed_fields}

        if not update_data:
            return jsonify({
                "success": False,
                "error": "No valid fields to update"
            }), 400

        # Validate app_name if provided
        if 'app_name' in update_data and not update_data['app_name'].strip():
            return jsonify({
                "success": False,
                "error": "App name cannot be empty"
            }), 400

        success = db_service.update_app_settings(update_data)

        if success:
            # Log activity
            db_service.log_activity(
                user_id=session.get('user_id'),
                user_name=session.get('user_name', 'Admin'),
                type="settings",
                action_text="updated application settings",
                blog_title=""
            )

            return jsonify({
                "success": True,
                "message": "Settings updated successfully"
            })
        else:
            return jsonify({
                "success": False,
                "error": "Failed to update settings"
            }), 500

    except Exception as e:
        print(f"Error updating general settings: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@settings_bp.route('/settings/general/public', methods=['GET'])
def get_public_settings():
    """
    GET /settings/general/public
    Returns public app settings (no auth required) - for displaying in pages
    """
    try:
        settings = db_service.get_app_settings()
        # Only return public-safe fields
        public_settings = {
            "app_name": settings.get("app_name", "Scriptly"),
            "tagline": settings.get("tagline", ""),
            "app_logo": settings.get("app_logo", ""),
            "app_favicon": settings.get("app_favicon", "")
        }
        return jsonify({
            "success": True,
            "data": public_settings
        })
    except Exception as e:
        return jsonify({
            "success": True,
            "data": {
                "app_name": "Scriptly",
                "tagline": ""
            }
        })
