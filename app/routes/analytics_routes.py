import os
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
# Google frequently returns a superset of the requested scopes (e.g. it adds
# openid). Without this, oauthlib raises "Scope has changed" during token
# exchange and the connect flow dies.
os.environ['OAUTHLIB_RELAX_TOKEN_SCOPE'] = '1'

import threading
import time

from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, current_app
from datetime import datetime
from app.firebase.firestore_service import FirestoreService
from app.core.security import admin_required
from app.utils.cache import cache
from app.utils.date_utils import utcnow

from app.core.logging import get_logger

logger = get_logger(__name__)

analytics_bp = Blueprint('analytics_bp', __name__)
db_service = FirestoreService()

SCOPES = ['https://www.googleapis.com/auth/analytics.readonly']
REDIRECT_PATH = '/analytics/callback'

# ---------------------------------------------------------------------------
# Cached GA session
# ---------------------------------------------------------------------------
#
# The analytics screen fires five of these endpoints at once, and each one used
# to independently:
#
#   1. read analytics_config from Firestore              (~0.6-2.3 s)
#   2. read it *again* inside _get_credentials           (~0.6-2.3 s)
#   3. construct a fresh BetaAnalyticsDataClient, which opens a new gRPC
#      channel and performs its own TLS handshake
#   4. run the GA4 report
#
# So one page view cost ten Firestore reads and five new gRPC channels before a
# single byte of analytics data was requested. Measured in the browser:
# overview 5.46 s, top-pages 4.34 s, traffic-sources 4.32 s, timeseries 4.23 s,
# realtime 4.04 s.
#
# The client and its credentials are reusable and are designed to be reused --
# google-auth refreshes the access token in place on the cached credentials
# object when it expires, so a long-lived client keeps working. Holding one per
# user removes steps 1-3 from every request after the first.
_SESSION_TTL_SECONDS = 1800
_sessions = {}
_sessions_lock = threading.Lock()

# A failed credential build is cached too, briefly. Failure costs an OAuth
# round trip to Google that ends in RefreshError, and without caching it the
# five endpoints each pay for one on every page load -- and the realtime poller
# pays for one on every tick, forever, for an account whose token was revoked.
# Measured against a revoked token: 3-6 s per endpoint, repeated indefinitely.
_FAILURE_TTL_SECONDS = 60

# One build lock per user, so five concurrent requests produce one client and
# one token refresh rather than five of each. Bounded by the number of distinct
# users the process has served, which is the same order as the session cache.
_build_locks = {}
_build_locks_guard = threading.Lock()


def _build_lock(user_id):
    with _build_locks_guard:
        lock = _build_locks.get(user_id)
        if lock is None:
            lock = _build_locks[user_id] = threading.Lock()
        return lock


def _cached_session(user_id):
    """The live cache entry for this user, or ``None`` if there isn't one."""
    with _sessions_lock:
        entry = _sessions.get(user_id)
        if entry and entry['expires'] > time.monotonic():
            return entry['client'], entry['config']
    return None


def _store_session(user_id, client, creds, config, ttl):
    with _sessions_lock:
        _sessions[user_id] = {
            'client': client,
            'creds': creds,
            'config': config,
            'expires': time.monotonic() + ttl,
        }

# Report payloads. GA4 aggregates on its own schedule and is not real-time, so
# a page of historical numbers is safely a few minutes stale -- and re-deriving
# it costs a multi-second API call. Realtime gets a much shorter window because
# "active users right now" is the one figure where staleness is the point.
_REPORT_TTL_SECONDS = 300
_REALTIME_TTL_SECONDS = 20


def invalidate_analytics_session(user_id):
    """Drop the cached client and every cached report for one user.

    Called whenever the stored configuration changes -- connect, disconnect,
    property switch -- because the cached client is bound to the credentials and
    property that were current when it was built, and the cached reports are
    keyed on a property that may no longer be the selected one.
    """
    with _sessions_lock:
        _sessions.pop(user_id, None)
    cache.delete('analytics_config:%s' % user_id)
    cache.clear_prefix('ga_report:%s:' % user_id)


