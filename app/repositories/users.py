"""User records, team membership, and pending invitations.

One slice of what used to be a single 3,300-line ``FirestoreService`` class.
That class was imported by every blueprint, so any data-layer change risked the
whole application, and its size made it effectively untestable -- there was no
way to exercise one domain without loading all of them.

This is a mixin, not a standalone repository object, because the methods call
each other across domain lines (creating a draft updates a category count;
listing published posts backfills slugs). Composing mixins keeps those calls
working with no rewiring, so the split is a pure move: same method set, same
behaviour, reviewable units. ``FirestoreService`` composes every mixin, so all
existing call sites are unchanged.

``self.db`` (the Firestore client) and the collection names come from
``FirestoreService.__init__``.
"""
from app.utils.date_utils import utcnow
from app.utils.retry import retry_on_unavailable
from datetime import datetime
from firebase_admin import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from app.core.logging import get_logger

logger = get_logger(__name__)


class UserRepository:
    """User records, team membership, and pending invitations."""

    def save_user(self, user_data):
        try:
            user_id = user_data.get('uid')
            if not user_id:
                return None
            user_ref = self.db.collection(self.user_collection).document(user_id)
            existing_user = user_ref.get()

            if not existing_user.exists:
                user_data["role"] = user_data.get("role", "ADMIN")
                user_data["profile_image"] = user_data.get("profile_image", "")
                user_data["created_at"] = firestore.SERVER_TIMESTAMP
                user_data["created_by"] = user_data.get("created_by", None)
                user_data["last_login"] = firestore.SERVER_TIMESTAMP
                user_ref.set(user_data)
                return user_data
            else:
                user_ref.update({"last_login": firestore.SERVER_TIMESTAMP})
                return existing_user.to_dict()
        except Exception:
            logger.exception("Error saving user")
            return None

    def update_last_login(self, user_id):
        """Update last_login timestamp (fire-and-forget)."""
        try:
            self.db.collection(self.user_collection).document(user_id).update({
                "last_login": firestore.SERVER_TIMESTAMP
            })
        except Exception:
            pass

    @retry_on_unavailable
    def get_user_by_id(self, user_id):
        """Gets a user document by their ID."""
        try:
            if not user_id:
                return None
            doc = self.db.collection(self.user_collection).document(user_id).get()
            if doc.exists:
                return doc.to_dict()
            return None
        except Exception:
            logger.exception("Error getting user")
            return None

    def update_user_profile(self, user_id, profile_data):
        try:
            if not user_id:
                return None
            user_ref = self.db.collection(self.user_collection).document(user_id)
            user_ref.update(profile_data)
            return self.get_user_by_id(user_id)
        except Exception:
            logger.exception("Error updating user profile")
            return None

    def get_my_sub_users(self, admin_id):
        try:
            docs = self.db.collection(self.user_collection)\
                .where(filter=FieldFilter('created_by', '==', admin_id)).stream()
            return [{**doc.to_dict(), 'uid': doc.id} for doc in docs]
        except Exception:
            logger.exception("Error fetching sub-users")
            return []

    def get_site_owner_for_user(self, user_id):
        """
        Gets the site owner for a user.
        - If user is an ADMIN or has no created_by, they are their own site owner
        - If user was created by an admin, that admin is the site owner
        """
        try:
            user = self.get_user_by_id(user_id)
            if not user:
                return user_id  # Fallback to self

            # If user is admin or wasn't created by anyone, they own their own site
            if user.get('role') == 'ADMIN' or not user.get('created_by'):
                return user_id

            # Return the admin who created this user
            return user.get('created_by')
        except Exception:
            logger.exception("Error getting site owner")
            return user_id  # Fallback to self

    def create_invitation(self, email, role, invited_by):
        email = email.lower().strip()
        try:
            existing_users = self.db.collection(self.user_collection)\
                .where(filter=FieldFilter('email', '==', email)).stream()
            if any(True for _ in existing_users):
                return {"success": False, "error": "A user with this email already exists"}

            existing_invites = self.db.collection('invitations')\
                .where(filter=FieldFilter('email', '==', email))\
                .where(filter=FieldFilter('invited_by', '==', invited_by))\
                .where(filter=FieldFilter('status', '==', 'pending')).stream()
            for doc in existing_invites:
                data = doc.to_dict()
                data['id'] = doc.id
                return {"success": True, "invitation": data, "already_existed": True}

            inv_data = {
                "email": email,
                "role": role.upper(),
                "invited_by": invited_by,
                "invited_at": firestore.SERVER_TIMESTAMP,
                "status": "pending"
            }
            doc_ref = self.db.collection('invitations').add(inv_data)
            inv_data['id'] = doc_ref[1].id
            return {"success": True, "invitation": inv_data}
        except Exception as e:
            logger.exception("Error creating invitation")
            return {"success": False, "error": str(e)}

    def get_pending_invitation_by_email(self, email):
        email = email.lower().strip()
        try:
            docs = self.db.collection('invitations')\
                .where(filter=FieldFilter('email', '==', email))\
                .where(filter=FieldFilter('status', '==', 'pending')).stream()
            invitations = []
            for doc in docs:
                data = doc.to_dict()
                data['id'] = doc.id
                invitations.append(data)
            if not invitations:
                return None
            invitations.sort(key=lambda x: x.get('invited_at') or datetime.min, reverse=True)
            return invitations[0]
        except Exception:
            logger.exception("Error checking invitation")
            return None

    def accept_invitation(self, invitation_id):
        try:
            self.db.collection('invitations').document(invitation_id).update({
                "status": "accepted",
                "accepted_at": utcnow()
            })
            return True
        except Exception:
            logger.exception("Error accepting invitation")
            return False

    def get_invitations_by_admin(self, admin_id):
        try:
            docs = self.db.collection('invitations')\
                .where(filter=FieldFilter('invited_by', '==', admin_id)).stream()
            invitations = []
            for doc in docs:
                data = doc.to_dict()
                data['id'] = doc.id
                if data.get('invited_at') and hasattr(data['invited_at'], 'isoformat'):
                    data['invited_at'] = data['invited_at'].isoformat()
                if data.get('accepted_at') and hasattr(data['accepted_at'], 'isoformat'):
                    data['accepted_at'] = data['accepted_at'].isoformat()
                invitations.append(data)
            invitations.sort(key=lambda x: x.get('invited_at') or '', reverse=True)
            return invitations
        except Exception:
            logger.exception("Error fetching invitations")
            return []
