import os
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, abort, current_app
from functools import wraps
from datetime import datetime, timedelta
from app.firebase.firestore_service import FirestoreService

analytics_bp = Blueprint('analytics_bp', __name__)
db_service = FirestoreService()

SCOPES = ['https://www.googleapis.com/auth/analytics.readonly']
REDIRECT_PATH = '/analytics/callback'


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('auth_bp.login'))
        if session.get('user_role') != 'ADMIN':
            abort(404)
        return f(*args, **kwargs)
    return decorated_function


def _get_analytics_config(user_id):
    doc = db_service.db.collection("analytics_config").document(user_id).get()
    return doc.to_dict() if doc.exists else None


def _get_credentials(user_id):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from datetime import datetime, timezone
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
        except Exception as e:
            error_str = str(e).lower()
            print(f"Token refresh failed: {e}")
            if 'invalid_grant' in error_str or 'token has been expired or revoked' in error_str:
                db_service.db.collection("analytics_config").document(user_id).update({
                    'connected': False,
                    'access_token': '',
                    'refresh_token': '',
                    'token_expiry': None
                })
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
    except Exception as e:
        print(f"Error fetching measurement ID: {e}")
    return (None, None)


def _extract_domain(url):
    if not url:
        return ''
    url = url.strip().rstrip('/')
    if '://' in url:
        url = url.split('://')[1]
    return url.split('/')[0]


# ==================== PAGES ====================

@analytics_bp.route('/analytics')
@admin_required
def analytics_page():
    user_id = session.get('user_id')
    has_oauth = bool(current_app.config.get('GOOGLE_OAUTH_CLIENT_ID'))

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
    except Exception as e:
        # Never let a transient backend error turn navigation into a hard error
        # page — render a safe (disconnected) state instead of a 500.
        print(f"Analytics page load error: {e}")
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
                           has_oauth=has_oauth)


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

    session['oauth_state'] = state
    session['code_verifier'] = flow.code_verifier
    return redirect(authorization_url)


@analytics_bp.route('/analytics/callback')
@admin_required
def callback():
    from google_auth_oauthlib.flow import Flow

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
        redirect_uri=redirect_uri,
        state=session.get('oauth_state')
    )

    flow.fetch_token(
        authorization_response=request.url,
        code_verifier=session.get('code_verifier')
    )
    creds = flow.credentials

    user_id = session.get('user_id')
    db_service.db.collection("analytics_config").document(user_id).set({
        'connected': True,
        'access_token': creds.token,
        'refresh_token': creds.refresh_token,
        'token_expiry': creds.expiry.isoformat() if creds.expiry else None,
        'property_id': '',
        'property_name': '',
        'connected_at': datetime.utcnow().isoformat()
    })

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
    from google.api_core import timeout as api_timeout

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
        print(f"Error listing properties: {e}")
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
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.analytics.data_v1beta.types import RunRealtimeReportRequest, Metric

    user_id = session.get('user_id')
    config = _get_analytics_config(user_id)
    if not config or not config.get('property_id'):
        return jsonify({"error": "Not configured"}), 400

    creds = _get_credentials(user_id)
    if not creds:
        return jsonify({"error": "Not connected", "reconnect": True}), 401

    try:
        client = BetaAnalyticsDataClient(credentials=creds)
        property_id = config['property_id']

        response = client.run_realtime_report(
            RunRealtimeReportRequest(
                property=property_id,
                metrics=[Metric(name="activeUsers")]
            )
        )

        active_users = 0
        if response.rows:
            active_users = int(response.rows[0].metric_values[0].value)

        return jsonify({"success": True, "active_users": active_users})
    except Exception as e:
        print(f"Realtime error: {e}")
        return jsonify({"error": str(e)}), 500