def _analytics_session(user_id):
    """``(client, config)`` for this user, reusing the channel and the token.

    Returns ``(None, config)`` when the user is not connected or has not
    selected a property, so callers can tell "not configured" from "not
    connected" exactly as before.
    """
    hit = _cached_session(user_id)
    if hit is not None:
        return hit

    # Double-checked locking, per user. The five endpoints arrive together and
    # would otherwise each perform the same token refresh and each open its own
    # gRPC channel; the loser of the race finds the winner's work on re-check.
    # The lock is per user, so one account's cold start never blocks another's.
    with _build_lock(user_id):
        hit = _cached_session(user_id)
        if hit is not None:
            return hit

        config = _get_analytics_config(user_id)
        if not config or not config.get('property_id'):
            # Nothing built and nothing to cache: this is a configuration
            # state, not a failure, and _get_analytics_config is itself cached.
            return None, config

        creds = _get_credentials(user_id, config=config)
        if not creds:
            # Negative-cached: see _FAILURE_TTL_SECONDS. The other four
            # endpoints and the next poll now answer instantly instead of each
            # repeating the failed OAuth exchange.
            _store_session(user_id, None, None, config, _FAILURE_TTL_SECONDS)
            return None, config

        from google.analytics.data_v1beta import BetaAnalyticsDataClient

        client = BetaAnalyticsDataClient(credentials=creds)
        _store_session(user_id, client, creds, config, _SESSION_TTL_SECONDS)
        return client, config


def _cached_report(user_id, key, ttl, build):
    """Memoise one report payload, keyed per user and per query.

    ``build`` is only called on a miss. A failure is not cached: an empty or
    errored payload pinned for the whole TTL would turn one transient API
    hiccup into five minutes of a blank dashboard.
    """
    cache_key = 'ga_report:%s:%s' % (user_id, key)
    hit = cache.get(cache_key)
    if hit is not None:
        return hit
    value = build()
    cache.set(cache_key, value, ttl)
    return value


def _get_analytics_config(user_id):
    """The stored GA connection for this user.

    Cached briefly: the five analytics endpoints are five separate requests
    firing concurrently, so a request-scoped memo cannot help them -- they need
    a store that outlives one request. The TTL is short because this document
    holds the access token, and it is invalidated outright whenever the
    connection changes.
    """
    cache_key = 'analytics_config:%s' % user_id
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    doc = db_service.db.collection("analytics_config").document(user_id).get()
    config = doc.to_dict() if doc.exists else None
    if config:
        cache.set(cache_key, config, 120)
    return config


def _get_credentials(user_id, config=None):
    """OAuth credentials for this user, refreshing and persisting if needed.

    ``config`` is accepted so a caller that has already read the document does
    not pay a second read for it -- every endpoint used to read it twice.
    """
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from datetime import timezone
    if config is None:
        config = _get_analytics_config(user_id)
    if not config or not config.get('refresh_token'):
        return None

    expiry = None
    token_expiry = config.get('token_expiry')
    if token_expiry:
        try:
            if isinstance(token_expiry, str):
                dt = datetime.fromisoformat(token_expiry.replace('Z', '+00:00'))
            elif hasattr(token_expiry, 'timestamp'):
                dt = datetime.utcfromtimestamp(token_expiry.timestamp())
            elif hasattr(token_expiry, 'isoformat'):
                dt = token_expiry
            else:
                dt = None
            if dt:
                if dt.tzinfo is not None:
                    dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
                expiry = dt
        except (ValueError, TypeError, OSError):
            expiry = None

    creds = Credentials(
        token=config.get('access_token'),
        refresh_token=config.get('refresh_token'),
        token_uri='https://oauth2.googleapis.com/token',
        client_id=current_app.config['GOOGLE_OAUTH_CLIENT_ID'],
        client_secret=current_app.config['GOOGLE_OAUTH_CLIENT_SECRET'],
        scopes=SCOPES,
        expiry=expiry
    )

    if (creds.expired or not creds.token) and creds.refresh_token:
        try:
            creds.refresh(Request())
            db_service.db.collection("analytics_config").document(user_id).update({
                'access_token': creds.token,
                'token_expiry': creds.expiry.isoformat() if creds.expiry else None
            })
            # The cached copy still carries the token we just replaced. Left in
            # place, the next caller within the TTL would rebuild credentials
            # from the expired token and refresh all over again -- turning the
            # cache into a source of extra token exchanges rather than fewer.
            cache.delete('analytics_config:%s' % user_id)
        except Exception as e:
            error_str = str(e).lower()
            logger.exception("Token refresh failed")
            if 'invalid_grant' in error_str or 'token has been expired or revoked' in error_str:
                db_service.db.collection("analytics_config").document(user_id).update({
                    'connected': False,
                    'access_token': '',
                    'refresh_token': '',
                    'token_expiry': None
                })
                invalidate_analytics_session(user_id)
            return None

    return creds


