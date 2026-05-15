import os
from flask import Flask, redirect, url_for, session, render_template, abort, request, jsonify, current_app
from flask_compress import Compress
from config import Config
from app.firebase.firebase_admin import FirebaseLoader
from app.firebase.firestore_service import FirestoreService
from app.utils.date_utils import format_date, format_time, format_datetime
from whitenoise import WhiteNoise
from werkzeug.middleware.proxy_fix import ProxyFix
from functools import wraps
from datetime import datetime, timezone


def admin_required(f):
    """Decorator to restrict routes to admin users only"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('auth_bp.login'))
        if session.get('user_role') != 'ADMIN':
            abort(404)  # Show 404 instead of 403 to hide the existence of the page
        return f(*args, **kwargs)
    return decorated_function


def create_app(config_class=Config):
    app = Flask(__name__, static_folder='static', template_folder='templates')
    app.config.from_object(config_class)

    # Enable response compression (gzip)
    Compress(app)

    # Middleware setup
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    cache_max_age = 0 if os.environ.get('FLASK_DEBUG') == '1' else 604800
    app.wsgi_app = WhiteNoise(app.wsgi_app, root='app/static/', prefix='static/', max_age=cache_max_age)

    # Initialize Firebase
    FirebaseLoader.get_instance(app.config['FIREBASE_SERVICE_ACCOUNT'])

    # Register Jinja2 template filters for date/time formatting
    @app.template_filter('localized_date')
    def localized_date_filter(dt, settings=None):
        """Format date with user's timezone and date format settings."""
        if settings is None:
            return format_date(dt)
        return format_date(
            dt,
            settings.get('date_format', 'MMM DD, YYYY'),
            settings.get('timezone', 'UTC')
        )

    @app.template_filter('localized_time')
    def localized_time_filter(dt, settings=None):
        """Format time with user's timezone and time format settings."""
        if settings is None:
            return format_time(dt)
        return format_time(
            dt,
            settings.get('time_format', '12h'),
            settings.get('timezone', 'UTC')
        )

    @app.template_filter('localized_datetime')
    def localized_datetime_filter(dt, settings=None):
        """Format full datetime with user's settings."""
        if settings is None:
            return format_datetime(dt)
        return format_datetime(
            dt,
            settings.get('date_format', 'MMM DD, YYYY'),
            settings.get('time_format', '12h'),
            settings.get('timezone', 'UTC')
        )

    # Context processor to inject app settings into all templates
    _ctx_db_service = FirestoreService()

    @app.context_processor
    def inject_app_settings():
        """Make app settings available to all templates (cached in FirestoreService)."""
        try:
            app_settings = _ctx_db_service.get_app_settings()
            return {'app_config': app_settings}
        except Exception:
            return {'app_config': {'app_name': 'Scriptly', 'tagline': ''}}

    @app.route('/')
    def index():
        if not session.get('logged_in'):
            return redirect(url_for('auth_bp.login'))
        return redirect(url_for('blog.home'))

    # Error handlers
    @app.errorhandler(404)
    def page_not_found(e):
        return render_template('errors/404.html'), 404

    @app.errorhandler(403)
    def forbidden(e):
        return render_template('errors/404.html'), 404  # Show 404 to hide existence

    # Blueprint Registration
    from app.routes.blog_routes import blog_bp
    from app.routes.auth import auth_bp
    from app.routes.user_mgmt import user_bp
    from app.routes.site_routes import site_bp
    from app.routes.newsletter_routes import newsletter_bp
    from app.routes.settings_routes import settings_bp
    from app.routes.activity_routes import activity_bp
    from app.routes.blogs_listing_routes import blogs_bp
    from app.routes.analytics_routes import analytics_bp
    from app.routes.schedule_routes import schedule_bp
    from app.routes.leads_routes import leads_bp
    from app.routes.gallery_routes import gallery_bp

    app.register_blueprint(blog_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(site_bp)
    app.register_blueprint(newsletter_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(activity_bp)
    app.register_blueprint(blogs_bp)
    app.register_blueprint(analytics_bp)
    app.register_blueprint(schedule_bp)
    app.register_blueprint(leads_bp)
    app.register_blueprint(gallery_bp)

    # FIX: Register with url_prefix to match your JS calls (/users/list, etc.)
    app.register_blueprint(user_bp, url_prefix='/users')

    # Session inactivity timeout check
    @app.before_request
    def check_session_timeout():
        # Exempt static files, auth pages, and public site routes
        if request.endpoint and request.endpoint == 'static':
            return None
        exempt = {'auth_bp.login', 'auth_bp.signup', 'auth_bp.verify_token', 'auth_bp.logout'}
        if request.endpoint in exempt:
            return None
        if request.endpoint and request.endpoint.startswith('site_bp.'):
            return None

        if session.get('logged_in'):
            last_activity = session.get('last_activity')
            if last_activity is not None:
                if isinstance(last_activity, str):
                    last_activity = datetime.fromisoformat(last_activity)
                elapsed = datetime.now(timezone.utc) - last_activity
                timeout = current_app.config.get('PERMANENT_SESSION_LIFETIME')
                if elapsed > timeout:
                    session.clear()
                    if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in (request.headers.get('Accept') or ''):
                        return jsonify({'error': 'session_expired', 'redirect': url_for('auth_bp.login', expired=1)}), 401
                    return redirect(url_for('auth_bp.login', expired=1))
            # Reset activity timestamp
            session['last_activity'] = datetime.now(timezone.utc).isoformat()

    # Initialize background scheduler for scheduled blog publishing
    from app.scheduler import init_scheduler
    init_scheduler(app)

    # Pre-warm Firebase token verification key cache and Firestore gRPC connection
    import threading
    def _warm_firebase():
        try:
            from firebase_admin import auth
            auth.verify_id_token("dummy", check_revoked=False)
        except Exception:
            pass
        try:
            db = FirestoreService()
            db.get_app_settings()
        except Exception:
            pass
    threading.Thread(target=_warm_firebase, daemon=True).start()

    return app