@analytics_bp.route('/api/analytics/overview')
@admin_required
def overview_data():
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.analytics.data_v1beta.types import (
        RunReportRequest, DateRange, Metric, Dimension
    )

    user_id = session.get('user_id')
    config = _get_analytics_config(user_id)
    if not config or not config.get('property_id'):
        return jsonify({"error": "Not configured"}), 400

    creds = _get_credentials(user_id)
    if not creds:
        return jsonify({"error": "Not connected", "reconnect": True}), 401

    try:
        client = BetaAnalyticsDataClient(credentials=creds)
        property_id = config['property_id']
        period = request.args.get('period', '7')
        start_date = "today" if period == "1" else f"{period}daysAgo"

        response = client.run_report(
            RunReportRequest(
                property=property_id,
                date_ranges=[DateRange(start_date=start_date, end_date="today")],
                metrics=[
                    Metric(name="screenPageViews"),
                    Metric(name="sessions"),
                    Metric(name="totalUsers"),
                    Metric(name="averageSessionDuration"),
                    Metric(name="bounceRate")
                ]
            )
        )

        data = {
            'page_views': 0,
            'sessions': 0,
            'users': 0,
            'avg_duration': 0,
            'bounce_rate': 0
        }

        if response.rows:
            row = response.rows[0]
            data['page_views'] = int(row.metric_values[0].value)
            data['sessions'] = int(row.metric_values[1].value)
            data['users'] = int(row.metric_values[2].value)
            data['avg_duration'] = round(float(row.metric_values[3].value), 1)
            data['bounce_rate'] = round(float(row.metric_values[4].value) * 100, 1)

        return jsonify({"success": True, "data": data, "period": period})
    except Exception as e:
        print(f"Overview error: {e}")
        return jsonify({"error": str(e)}), 500


@analytics_bp.route('/api/analytics/top-pages')
@admin_required
def top_pages():
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.analytics.data_v1beta.types import (
        RunReportRequest, DateRange, Metric, Dimension, OrderBy
    )

    user_id = session.get('user_id')
    config = _get_analytics_config(user_id)
    if not config or not config.get('property_id'):
        return jsonify({"error": "Not configured"}), 400

    creds = _get_credentials(user_id)
    if not creds:
        return jsonify({"error": "Not connected", "reconnect": True}), 401

    try:
        client = BetaAnalyticsDataClient(credentials=creds)
        property_id = config['property_id']
        period = request.args.get('period', '7')
        start_date = "today" if period == "1" else f"{period}daysAgo"

        response = client.run_report(
            RunReportRequest(
                property=property_id,
                date_ranges=[DateRange(start_date=start_date, end_date="today")],
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

        return jsonify({"success": True, "pages": pages})
    except Exception as e:
        print(f"Top pages error: {e}")
        return jsonify({"error": str(e)}), 500


@analytics_bp.route('/api/analytics/traffic-sources')
@admin_required
def traffic_sources():
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.analytics.data_v1beta.types import (
        RunReportRequest, DateRange, Metric, Dimension, OrderBy
    )

    user_id = session.get('user_id')
    config = _get_analytics_config(user_id)
    if not config or not config.get('property_id'):
        return jsonify({"error": "Not configured"}), 400

    creds = _get_credentials(user_id)
    if not creds:
        return jsonify({"error": "Not connected", "reconnect": True}), 401

    try:
        client = BetaAnalyticsDataClient(credentials=creds)
        property_id = config['property_id']
        period = request.args.get('period', '7')
        start_date = "today" if period == "1" else f"{period}daysAgo"

        response = client.run_report(
            RunReportRequest(
                property=property_id,
                date_ranges=[DateRange(start_date=start_date, end_date="today")],
                dimensions=[Dimension(name="sessionDefaultChannelGroup")],
                metrics=[
                    Metric(name="sessions"),
                    Metric(name="totalUsers")
                ],
                order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name="sessions"), desc=True)],
                limit=8
            )
        )

        sources = []
        for row in response.rows:
            sources.append({
                'channel': row.dimension_values[0].value,
                'sessions': int(row.metric_values[0].value),
                'users': int(row.metric_values[1].value)
            })

        return jsonify({"success": True, "sources": sources})
    except Exception as e:
        print(f"Traffic sources error: {e}")
        return jsonify({"error": str(e)}), 500