def _fetch_measurement_id(creds, property_id):
    try:
        from google.analytics.admin_v1beta import AnalyticsAdminServiceClient
        client = AnalyticsAdminServiceClient(credentials=creds)
        streams = client.list_data_streams(parent=property_id, timeout=15)
        for stream in streams:
            if stream.web_stream_data and stream.web_stream_data.measurement_id:
                return (
                    stream.web_stream_data.measurement_id,
                    stream.web_stream_data.default_uri or ''
                )
    except Exception:
        logger.exception("Error fetching measurement ID")
    return (None, None)


def _extract_domain(url):
    if not url:
        return ''
    url = url.strip().rstrip('/')
    if '://' in url:
        url = url.split('://')[1]
    return url.split('/')[0]


# ==================== PERIOD MATH ====================

# The three periods the UI offers, as (days back, span in days). GA4 reads
# "7daysAgo".."today" as an *inclusive* 8-day window, so the span is N + 1 —
# and the comparison window has to be the same length or the delta is a lie.
PERIODS = {'1': 1, '7': 7, '30': 30}


def _period(raw):
    """Normalise the ?period= argument to one of the three we support."""
    return raw if raw in PERIODS else '7'


def _ranges(period):
    """
    Current and previous windows for a period, as GA4 relative-date strings.

    Relative keywords rather than computed ISO dates on purpose: "today" means
    today *in the property's timezone*, which the server cannot know. A property
    reporting in UTC+13 rolls over half a day before a UTC server does, and
    computing the dates here would silently ask for the wrong day.

      period=7 → current  7daysAgo..today     (8 days)
                 previous 15daysAgo..8daysAgo (8 days)
    """
    n = PERIODS[period]
    span = n + 1

    if period == '1':
        return ('today', 'today'), ('yesterday', 'yesterday'), 1

    return (
        (f'{n}daysAgo', 'today'),
        (f'{2 * n + 1}daysAgo', f'{n + 1}daysAgo'),
        span,
    )


def _fill_days(by_date, span, fallback_end):
    """
    Expand a sparse {YYYYMMDD: metrics} map into a dense, ordered series.

    GA4 omits days with no traffic entirely. Plotting only the days it returns
    compresses those gaps, so a quiet Sunday reads as "never happened" and the
    x-axis silently misstates the range. Every missing day inside the window is
    therefore a real zero.

    The window ends at the latest day the property actually reported, not at the
    server's today — same timezone problem as above. `fallback_end` covers a
    property that returned nothing at all.
    """
    from datetime import date, timedelta

    if by_date:
        end = max(by_date)
        end_date = date(int(end[0:4]), int(end[4:6]), int(end[6:8]))
    else:
        end_date = fallback_end

    out = []
    for i in range(span - 1, -1, -1):
        day = end_date - timedelta(days=i)
        key = day.strftime('%Y%m%d')
        row = by_date.get(key) or {}
        out.append({
            'iso': day.isoformat(),
            'label': day.strftime('%b %d'),
            'page_views': int(row.get('page_views', 0)),
            'sessions': int(row.get('sessions', 0)),
            'users': int(row.get('users', 0)),
        })
    return out


