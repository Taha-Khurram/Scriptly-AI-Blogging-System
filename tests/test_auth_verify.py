"""
Integration tests for the login/signup token-verification endpoint
(``POST /api/auth/verify``) and the admin/invite creation paths.

Firebase Auth and Firestore are fully mocked; these tests exercise the Flask
route logic — session creation, the Gmail-only signup gate, orphan cleanup,
and error handling.

Test matrix:
  POSITIVE  — valid Gmail signup, existing user login, Google/invite signup.
  NEGATIVE  — non-Gmail signup, invalid token, missing/malformed token.
  SECURITY  — verified-claim vs client-payload email, domain spoof at the API,
              admin-create and invite Gmail/password enforcement.
"""
import json

import pytest

import app.routes.auth as auth_module
from tests.conftest import make_fake_id_token


# Stand-ins for the firebase_admin.auth exception classes. The route
# distinguishes expired / revoked / invalid tokens to give each its own
# message, and `except` needs real classes to match against.
class _ExpiredIdTokenError(Exception):
    pass


class _RevokedIdTokenError(Exception):
    pass


class _InvalidIdTokenError(Exception):
    pass


class _EmailAlreadyExistsError(Exception):
    pass


@pytest.fixture
def fake_firebase(monkeypatch):
    """Patch the auth module's Firebase + Firestore + cache dependencies.

    Yields ``(admin_auth, db)`` mocks. Defaults simulate a brand-new user
    (no Firestore record, no cache, no invitation); individual tests override
    ``admin_auth.verify_id_token.return_value`` and the db mocks as needed.
    """
    # Force the "no cached user" full path and swallow cache writes.
    monkeypatch.setattr(auth_module.cache, "get", lambda key: None)
    monkeypatch.setattr(auth_module.cache, "set", lambda *a, **k: None)

    from unittest.mock import MagicMock

    admin_auth = MagicMock(name="admin_auth")
    # uid comes from the *verified* token now, not the client-supplied JWT
    # payload, so the mock must supply it the way the real SDK does.
    admin_auth.verify_id_token.return_value = {
        "uid": "uid1", "email": "user@gmail.com", "name": "User",
    }
    # The route catches these by type to give each a distinct message; a
    # MagicMock attribute is not a class and cannot appear in an `except`.
    admin_auth.ExpiredIdTokenError = _ExpiredIdTokenError
    admin_auth.RevokedIdTokenError = _RevokedIdTokenError
    admin_auth.InvalidIdTokenError = _InvalidIdTokenError
    admin_auth.EmailAlreadyExistsError = _EmailAlreadyExistsError
    monkeypatch.setattr(auth_module, "admin_auth", admin_auth)

    db = MagicMock(name="db_service")
    db.get_user_by_id.return_value = None
    db.get_pending_invitation_by_email.return_value = None
    db.save_user.side_effect = lambda info: {**info, "role": info.get("role", "USER")}
    db.update_last_login.return_value = None
    db.accept_invitation.return_value = None
    monkeypatch.setattr(auth_module, "db_service", db)

    return admin_auth, db


def _verify(client, uid="uid1", email="user@gmail.com"):
    return client.post(
        "/api/auth/verify",
        data=json.dumps({"idToken": make_fake_id_token(uid, email)}),
        content_type="application/json",
    )


# =========================================================================== #
# POSITIVE
# =========================================================================== #
def test_signup_with_valid_gmail_succeeds(client, fake_firebase):
    admin_auth, db = fake_firebase
    admin_auth.verify_id_token.return_value = {"uid": "uid-new", "email": "newuser@gmail.com", "name": "New User"}

    resp = _verify(client, "uid-new", "newuser@gmail.com")
    body = resp.get_json()

    assert resp.status_code == 200
    assert body["success"] is True
    assert "redirect" in body
    db.save_user.assert_called_once()
    admin_auth.delete_user.assert_not_called()

    with client.session_transaction() as sess:
        assert sess["logged_in"] is True
        assert sess["user_id"] == "uid-new"


def test_existing_non_gmail_user_can_still_log_in(client, fake_firebase):
    """The Gmail rule gates *signup*, not login. Existing accounts must work."""
    admin_auth, db = fake_firebase
    db.get_user_by_id.return_value = {
        "name": "Legacy", "role": "ADMIN", "email": "legacy@company.com", "profile_image": "",
    }
    admin_auth.verify_id_token.return_value = {"uid": "uid-legacy", "email": "legacy@company.com"}

    resp = _verify(client, "uid-legacy", "legacy@company.com")

    assert resp.status_code == 200
    assert resp.get_json()["success"] is True
    db.save_user.assert_not_called()          # no new account created
    admin_auth.delete_user.assert_not_called()  # existing user not touched


