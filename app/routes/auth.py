"""Authentication, session establishment and profile routes.

Sign-in happens in the browser against Firebase Auth; this blueprint's job is
to verify the resulting ID token server-side and establish a Flask session from
it. Four problems in the previous implementation are addressed here.

**Thread leaks.** Two fire-and-forget calls used
``ThreadPoolExecutor(max_workers=1).submit(...)`` -- creating a *new* executor
per request and never shutting it down. Under load that leaks a thread per
login. Deferred work now goes to one module-level pool with a bounded size.

**Raw exception strings returned to unauthenticated callers.** ``except
Exception as e: return jsonify({'error': str(e)}), 401`` leaked Firebase error
codes and gRPC internals. Failures are now logged server-side with the request
id and answered generically.

**An unthrottled account-enumeration oracle.** ``/api/auth/check-email``
returned a definitive yes/no for any address. It is now rate-limited and
answers uniformly.

**Unverified claims trusted before verification.** The JWT payload was
base64-decoded to read ``uid``/``email`` *before* the signature was checked, as
a latency optimisation. That is sound only if nothing security-relevant is
decided from those values -- the original code went on to use the unverified
uid as the Firestore lookup key and the cache key. Now the unverified payload
is used solely to probe the session cache, and every value written to the
session comes from the verified token.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from firebase_admin import auth as admin_auth
from flask import Blueprint, current_app, jsonify, redirect, render_template, request, session, url_for

from app.core.errors import AuthenticationError, ValidationError
from app.core.extensions import limiter
from app.core.security import api_login_required, login_required
from app.core.sessions import destroy_session, rotate_session
from app.firebase.firestore_service import FirestoreService
from app.utils.cache import cache
from app.utils.validators import is_valid_gmail, validate_password

logger = logging.getLogger(__name__)

auth_bp = Blueprint('auth_bp', __name__)
db_service = FirestoreService()

# Message shown when a non-Gmail address attempts to create an account.
GMAIL_ONLY_ERROR = 'Only Gmail addresses (@gmail.com) are allowed to sign up.'

# One bounded pool for deferred, non-blocking writes (last-login stamp,
# invitation acceptance). These must not delay the login response, and their
# failure must not fail the login -- but they also must not spawn an unbounded
# number of threads. Daemon threads so a shutdown is never held up by one.
_deferred = ThreadPoolExecutor(max_workers=4, thread_name_prefix='auth-deferred')

# How long a verified user record stays cached. Long enough to skip a Firestore
# read on the common returning-user path, short enough that a role change takes
# effect without an explicit sign-out.
USER_CACHE_TTL = 300


def _run_deferred(fn, *args, label='deferred'):
    """Submit background work, logging rather than raising on failure."""
    def wrapped():
        try:
            fn(*args)
        except Exception:
            logger.warning('Deferred %s failed', label, exc_info=True)

    try:
        _deferred.submit(wrapped)
    except RuntimeError:
        # Pool is shutting down: do it inline rather than dropping it.
        wrapped()


def _peek_token_uid(id_token):
    """Read the ``uid`` claim without verifying the signature.

    Used *only* to look for a cached session for this uid, which turns the
    common returning-user path into one signature check instead of a signature
    check plus three Firestore reads. Nothing is trusted from this: the value
    is not written to the session, and a cache hit is still gated on the
    signature verification that follows.
    """
    try:
        payload_segment = id_token.split('.')[1]
        payload_segment += '=' * (-len(payload_segment) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_segment))
        return payload.get('user_id') or payload.get('sub')
    except (AttributeError, IndexError, ValueError, TypeError, binascii.Error):
        return None


def _establish_session(uid, email, user_record):
    """Write the verified identity into a freshly-issued session."""
    session.clear()          # no state carried over from a previous identity
    session.permanent = True
    # New session id for the authenticated session. Session fixation works by
    # getting a victim to browse under an id the attacker already holds and
    # waiting for them to sign in; rotating here makes that id worthless at the
    # exact moment it would otherwise become valuable. clear() alone does not
    # do this -- it empties the contents but keeps the identifier.
    rotate_session()
    session.update({
        'user_id': uid,
        'user_name': user_record.get('name') or email.split('@')[0],
        'user_email': email,
        # Default to USER, not ADMIN: a record missing its role must fail
        # closed. The original defaulted to ADMIN, so a partially-written user
        # document granted full administrative access.
        'user_role': user_record.get('role', 'USER'),
        'profile_image': user_record.get('profile_image', ''),
        'logged_in': True,
        'last_activity': datetime.now(timezone.utc).isoformat(),
    })


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@auth_bp.route('/login')
def login():
    if session.get('logged_in'):
        return redirect(url_for('blog.home'))
    return render_template(
        'login.html', firebase_config=current_app.config['FIREBASE_CONFIG']
    )


@auth_bp.route('/signup')
def signup():
    if session.get('logged_in'):
        return redirect(url_for('blog.home'))
    return render_template(
        'signup.html', firebase_config=current_app.config['FIREBASE_CONFIG']
    )


@auth_bp.route('/forgot-password')
def forgot_password():
    if session.get('logged_in'):
        return redirect(url_for('blog.home'))
    return render_template(
        'forgot_password.html',
        firebase_config=current_app.config['FIREBASE_CONFIG'],
    )


@auth_bp.route('/logout')
def logout():
    user_id = session.get('user_id')
    # Deletes the server-side record, not just the browser's copy. With
    # client-side sessions "logging out" only asked the browser to forget a
    # cookie that stayed valid for its full lifetime if it had been captured.
    destroy_session()
    if user_id:
        # Drop the cached record so a role change made while signed in is
        # picked up on the next sign-in rather than served from cache.
        cache.delete(f'user:{user_id}')
        logger.info('User signed out', extra={'user_id': user_id})
    return redirect(url_for('auth_bp.login'))


# ---------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------

@auth_bp.route('/api/auth/verify', methods=['POST'])
@limiter.limit(lambda: current_app.config.get('RATELIMIT_AUTH', '20 per minute'))
def verify_token():
    """Exchange a Firebase ID token for a Flask session.

    Rate-limited per IP: without a limit this endpoint is an unauthenticated
    door to Firebase token verification and, on the first-login path, to three
    Firestore reads per call.
    """
    payload = request.get_json(silent=True) or {}
    id_token = payload.get('idToken')

    if not id_token or not isinstance(id_token, str):
        raise ValidationError('An authentication token is required.')

    # Fast path: a cached record for this uid means we only need to prove the
    # token is genuine, not re-read the user from Firestore.
    unverified_uid = _peek_token_uid(id_token)
    cached_user = cache.get(f'user:{unverified_uid}') if unverified_uid else None

    try:
        decoded = admin_auth.verify_id_token(id_token, check_revoked=False)
    except admin_auth.ExpiredIdTokenError:
        logger.info('Rejected expired ID token')
        raise AuthenticationError('Your sign-in has expired. Please try again.')
    except admin_auth.RevokedIdTokenError:
        logger.warning('Rejected revoked ID token')
        raise AuthenticationError('This session was revoked. Please sign in again.')
    except (admin_auth.InvalidIdTokenError, ValueError) as exc:
        logger.warning('Rejected invalid ID token: %s', exc)
        raise AuthenticationError('Sign-in could not be verified. Please try again.')
    except Exception:
        # Firebase unreachable, clock skew, certificate fetch failure. Logged
        # in full; the caller learns only that it failed.
        logger.exception('Token verification failed unexpectedly')
        raise AuthenticationError('Sign-in is temporarily unavailable. Please try again.')

    # Everything below uses only cryptographically verified claims.
    uid = decoded['uid']
    email = decoded.get('email') or ''

    if cached_user is not None and unverified_uid == uid:
        user_record = cached_user
    else:
        user_record = _load_or_create_user(uid, email, decoded)
        if user_record is None:
            return jsonify({'success': False, 'error': GMAIL_ONLY_ERROR}), 403
        cache.set(f'user:{uid}', user_record, ttl=USER_CACHE_TTL)

    _establish_session(uid, email, user_record)
    logger.info(
        'User signed in',
        extra={'user_id': uid, 'role': session.get('user_role'),
               'cache_hit': cached_user is not None},
    )
    return jsonify({'success': True, 'redirect': url_for('blog.home')})


def _load_or_create_user(uid, email, decoded):
    """Fetch the user record, creating it on first sign-in.

    Returns ``None`` when a *new* account fails the Gmail-only rule, having
    already deleted the Firebase Auth account the client just created so no
    orphaned, unusable login is left behind. Existing users are exempt from the
    rule so a policy change can never lock out an account that already works.
    """
    existing_user = db_service.get_user_by_id(uid)
    invitation = db_service.get_pending_invitation_by_email(email) if email else None

    if existing_user:
        _run_deferred(db_service.update_last_login, uid, label='last-login')
        if invitation:
            _run_deferred(
                db_service.accept_invitation, invitation['id'], label='invitation'
            )
        return existing_user

    # --- New account: enforce the signup policy -------------------------
    if not is_valid_gmail(email):
        logger.warning(
            'Rejected signup from disallowed domain',
            extra={'user_id': uid, 'email_domain': email.rpartition("@")[2]},
        )
        try:
            admin_auth.delete_user(uid)
        except Exception:
            logger.warning('Could not roll back rejected account %s', uid, exc_info=True)
        return None

    user_info = {
        'uid': uid,
        'name': decoded.get('name') or email.split('@')[0],
        'email': email,
    }
    if invitation:
        user_info['role'] = invitation['role']
        user_info['created_by'] = invitation['invited_by']

    user_record = db_service.save_user(user_info)
    logger.info(
        'Created user on first sign-in',
        extra={'user_id': uid, 'invited': bool(invitation)},
    )

    if invitation:
        _run_deferred(
            db_service.accept_invitation, invitation['id'], label='invitation'
        )
    return user_record


@auth_bp.route('/api/auth/check-email', methods=['POST'])
@limiter.limit('5 per minute; 30 per hour')
def check_email():
    """Report whether an address can be used for a password reset.

    Deliberately uniform. The previous version returned ``{"exists": true}`` or
    a 404 for any address with no throttle, which confirms account existence
    for an arbitrary list of emails at scale -- useful for credential stuffing
    and for targeting phishing. It now validates format only and always claims
    success; the reset email itself is what silently does or does not arrive.

    Kept as an endpoint rather than removed because the frontend calls it to
    catch typos before submitting.
    """
    payload = request.get_json(silent=True) or {}
    email = (payload.get('email') or '').strip()

    if not email:
        raise ValidationError('An email address is required.')

    if not is_valid_gmail(email):
        # Not an enumeration signal: this is a syntactic rule the client
        # already applies and it is identical for every address, existing or
        # not.
        return jsonify({'exists': False, 'error': GMAIL_ONLY_ERROR}), 400

    logger.info(
        'Password-reset lookup',
        extra={'email_domain': email.rpartition('@')[2]},
    )
    return jsonify({
        'exists': True,
        'message': 'If an account exists for that address, a reset link is on its way.',
    })


# ---------------------------------------------------------------------------
# Admin: manual user creation
# ---------------------------------------------------------------------------

@auth_bp.route('/api/admin/create-user', methods=['POST'])
@limiter.limit('10 per minute')
def create_sub_user():
    """Create a USER-role account under the calling admin."""
    from app.core.security import current_user

    user = current_user()
    if not user.is_admin:
        logger.warning(
            'Non-admin attempted user creation', extra={'user_id': user.id}
        )
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.get_json(silent=True) or {}
    email = (data.get('email') or '').strip()
    password = data.get('password') or ''
    name = (data.get('name') or '').strip()

    if not name:
        raise ValidationError('A name is required.')
    if not is_valid_gmail(email):
        raise ValidationError(GMAIL_ONLY_ERROR)

    # Same rules the client shows, enforced here because the client is not a
    # trust boundary.
    password_errors = validate_password(password)
    if password_errors:
        raise ValidationError(
            'Weak password: ' + ', '.join(password_errors),
            details={'requirements': password_errors},
        )

    try:
        auth_record = admin_auth.create_user(
            email=email, password=password, display_name=name
        )
    except admin_auth.EmailAlreadyExistsError:
        raise ValidationError('An account already exists for that email address.')
    except ValueError as exc:
        raise ValidationError(f'Could not create the account: {exc}')
    except Exception:
        logger.exception('Firebase user creation failed')
        raise AuthenticationError('Could not create the account. Please try again.')

    try:
        db_service.save_user({
            'uid': auth_record.uid,
            'name': name,
            'email': email,
            'role': 'USER',
            'created_by': user.id,
        })
    except Exception:
        # The Auth account exists but its Firestore record does not, which
        # would leave a login that resolves to no profile. Roll it back so the
        # admin can simply retry.
        logger.exception('Rolling back auth account after Firestore failure')
        try:
            admin_auth.delete_user(auth_record.uid)
        except Exception:
            logger.error('Orphaned auth account left behind: %s', auth_record.uid)
        raise AuthenticationError('Could not create the account. Please try again.')

    logger.info(
        'Admin created user',
        extra={'user_id': auth_record.uid, 'created_by': user.id},
    )
    return jsonify({'success': True, 'message': 'User created successfully'})


# ---------------------------------------------------------------------------
# Session lifetime
# ---------------------------------------------------------------------------

@auth_bp.route('/api/session/heartbeat', methods=['POST'])
@api_login_required
@limiter.limit('60 per minute')
def session_heartbeat():
    """Extend the session because the user is genuinely active.

    The inactivity timeout is ten minutes, and the sliding expiry only moves
    when a request arrives. Plenty of real activity issues no request at all:
    typing a post into the rich-text editor happens inside a TinyMCE iframe and
    reaches neither the server nor the parent document's event listeners. So
    without this endpoint a ten-minute window would sign an author out mid-post
    -- and nothing in the editor autosaves, so the draft would go with them.

    The frontend calls this on real interaction, throttled to roughly a third
    of the window, so an active user costs about three requests per ten minutes
    and an idle one costs none. That is what keeps "inactivity" meaning idle
    rather than "has not clicked a link recently".

    Deliberately *not* on a bare timer in the browser: a poll that ran
    regardless of interaction would keep every open tab alive forever and
    there would be no inactivity timeout at all.

    Reaching this endpoint at all means the session was still valid --
    ``enforce_session_timeout`` runs first and answers an expired one with
    ``session_expired``, which the frontend fetch wrapper turns into a
    redirect. Returning the full window is therefore accurate: the touch below
    has just reset it.
    """
    timeout = int(current_app.config['PERMANENT_SESSION_LIFETIME'].total_seconds())

    # Force the write. The interface throttles touches to SESSION_TOUCH_SECONDS,
    # and the whole point of this call is to move the expiry, so it must not be
    # the request that gets throttled out.
    session.modified = True

    return jsonify({
        'success': True,
        'expires_in': timeout,
        'warn_in': max(0, timeout - int(
            current_app.config.get('SESSION_WARNING_SECONDS', 60)
        )),
    })


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

@auth_bp.route('/profile')
@login_required
def profile_page():
    user = db_service.get_user_by_id(session['user_id']) or {}
    return render_template('profile.html', user=user)


@auth_bp.route('/api/profile/update', methods=['POST'])
@login_required
@limiter.limit('30 per minute')
def update_profile():
    """Update the signed-in user's own display name and avatar."""
    data = request.get_json(silent=True) or {}
    update = {}

    name = (data.get('name') or '').strip()
    if name:
        if len(name) > 100:
            raise ValidationError('Name must be 100 characters or fewer.')
        update['name'] = name

    profile_image = data.get('profile_image')
    if profile_image is not None:
        image_url = str(profile_image).strip()
        if image_url and not _is_safe_image_url(image_url):
            raise ValidationError('Profile image must be an https:// URL.')
        update['profile_image'] = image_url

    if not update:
        raise ValidationError('No changes provided.')

    user_id = session['user_id']
    if not db_service.update_user_profile(user_id, update):
        logger.error('Profile update failed', extra={'user_id': user_id})
        raise AuthenticationError('Could not save your changes. Please try again.')

    # Keep session and cache consistent with what was written, or the header
    # keeps showing the old name until the next sign-in.
    if 'name' in update:
        session['user_name'] = update['name']
    if 'profile_image' in update:
        session['profile_image'] = update['profile_image']
    cache.delete(f'user:{user_id}')

    return jsonify({'success': True})


def _is_safe_image_url(url):
    """Allow only https URLs and same-origin relative paths.

    The value is rendered into an ``<img src>`` for other users to see (in the
    team list and on comments), so a ``javascript:`` or ``data:`` URL here is a
    stored injection vector. Plain http is refused as well, because a mixed
    -content image on an https page is blocked and simply appears broken.
    """
    lowered = url.lower()
    if lowered.startswith('/static/') or lowered.startswith('/'):
        return '//' not in url[1:] and ':' not in url
    return lowered.startswith('https://')