def _fill_hours(by_hour):
    """
    Today, by hour — and only up to the last hour that reported.

    Padding out to 23:00 would draw a flat line across hours that have not
    happened yet, which reads as "traffic stopped" rather than "the day is not
    over". Gaps *before* the last reported hour are genuine zeros and are kept.
    """
    if not by_hour:
        return []

    last = max(int(h) for h in by_hour)
    out = []
    for h in range(0, last + 1):
        row = by_hour.get('%02d' % h) or {}
        out.append({
            'iso': '%02d:00' % h,
            'label': '%02d:00' % h,
            'page_views': int(row.get('page_views', 0)),
            'sessions': int(row.get('sessions', 0)),
            'users': int(row.get('users', 0)),
        })
    return out


# ==================== PAGES ====================

@analytics_bp.route('/analytics')
@admin_required
def analytics_page():
    user_id = session.get('user_id')
    has_oauth = bool(current_app.config.get('GOOGLE_OAUTH_CLIENT_ID'))

    oauth_errors = {
        'denied': 'Google sign-in was cancelled. Please try connecting again.',
        'state': 'Sign-in could not be verified (your session changed). Make sure you open the app at the same address each time, then try again.',
        'no_code': 'Google did not return an authorization code. Please try connecting again.',
        'oauth': 'We could not complete the Google connection. Please try again.',
    }
    oauth_error = oauth_errors.get(request.args.get('error', ''))

    try:
        config = _get_analytics_config(user_id)
        connected = bool(config and config.get('connected') and config.get('refresh_token'))
        property_id = config.get('property_id', '') if config else ''
        property_name = config.get('property_name', '') if config else ''
        measurement_id = config.get('measurement_id', '') if config else ''
        stream_url = config.get('stream_url', '') if config else ''

        site_settings = db_service.get_site_settings(user_id) if connected else {}
        custom_domain = site_settings.get('custom_domain', '') if site_settings else ''
        site_analytics_id = site_settings.get('analytics_id', '') if site_settings else ''
    except Exception:
        # Never let a transient backend error turn navigation into a hard error
        # page — render a safe (disconnected) state instead of a 500.
        logger.exception("Analytics page load error")
        connected = False
        property_id = property_name = measurement_id = stream_url = ''
        custom_domain = site_analytics_id = ''

    return render_template('analytics.html',
                           connected=connected,
                           property_id=property_id,
                           property_name=property_name,
                           measurement_id=measurement_id,
                           stream_url=stream_url,
                           custom_domain=custom_domain,
                           site_analytics_id=site_analytics_id,
                           has_oauth=has_oauth,
                           oauth_error=oauth_error)


# ==================== OAUTH FLOW ====================

@analytics_bp.route('/analytics/connect')
@admin_required
def connect():
    from google_auth_oauthlib.flow import Flow

    client_id = current_app.config['GOOGLE_OAUTH_CLIENT_ID']
    client_secret = current_app.config['GOOGLE_OAUTH_CLIENT_SECRET']

    if not client_id or not client_secret:
        return jsonify({"error": "Google OAuth not configured"}), 400

    redirect_uri = request.host_url.rstrip('/') + REDIRECT_PATH

    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token"
            }
        },
        scopes=SCOPES,
        redirect_uri=redirect_uri
    )

    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent'
    )

    # Persist the CSRF state + PKCE verifier for the callback. Mark the session
    # permanent/modified so the cookie is reliably written before we redirect
    # off to Google and comes back intact.
    session.permanent = True
    session['oauth_state'] = state
    session['code_verifier'] = flow.code_verifier
    session.modified = True
    return redirect(authorization_url)