def test_invited_gmail_user_gets_invitation_role(client, fake_firebase):
    admin_auth, db = fake_firebase
    admin_auth.verify_id_token.return_value = {"uid": "uid-inv", "email": "invitee@gmail.com", "name": "Invitee"}
    db.get_pending_invitation_by_email.return_value = {
        "id": "inv1", "role": "EDITOR", "invited_by": "admin-1",
    }

    resp = _verify(client, "uid-inv", "invitee@gmail.com")

    assert resp.status_code == 200
    saved = db.save_user.call_args[0][0]
    assert saved["role"] == "EDITOR"
    assert saved["created_by"] == "admin-1"


# =========================================================================== #
# NEGATIVE
# =========================================================================== #
def test_signup_with_non_gmail_is_rejected_and_orphan_deleted(client, fake_firebase):
    admin_auth, db = fake_firebase
    admin_auth.verify_id_token.return_value = {"uid": "uid-bad", "email": "attacker@yahoo.com"}

    resp = _verify(client, "uid-bad", "attacker@yahoo.com")
    body = resp.get_json()

    assert resp.status_code == 403
    assert body["success"] is False
    assert body["error"] == auth_module.GMAIL_ONLY_ERROR
    db.save_user.assert_not_called()
    # The just-created Firebase Auth account is rolled back.
    admin_auth.delete_user.assert_called_once_with("uid-bad")

    with client.session_transaction() as sess:
        assert "logged_in" not in sess


def test_invalid_token_returns_401(client, fake_firebase):
    admin_auth, db = fake_firebase
    admin_auth.verify_id_token.side_effect = _InvalidIdTokenError("bad token")

    resp = _verify(client, "uid-x", "user@gmail.com")

    assert resp.status_code == 401
    assert resp.get_json()["success"] is False
    db.save_user.assert_not_called()


def test_missing_id_token_returns_400(client, fake_firebase):
    """A body with no token is a malformed request, not a failed sign-in.

    400 rather than 401 is deliberate: 401 tells the frontend "your credentials
    were rejected, show the expired-session prompt", which is the wrong thing
    to show when the client simply never sent a token.
    """
    resp = client.post("/api/auth/verify", data=json.dumps({}), content_type="application/json")
    body = resp.get_json()

    assert resp.status_code == 400
    assert body["success"] is False
    assert body["code"] == "validation_error"
    # Every error response carries the id needed to find it in the logs.
    assert body["request_id"]


def test_malformed_token_returns_401(client, fake_firebase):
    """A token that is not a valid JWT must be rejected by verification.

    The mock raises the way the real SDK does. Asserting on the mock being
    *called* is the point: a syntactically broken token must still reach
    signature verification rather than being waved through by a parse
    shortcut.
    """
    admin_auth, db = fake_firebase
    admin_auth.verify_id_token.side_effect = _InvalidIdTokenError("not a JWT")

    resp = client.post(
        "/api/auth/verify",
        data=json.dumps({"idToken": "not-a-real-jwt"}),
        content_type="application/json",
    )

    assert resp.status_code == 401
    assert resp.get_json()["success"] is False
    admin_auth.verify_id_token.assert_called_once()
    db.save_user.assert_not_called()


def test_expired_and_revoked_tokens_get_distinct_messages(client, fake_firebase):
    """Expired and revoked are different situations for the user."""
    admin_auth, db = fake_firebase

    admin_auth.verify_id_token.side_effect = _ExpiredIdTokenError("expired", None)
    expired = _verify(client)

    admin_auth.verify_id_token.side_effect = _RevokedIdTokenError("revoked")
    revoked = _verify(client)

    assert expired.status_code == revoked.status_code == 401
    assert expired.get_json()["error"] != revoked.get_json()["error"]


def test_error_response_never_leaks_internals(client, fake_firebase):
    """An unexpected upstream failure must not be echoed to the caller.

    The original code returned `str(e)`, which put Firebase error codes and
    gRPC transport details in front of an unauthenticated client.
    """
    admin_auth, db = fake_firebase
    secret = "gRPC transport failed: /var/secrets/serviceAccount.json unreadable"
    admin_auth.verify_id_token.side_effect = RuntimeError(secret)

    resp = _verify(client)
    body = resp.get_json()

    assert resp.status_code == 401
    assert "gRPC" not in json.dumps(body)
    assert "serviceAccount" not in json.dumps(body)
    assert body["request_id"]


