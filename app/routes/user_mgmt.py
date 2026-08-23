from flask import Blueprint, render_template, request, jsonify, session, make_response
from firebase_admin import auth as admin_auth
from app.firebase.firestore_service import FirestoreService
from app.services.email_service import EmailService
from app.utils.validators import is_valid_gmail
from app.core.security import admin_required

from app.core.logging import get_logger

logger = get_logger(__name__)

# We define the blueprint. The 'url_prefix' will be handled in the app factory.
user_bp = Blueprint('user_bp', __name__)
db_service = FirestoreService()
email_service = EmailService()

@user_bp.route('/manage-users')
@admin_required
def manage_users():
    """Renders the management dashboard for Admins."""
    response = make_response(render_template('users.html'))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    return response


@user_bp.route('/list', methods=['GET'])
@admin_required
def list_sub_users():
    """Fetches users and pending invitations for the logged-in Admin."""
    try:
        admin_id = session.get('user_id')
        users = db_service.get_my_sub_users(admin_id)

        safe_users = []
        for u in users:
            clean_u = {}
            for k, v in u.items():
                if hasattr(v, 'isoformat'):
                    clean_u[k] = v.isoformat()
                elif hasattr(v, 'path'):
                    clean_u[k] = str(v)
                else:
                    clean_u[k] = v
            safe_users.append(clean_u)

        invitations = db_service.get_invitations_by_admin(admin_id)

        response = jsonify({"success": True, "users": safe_users, "invitations": invitations})
        response.headers['Cache-Control'] = 'no-cache, no-store'
        return response
    except Exception as e:
        logger.exception("Error in /users/list")
        return jsonify({"success": False, "error": str(e)}), 500


@user_bp.route('/invite', methods=['POST'])
@admin_required
def invite_user():
    """Creates an invitation and returns the signup link."""
    data = request.json
    email = data.get('email', '').strip().lower()
    role = data.get('role', 'EDITOR').upper()
    admin_id = session.get('user_id')
    admin_name = session.get('user_name', 'Admin')

    if not email:
        return jsonify({"success": False, "error": "Email is required"}), 400

    # Only Gmail accounts can sign up, so inviting any other domain would just
    # produce a link the recipient can never complete.
    if not is_valid_gmail(email):
        return jsonify({"success": False, "error": "Only Gmail addresses (@gmail.com) can be invited."}), 400

    result = db_service.create_invitation(email, role, admin_id)
    if not result.get('success'):
        return jsonify(result), 400

    signup_url = f"{request.host_url}signup?invite={email}"

    # Attempt email delivery (may fail with test domain)
    email_sent = False
    try:
        role_article = "an" if role in ("ADMIN", "EDITOR") else "a"
        html_content = render_template('emails/invitation.html',
            app_name='Scriptly',
            inviter_name=admin_name,
            role=role.capitalize(),
            role_article=role_article,
            signup_url=signup_url
        )
        send_result = email_service.send_single(email, "You're invited to join Scriptly", html_content)
        email_sent = send_result.get('success', False)
    except Exception:
        logger.exception("Email send attempt failed")

    db_service.log_activity(
        user_id=admin_id,
        user_name=admin_name,
        type="user",
        action_text=f"Invited user '{email}'",
        target_type="user",
        target_name=email,
        metadata={"role": role, "email": email}
    )

    msg = "Invitation created! Share the link below with the user."
    if email_sent:
        msg = "Invitation email sent successfully!"

    return jsonify({
        "success": True,
        "message": msg,
        "signup_url": signup_url,
        "email_sent": email_sent
    })


@user_bp.route('/resend-invite', methods=['POST'])
@admin_required
def resend_invitation():
    """Resends the invitation email for a pending invitation."""
    data = request.json
    email = data.get('email', '').strip().lower()
    admin_name = session.get('user_name', 'Admin')

    if not email:
        return jsonify({"success": False, "error": "Email is required"}), 400

    invitation = db_service.get_pending_invitation_by_email(email)
    if not invitation:
        return jsonify({"success": False, "error": "No pending invitation found for this email"}), 404

    signup_url = f"{request.host_url}signup?invite={email}"
    role = invitation.get('role', 'EDITOR')
    role_article = "an" if role in ("ADMIN", "EDITOR") else "a"

    html_content = render_template('emails/invitation.html',
        app_name='Scriptly',
        inviter_name=admin_name,
        role=role.capitalize(),
        role_article=role_article,
        signup_url=signup_url
    )

    send_result = email_service.send_single(email, "You're invited to join Scriptly", html_content)

    if not send_result.get('success'):
        return jsonify({"success": False, "error": f"Failed to send email: {send_result.get('error')}"}), 500

    return jsonify({"success": True, "message": "Invitation resent successfully"})


@user_bp.route('/update-role', methods=['POST'])
@admin_required
def update_user_role():
    """Updates user role. Accessible at /users/update-role"""
    data = request.json
    user_id = data.get('userId')
    new_role = data.get('role', '').upper()

    try:
        db_service.db.collection("users").document(user_id).update({"role": new_role})
        db_service.log_activity(
            user_id=session.get('user_id'),
            user_name=session.get('user_name', 'Admin'),
            type="user",
            action_text=f"Changed role to {new_role}",
            target_type="user",
            target_id=user_id,
            target_name=data.get('username', user_id),
            metadata={"new_role": new_role}
        )
        return jsonify({"success": True, "message": "Role updated"})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@user_bp.route('/delete-user', methods=['POST'])
@admin_required
def delete_user():
    """Deletes a user from Firebase Auth and Firestore."""
    data = request.json
    user_id = data.get('userId')
    username = data.get('username', 'Unknown')
    admin_id = session.get('user_id')

    if not user_id:
        return jsonify({"success": False, "error": "User ID is required"}), 400

    if user_id == admin_id:
        return jsonify({"success": False, "error": "You cannot delete your own account"}), 400

    try:
        try:
            admin_auth.delete_user(user_id)
        except Exception:
            pass

        db_service.db.collection("users").document(user_id).delete()

        db_service.log_activity(
            user_id=admin_id,
            user_name=session.get('user_name', 'Admin'),
            type="user",
            action_text=f"Deleted user '{username}'",
            target_type="user",
            target_id=user_id,
            target_name=username,
            metadata={"deleted_user_id": user_id}
        )

        return jsonify({"success": True, "message": f"User '{username}' deleted successfully"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500