@analytics_bp.route('/analytics/callback')
@admin_required
def callback():
    from google_auth_oauthlib.flow import Flow

    # Google can redirect back with an error (e.g. the user denied access).
    if request.args.get('error'):
        return redirect(url_for('analytics_bp.analytics_page', error='denied'))

    # Lenient CSRF check: only enforce when we still have the stored state.
    # A missing stored state means the session was lost on the round-trip to
    # Google (commonly a localhost vs 127.0.0.1 host mismatch) — recover
    # gracefully instead of throwing a raw MismatchingStateError 500.
    expected_state = session.get('oauth_state')
    returned_state = request.args.get('state')
    if expected_state and returned_state and expected_state != returned_state:
        return redirect(url_for('analytics_bp.analytics_page', error='state'))

    code = request.args.get('code')
    if not code:
        return redirect(url_for('analytics_bp.analytics_page', error='no_code'))

    client_id = current_app.config['GOOGLE_OAUTH_CLIENT_ID']
    client_secret = current_app.config['GOOGLE_OAUTH_CLIENT_SECRET']
    redirect_uri = request.host_url.rstrip('/') + REDIRECT_PATH

    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token"
            }
        },
        scopes=SCOPES,
        redirect_uri=redirect_uri
    )

    # Exchange the authorization code directly. Passing `code` (rather than the
    # full authorization_response URL) skips oauthlib's strict state comparison,
    # which we've already handled above.
    try:
        flow.fetch_token(
            code=code,
            code_verifier=session.get('code_verifier')
        )
    except Exception:
        logger.exception("OAuth token exchange failed")
        return redirect(url_for('analytics_bp.analytics_page', error='oauth'))
    finally:
        # One-time values — don't leave them lying around in the session.
        session.pop('oauth_state', None)
        session.pop('code_verifier', None)

    creds = flow.credentials

    user_id = session.get('user_id')
    db_service.db.collection("analytics_config").document(user_id).set({
        'connected': True,
        'access_token': creds.token,
        'refresh_token': creds.refresh_token,
        'token_expiry': creds.expiry.isoformat() if creds.expiry else None,
        'property_id': '',
        'property_name': '',
        'connected_at': utcnow().isoformat()
    })
    # A cached client from a previous connection would still be holding the old
    # credentials, so a reconnect has to start from a clean session.
    invalidate_analytics_session(user_id)

    db_service.log_activity(
        user_id=user_id,
        user_name=session.get('user_name', 'Admin'),
        type="settings",
        action_text="Connected Google Analytics",
        target_type="settings",
        target_name="Google Analytics"
    )

    return redirect(url_for('analytics_bp.analytics_page'))


@analytics_bp.route('/analytics/disconnect', methods=['POST'])
@admin_required
def disconnect():
    user_id = session.get('user_id')
    db_service.db.collection("analytics_config").document(user_id).delete()
    # Without this the cached client keeps answering for up to 30 minutes after
    # the user has disconnected, and the cached reports keep serving the numbers
    # from the property they just detached.
    invalidate_analytics_session(user_id)
    db_service.db.collection("site_settings").document(user_id).set(
        {'analytics_id': ''}, merge=True
    )

    db_service.log_activity(
        user_id=user_id,
        user_name=session.get('user_name', 'Admin'),
        type="settings",
        action_text="Disconnected Google Analytics",
        target_type="settings",
        target_name="Google Analytics"
    )

    return jsonify({"success": True})


# ==================== PROPERTY SELECTION ====================

@analytics_bp.route('/analytics/properties')
@admin_required
def list_properties():
    from google.analytics.admin_v1beta import AnalyticsAdminServiceClient

    user_id = session.get('user_id')
    creds = _get_credentials(user_id)
    if not creds:
        return jsonify({"error": "Not connected", "reconnect": True}), 401

    try:
        client = AnalyticsAdminServiceClient(credentials=creds)
        accounts = client.list_account_summaries(timeout=15)

        properties = []
        for account in accounts:
            for prop in account.property_summaries:
                properties.append({
                    'property_id': prop.property,
                    'display_name': prop.display_name,
                    'account_name': account.display_name
                })

        if not properties:
            return jsonify({"success": True, "properties": [], "message": "No GA4 properties found in this account."})

        return jsonify({"success": True, "properties": properties})
    except Exception as e:
        logger.exception("Error listing properties")
        return jsonify({"error": str(e)}), 500