def test_session_role_defaults_to_user_not_admin(client, fake_firebase):
    """A record with no role must not be treated as an administrator.

    The original session default was ADMIN, so a partially-written user
    document granted full administrative access.
    """
    admin_auth, db = fake_firebase
    db.get_user_by_id.return_value = {"name": "No Role", "email": "user@gmail.com"}
    admin_auth.verify_id_token.return_value = {"uid": "uid-norole", "email": "user@gmail.com"}

    resp = _verify(client, "uid-norole")

    assert resp.status_code == 200
    with client.session_transaction() as sess:
        assert sess["user_role"] == "USER"


def test_check_email_does_not_confirm_account_existence(client, fake_firebase):
    """The endpoint must not work as an account-enumeration oracle."""
    admin_auth, db = fake_firebase

    resp = client.post(
        "/api/auth/check-email",
        data=json.dumps({"email": "somebody@gmail.com"}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    # It never consults Firebase, so it cannot reveal what Firebase knows.
    admin_auth.get_user_by_email.assert_not_called()


# =========================================================================== #
# SECURITY
# =========================================================================== #
def test_gmail_check_uses_verified_claim_not_client_payload(client, fake_firebase):
    """A client can put anything in the JWT payload; the gate must use the
    cryptographically verified email. Payload says gmail, verified says evil."""
    admin_auth, db = fake_firebase
    admin_auth.verify_id_token.return_value = {"uid": "uid-spoof", "email": "attacker@evil.com"}

    # Client-supplied payload claims a Gmail address...
    resp = _verify(client, "uid-spoof", "trusted@gmail.com")

    # ...but the verified token wins, so signup is rejected.
    assert resp.status_code == 403
    db.save_user.assert_not_called()
    admin_auth.delete_user.assert_called_once_with("uid-spoof")


@pytest.mark.parametrize("spoof_email", [
    "attacker@gmail.com.evil.com",
    "attacker@sub.gmail.com",
    "attacker@gmail.com@evil.com",
])
def test_domain_spoof_rejected_at_api(client, fake_firebase, spoof_email):
    admin_auth, db = fake_firebase
    admin_auth.verify_id_token.return_value = {"uid": "uid-spoof2", "email": spoof_email}

    resp = _verify(client, "uid-spoof2", spoof_email)

    assert resp.status_code == 403
    db.save_user.assert_not_called()


# --------------------------------------------------------------------------- #
# Admin-created users and invitations enforce the same rules
# --------------------------------------------------------------------------- #
def _login_as_admin(client):
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user_role"] = "ADMIN"
        sess["user_id"] = "admin-1"
        sess["user_name"] = "Admin"


def test_admin_create_user_rejects_non_gmail(client, fake_firebase):
    admin_auth, db = fake_firebase
    _login_as_admin(client)

    resp = client.post(
        "/api/admin/create-user",
        data=json.dumps({"email": "someone@yahoo.com", "password": "Password1!", "name": "X"}),
        content_type="application/json",
    )

    assert resp.status_code == 400
    assert resp.get_json()["error"] == auth_module.GMAIL_ONLY_ERROR
    admin_auth.create_user.assert_not_called()


def test_admin_create_user_rejects_weak_password(client, fake_firebase):
    admin_auth, db = fake_firebase
    _login_as_admin(client)

    resp = client.post(
        "/api/admin/create-user",
        data=json.dumps({"email": "user@gmail.com", "password": "weak", "name": "X"}),
        content_type="application/json",
    )

    assert resp.status_code == 400
    assert "Weak password" in resp.get_json()["error"]
    admin_auth.create_user.assert_not_called()


def test_admin_create_user_accepts_valid_gmail_and_strong_password(client, fake_firebase):
    admin_auth, db = fake_firebase
    _login_as_admin(client)
    admin_auth.create_user.return_value.uid = "created-uid"

    resp = client.post(
        "/api/admin/create-user",
        data=json.dumps({"email": "user@gmail.com", "password": "Password1!", "name": "X"}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    assert resp.get_json()["success"] is True
    admin_auth.create_user.assert_called_once()


def test_admin_create_user_requires_admin_role(client, fake_firebase):
    # No admin session set.
    resp = client.post(
        "/api/admin/create-user",
        data=json.dumps({"email": "user@gmail.com", "password": "Password1!", "name": "X"}),
        content_type="application/json",
    )
    assert resp.status_code == 403


def test_invite_rejects_non_gmail(client, fake_firebase):
    admin_auth, db = fake_firebase
    _login_as_admin(client)

    resp = client.post(
        "/users/invite",
        data=json.dumps({"email": "teammate@company.com", "role": "EDITOR"}),
        content_type="application/json",
    )

    # Rejected before any Firestore invitation is created.
    assert resp.status_code == 400
    assert "Gmail" in resp.get_json()["error"]
