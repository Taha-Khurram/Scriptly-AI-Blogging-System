from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, current_app
from firebase_admin import auth as admin_auth
from datetime import datetime, timezone
from app.firebase.firestore_service import FirestoreService
from app.utils.cache import cache

auth_bp = Blueprint('auth_bp', __name__)
db_service = FirestoreService()

@auth_bp.route('/login')
def login():
    if session.get('logged_in'):
        return redirect(url_for('blog.home'))
    return render_template('login.html', firebase_config=current_app.config['FIREBASE_CONFIG'])

@auth_bp.route('/signup')
def signup():
    if session.get('logged_in'):
        return redirect(url_for('blog.home'))
    return render_template('signup.html', firebase_config=current_app.config['FIREBASE_CONFIG'])

@auth_bp.route('/api/auth/verify', methods=['POST'])
def verify_token():
    data = request.json
    id_token = data.get('idToken')
    try:
        import json, base64
        from concurrent.futures import ThreadPoolExecutor

        # Decode JWT payload instantly (no network) to get uid/email early
        payload_part = id_token.split('.')[1]
        payload_part += '=' * (4 - len(payload_part) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_part))
        uid = payload['user_id']
        email = payload.get('email', '')

        # Check if user is cached (returning user fast path)
        cached_user = cache.get(f"user:{uid}")

        if cached_user:
            # Fast path: verify token + use cached user data (no Firestore)
            admin_auth.verify_id_token(id_token, check_revoked=False)
            user_record = cached_user
        else:
            # First login or cache expired: run all in parallel
            with ThreadPoolExecutor(max_workers=3) as executor:
                verify_future = executor.submit(lambda: admin_auth.verify_id_token(id_token, check_revoked=False))
                user_future = executor.submit(db_service.get_user_by_id, uid)
                invite_future = executor.submit(db_service.get_pending_invitation_by_email, email)

                decoded_token = verify_future.result()
                existing_user = user_future.result()
                invitation = invite_future.result()

            name = decoded_token.get('name') or email.split('@')[0]

            if existing_user:
                user_record = existing_user
                ThreadPoolExecutor(max_workers=1).submit(db_service.update_last_login, uid)
            else:
                user_info = {"uid": uid, "name": name, "email": email}
                if invitation:
                    user_info['role'] = invitation['role']
                    user_info['created_by'] = invitation['invited_by']
                user_record = db_service.save_user(user_info)

            if invitation:
                ThreadPoolExecutor(max_workers=1).submit(db_service.accept_invitation, invitation['id'])

            # Cache user for 10 minutes
            cache.set(f"user:{uid}", user_record, ttl=600)

        session.permanent = True
        session.update({
            'user_id': uid,
            'user_name': user_record.get('name', email.split('@')[0]),
            'user_role': user_record.get('role', 'ADMIN'),
            'profile_image': user_record.get('profile_image', ''),
            'logged_in': True,
            'last_activity': datetime.now(timezone.utc).isoformat()
        })

        return jsonify({"success": True, "redirect": url_for('blog.home')})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 401

@auth_bp.route('/api/admin/create-user', methods=['POST'])
def create_sub_user():
    """Route for Admins to manually create a user."""
    if session.get('user_role') != 'ADMIN':
        return jsonify({"error": "Unauthorized"}), 403

    data = request.json
    try:
        # 1. Create in Firebase Auth (Manual Email/Password)
        user_record = admin_auth.create_user(
            email=data['email'],
            password=data['password'],
            display_name=data['name']
        )

        # 2. Store in Firestore with 'USER' role linked to this Admin
        db_service.save_user({
            "uid": user_record.uid,
            "name": data['name'],
            "email": data['email'],
            "role": "USER",
            "created_by": session.get('user_id')
        })

        return jsonify({"success": True, "message": "User created successfully"})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@auth_bp.route('/forgot-password')
def forgot_password():
    if session.get('logged_in'):
        return redirect(url_for('blog.home'))
    return render_template('forgot_password.html', firebase_config=current_app.config['FIREBASE_CONFIG'])

@auth_bp.route('/api/auth/check-email', methods=['POST'])
def check_email():
    data = request.json
    email = data.get('email', '').strip()
    if not email:
        return jsonify({"exists": False, "error": "Email is required"}), 400
    try:
        admin_auth.get_user_by_email(email)
        return jsonify({"exists": True})
    except Exception as e:
        if 'USER_NOT_FOUND' in str(e) or 'not found' in str(e).lower():
            return jsonify({"exists": False, "error": "No account found with this email address"}), 404
        return jsonify({"exists": False, "error": "Something went wrong. Please try again"}), 500

@auth_bp.route('/profile')
def profile_page():
    if not session.get('logged_in'):
        return redirect(url_for('auth_bp.login'))
    user = db_service.get_user_by_id(session['user_id']) or {}
    return render_template('profile.html', user=user)

@auth_bp.route('/api/profile/update', methods=['POST'])
def update_profile():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized"}), 401
    data = request.json
    update = {}
    name = data.get('name', '').strip()
    if name:
        update['name'] = name
    profile_image = data.get('profile_image')
    if profile_image is not None:
        update['profile_image'] = profile_image.strip()
    if not update:
        return jsonify({"error": "No changes provided"}), 400
    result = db_service.update_user_profile(session['user_id'], update)
    if result:
        if 'name' in update:
            session['user_name'] = update['name']
        if 'profile_image' in update:
            session['profile_image'] = update['profile_image']
        return jsonify({"success": True})
    return jsonify({"error": "Update failed"}), 500

@auth_bp.route('/logout')
def logout():
    session.clear()
    session.modified = True 
    return redirect(url_for('auth_bp.login'))