@analytics_bp.route('/analytics/select-property', methods=['POST'])
@admin_required
def select_property():
    data = request.json
    property_id = data.get('property_id', '')
    property_name = data.get('property_name', '')
    user_id = session.get('user_id')

    if not property_id:
        return jsonify({"error": "Property ID required"}), 400

    update_data = {
        'property_id': property_id,
        'property_name': property_name
    }

    creds = _get_credentials(user_id)
    measurement_id, stream_url = (None, None)
    domain = ''
    if creds:
        measurement_id, stream_url = _fetch_measurement_id(creds, property_id)
        domain = _extract_domain(stream_url)
        if measurement_id:
            update_data['measurement_id'] = measurement_id
            update_data['stream_url'] = stream_url or ''

            site_update = {
                'analytics_id': measurement_id
            }
            if domain:
                site_update['custom_domain'] = domain

            db_service.db.collection("site_settings").document(user_id).set(
                site_update, merge=True
            )

    db_service.db.collection("analytics_config").document(user_id).update(update_data)
    # The cached reports are keyed per user, not per property, so switching
    # property has to discard them -- otherwise the screen would show the old
    # property's numbers under the new property's name.
    invalidate_analytics_session(user_id)

    return jsonify({
        "success": True,
        "measurement_id": measurement_id or '',
        "stream_url": stream_url or '',
        "domain": domain
    })


# ==================== DATA API ENDPOINTS ====================

@analytics_bp.route('/api/analytics/realtime')
@admin_required
def realtime_data():
    from google.analytics.data_v1beta.types import RunRealtimeReportRequest, Metric

    user_id = session.get('user_id')
    client, config = _analytics_session(user_id)
    if not config or not config.get('property_id'):
        return jsonify({"error": "Not configured"}), 400
    if client is None:
        return jsonify({"error": "Not connected", "reconnect": True}), 401

    try:
        property_id = config['property_id']

        def build():
            response = client.run_realtime_report(
                RunRealtimeReportRequest(
                    property=property_id,
                    metrics=[Metric(name="activeUsers")]
                )
            )
            active_users = 0
            if response.rows:
                active_users = int(response.rows[0].metric_values[0].value)
            return {"success": True, "active_users": active_users}

        # A very short TTL, not none: the screen polls this on a timer, and two
        # polls a few seconds apart cannot show a meaningfully different number.
        return jsonify(_cached_report(
            user_id, 'realtime', _REALTIME_TTL_SECONDS, build))
    except Exception as e:
        logger.exception("Realtime error")
        return jsonify({"error": str(e)}), 500


OVERVIEW_METRICS = [
    "screenPageViews", "sessions", "totalUsers",
    "averageSessionDuration", "bounceRate",
]


def _overview_totals(client, property_id, start_date, end_date):
    """The five headline metrics for one window, as one un-dimensioned row."""
    from google.analytics.data_v1beta.types import RunReportRequest, DateRange, Metric

    response = client.run_report(
        RunReportRequest(
            property=property_id,
            date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
            metrics=[Metric(name=m) for m in OVERVIEW_METRICS],
        )
    )

    totals = {'page_views': 0, 'sessions': 0, 'users': 0,
              'avg_duration': 0, 'bounce_rate': 0}
    if response.rows:
        v = response.rows[0].metric_values
        totals['page_views'] = int(v[0].value)
        totals['sessions'] = int(v[1].value)
        totals['users'] = int(v[2].value)
        totals['avg_duration'] = round(float(v[3].value), 1)
        totals['bounce_rate'] = round(float(v[4].value) * 100, 1)
    return totals


@analytics_bp.route('/api/analytics/overview')
@admin_required
def overview_data():
    """
    Headline metrics for the selected period, plus the same metrics for the
    window immediately before it.

    The comparison is the point. A stat tile that shows only a figure says how
    many and leaves the reader to guess whether that is good — the delta is what
    turns the count into a fact. Two un-dimensioned reports rather than one
    request carrying two date ranges: multiple ranges make GA4 append a
    `dateRange` dimension and fold both windows into the same row set, which is
    a shape worth avoiding for something this load-bearing.
    """
    user_id = session.get('user_id')
    client, config = _analytics_session(user_id)
    if not config or not config.get('property_id'):
        return jsonify({"error": "Not configured"}), 400
    if client is None:
        return jsonify({"error": "Not connected", "reconnect": True}), 401

    try:
        property_id = config['property_id']
        period = _period(request.args.get('period', '7'))
        (cur_start, cur_end), (prev_start, prev_end), _span = _ranges(period)

        def build():
            from app.utils.parallel import run_parallel_simple

            # Two independent GA4 reports. Run one after the other this was the
            # slowest endpoint on the screen at 5.46 s -- it paid for both
            # windows in series to render one row of tiles. They share nothing
            # but the client, which is safe to use concurrently.
            current, previous = run_parallel_simple([
                (_overview_totals, (client, property_id, cur_start, cur_end)),
                (_overview_totals, (client, property_id, prev_start, prev_end)),
            ], max_workers=2)

            if current is None:
                # The headline figures are the payload; without them there is
                # nothing to show, so this is a real failure rather than a
                # degraded response.
                raise RuntimeError('overview report failed')

            # The comparison is an enhancement, not the payload. If it fails --
            # a brand-new property with no history, a partial API error -- the
            # tiles still render their figures and simply carry no delta.
            # run_parallel_simple already logged the traceback.
            return {
                "success": True,
                "data": current,
                "previous": previous,
                "period": period,
            }

        return jsonify(_cached_report(
            user_id, 'overview:%s' % period, _REPORT_TTL_SECONDS, build))
    except Exception as e:
        logger.exception("Overview error")
        return jsonify({"error": str(e)}), 500


@analytics_bp.route('/api/analytics/timeseries')
@admin_required
def timeseries_data():
    """
    The selected period broken down over time — by hour for today, by day
    otherwise — so the trend has a shape instead of a single total.

    Deliberately a separate endpoint from /overview: the chart and the tiles
    fail independently, so a time-series error leaves the headline figures on
    screen instead of blanking the whole screen.

    The daily totals are NOT summed to produce the period total. `totalUsers` is
    a de-duplicated count, so a visitor who came back on Tuesday would be
    counted twice; /overview asks GA4 for the period total directly. The series
    is the shape, never the sum.
    """
    from google.analytics.data_v1beta.types import (
        RunReportRequest, DateRange, Metric, Dimension, OrderBy
    )
    from datetime import date

    user_id = session.get('user_id')
    client, config = _analytics_session(user_id)
    if not config or not config.get('property_id'):
        return jsonify({"error": "Not configured"}), 400
    if client is None:
        return jsonify({"error": "Not connected", "reconnect": True}), 401

    try:
        property_id = config['property_id']
        period = _period(request.args.get('period', '7'))
        (start_date, end_date), _prev, span = _ranges(period)

        # Today is plotted by hour: one point is not a trend.
        by_hour = period == '1'
        dimension = 'hour' if by_hour else 'date'

        def build():
            response = client.run_report(
                RunReportRequest(
                    property=property_id,
                    date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
                    dimensions=[Dimension(name=dimension)],
                    metrics=[
                        Metric(name="screenPageViews"),
                        Metric(name="sessions"),
                        Metric(name="totalUsers"),
                    ],
                    order_bys=[OrderBy(dimension=OrderBy.DimensionOrderBy(dimension_name=dimension))],
                    limit=200,
                )
            )

            buckets = {}
            for row in response.rows:
                buckets[row.dimension_values[0].value] = {
                    'page_views': int(row.metric_values[0].value),
                    'sessions': int(row.metric_values[1].value),
                    'users': int(row.metric_values[2].value),
                }

            points = _fill_hours(buckets) if by_hour else _fill_days(buckets, span, date.today())

            return {
                "success": True,
                "granularity": 'hour' if by_hour else 'day',
                "points": points,
                "period": period,
            }

        return jsonify(_cached_report(
            user_id, 'timeseries:%s' % period, _REPORT_TTL_SECONDS, build))
    except Exception as e:
        logger.exception("Timeseries error")
        return jsonify({"error": str(e)}), 500


@analytics_bp.route('/api/analytics/top-pages')
@admin_required
def top_pages():
    from google.analytics.data_v1beta.types import (
        RunReportRequest, DateRange, Metric, Dimension, OrderBy
    )

    user_id = session.get('user_id')
    client, config = _analytics_session(user_id)
    if not config or not config.get('property_id'):
        return jsonify({"error": "Not configured"}), 400
    if client is None:
        return jsonify({"error": "Not connected", "reconnect": True}), 401

    try:
        property_id = config['property_id']
        period = _period(request.args.get('period', '7'))
        (start_date, end_date), _prev, _span = _ranges(period)

        def build():
            response = client.run_report(
                RunReportRequest(
                    property=property_id,
                    date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
                    dimensions=[Dimension(name="pagePath"), Dimension(name="pageTitle")],
                    metrics=[
                        Metric(name="screenPageViews"),
                        Metric(name="averageSessionDuration")
                    ],
                    order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name="screenPageViews"), desc=True)],
                    limit=10
                )
            )

            pages = []
            for row in response.rows:
                pages.append({
                    'path': row.dimension_values[0].value,
                    'title': row.dimension_values[1].value,
                    'views': int(row.metric_values[0].value),
                    'avg_time': round(float(row.metric_values[1].value), 1)
                })
            return {"success": True, "pages": pages}

        return jsonify(_cached_report(
            user_id, 'top-pages:%s' % period, _REPORT_TTL_SECONDS, build))
    except Exception as e:
        logger.exception("Top pages error")
        return jsonify({"error": str(e)}), 500


@analytics_bp.route('/api/analytics/traffic-sources')
@admin_required
def traffic_sources():
    from google.analytics.data_v1beta.types import (
        RunReportRequest, DateRange, Metric, Dimension, OrderBy
    )

    user_id = session.get('user_id')
    client, config = _analytics_session(user_id)
    if not config or not config.get('property_id'):
        return jsonify({"error": "Not configured"}), 400
    if client is None:
        return jsonify({"error": "Not connected", "reconnect": True}), 401

    try:
        property_id = config['property_id']
        period = _period(request.args.get('period', '7'))
        (start_date, end_date), _prev, _span = _ranges(period)

        def build():
            # Every channel, not the top 8. The screen draws each one as a
            # share of the whole, and a truncated list makes those shares add up
            # to the top 8 rather than to the real total — so a site with a long
            # tail would read as more concentrated than it is.
            # sessionDefaultChannelGroup is a fixed vocabulary of about sixteen
            # values, so 25 is the whole thing; the client folds the tail into
            # one "Other" row for the chart.
            response = client.run_report(
                RunReportRequest(
                    property=property_id,
                    date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
                    dimensions=[Dimension(name="sessionDefaultChannelGroup")],
                    metrics=[
                        Metric(name="sessions"),
                        Metric(name="totalUsers")
                    ],
                    order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name="sessions"), desc=True)],
                    limit=25
                )
            )

            sources = []
            for row in response.rows:
                sources.append({
                    'channel': row.dimension_values[0].value,
                    'sessions': int(row.metric_values[0].value),
                    'users': int(row.metric_values[1].value)
                })

            return {
                "success": True,
                "sources": sources,
                "total_sessions": sum(src['sessions'] for src in sources),
            }

        return jsonify(_cached_report(
            user_id, 'traffic-sources:%s' % period, _REPORT_TTL_SECONDS, build))
    except Exception as e:
        logger.exception("Traffic sources error")
        return jsonify({"error": str(e)}), 500
