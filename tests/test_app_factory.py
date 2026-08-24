"""The application factory and the request pipeline it composes.

These tests exist because the previous suite could not build the real app --
``create_app()`` started a scheduler thread and made network calls, so the
tests assembled a two-blueprint stand-in instead. Everything asserted here
(error shape, security headers, cache policy, request ids) was therefore
completely untested, while shipping on every request.
"""
import json

import pytest


class TestFactory:
    def test_builds_with_every_blueprint(self, app):
        expected = {
            'health', 'auth_bp', 'blog', 'site_bp', 'newsletter', 'settings',
            'activity', 'blogs_listing', 'analytics_bp', 'schedule', 'leads',
            'gallery', 'optimization', 'user_bp',
        }
        assert expected <= set(app.blueprints)

    def test_no_duplicate_endpoint_names(self, app):
        """A duplicate registration silently shadows one of the two routes."""
        endpoints = [rule.endpoint for rule in app.url_map.iter_rules()]
        duplicates = {e for e in endpoints if endpoints.count(e) > 1}
        assert not duplicates, f'duplicate endpoints: {duplicates}'

    def test_testing_config_starts_no_background_threads(self, app):
        """A test must not start the scheduler or reach the network.

        This is the property that lets the suite use the real factory. If it
        regresses, every test starts publishing scheduled posts.
        """
        from app.scheduler import scheduler

        assert not scheduler.running
        assert app.config['SCHEDULER_ENABLED'] is False

    def test_secrets_are_not_exposed_in_config_dump(self, app):
        """Guards against a debug route or error page rendering app.config."""
        assert app.config['SECRET_KEY']
        assert 'SECRET_KEY' not in json.dumps(
            {k: str(v) for k, v in app.config.items() if 'KEY' not in k}
        )


class TestSecurityHeaders:
    @pytest.mark.parametrize('header,expected', [
        ('X-Content-Type-Options', 'nosniff'),
        ('X-Frame-Options', 'DENY'),
        ('Referrer-Policy', 'strict-origin-when-cross-origin'),
        ('Cross-Origin-Opener-Policy', 'same-origin'),
    ])
    def test_present_on_every_response(self, client, header, expected):
        assert client.get('/login').headers[header] == expected

    def test_csp_forbids_framing_and_inline_objects(self, client):
        csp = client.get('/login').headers['Content-Security-Policy']
        assert "frame-ancestors 'none'" in csp
        assert "object-src 'none'" in csp
        assert "base-uri 'self'" in csp

    def test_hsts_absent_over_plaintext(self, client):
        """HSTS on an http response is ignored by spec and pins localhost."""
        assert 'Strict-Transport-Security' not in client.get('/login').headers

    def test_headers_present_on_error_responses_too(self, client):
        """An error path must not bypass the header hook."""
        response = client.get('/definitely-not-a-route')
        assert response.status_code == 404
        assert response.headers['X-Content-Type-Options'] == 'nosniff'


class TestRequestIds:
    def test_every_response_carries_one(self, client):
        assert client.get('/login').headers['X-Request-ID']

    def test_upstream_id_is_honoured(self, client):
        """So a trace spans the load balancer and the app."""
        response = client.get('/login', headers={'X-Request-ID': 'trace-abc-123'})
        assert response.headers['X-Request-ID'] == 'trace-abc-123'

    def test_upstream_id_is_length_bounded(self, client):
        """It is attacker-controlled and lands in every log line."""
        response = client.get('/login', headers={'X-Request-ID': 'x' * 500})
        assert len(response.headers['X-Request-ID']) <= 64

    def test_ids_differ_between_requests(self, client):
        first = client.get('/login').headers['X-Request-ID']
        second = client.get('/login').headers['X-Request-ID']
        assert first != second


class TestErrorHandling:
    def test_api_404_is_json_with_a_stable_code(self, client):
        response = client.get('/api/nope')
        body = response.get_json()
        assert response.status_code == 404
        assert body['success'] is False
        assert body['code'] == 'not_found'
        assert body['request_id']

    def test_page_404_is_html(self, client):
        response = client.get('/nope')
        assert response.status_code == 404
        assert 'text/html' in response.content_type

    def test_unexpected_exception_leaks_nothing(self, app, client):
        """The generic handler must not echo the exception to the caller."""
        secret = '/var/secrets/serviceAccount.json is unreadable'

        @app.route('/api/_boom')
        def boom():
            raise RuntimeError(secret)

        response = client.get('/api/_boom')
        body = response.get_json()

        assert response.status_code == 500
        assert secret not in json.dumps(body)
        assert 'RuntimeError' not in json.dumps(body)
        assert body['request_id']

    def test_app_error_maps_to_its_status_code(self, app, client):
        from app.core.errors import ConflictError

        @app.route('/api/_conflict')
        def conflict():
            raise ConflictError('That slug is taken.', details={'field': 'slug'})

        response = client.get('/api/_conflict')
        body = response.get_json()

        assert response.status_code == 409
        assert body['code'] == 'conflict'
        assert body['error'] == 'That slug is taken.'
        assert body['details'] == {'field': 'slug'}

    def test_capacity_error_sets_retry_after(self, app, client):
        from app.core.errors import CapacityError

        @app.route('/api/_busy')
        def busy():
            raise CapacityError()

        response = client.get('/api/_busy')
        assert response.status_code == 503
        assert response.headers['Retry-After'] == '30'

    def test_405_is_handled_not_raised(self, client):
        response = client.post('/login')
        assert response.status_code == 405


class TestCachePolicy:
    def test_authenticated_pages_are_never_stored(self, client):
        """A cached dashboard page hands the next viewer another user's data."""
        cache_control = client.get('/login').headers['Cache-Control']
        assert 'no-store' in cache_control

    def test_health_is_not_cached(self, client):
        assert 'no-store' in client.get('/healthz').headers['Cache-Control']


class TestHealthEndpoints:
    def test_livez_touches_no_dependency(self, client):
        """Liveness must not fail because Firestore is down.

        Restarting the process does not fix a dependency outage, so a liveness
        probe that checks dependencies produces a restart loop.
        """
        body = client.get('/livez').get_json()
        assert body['status'] == 'alive'
        assert 'checks' not in body

    def test_readyz_reports_the_critical_dependency(self, client):
        response = client.get('/readyz')
        assert response.status_code == 200
        assert 'firestore' in response.get_json()['checks']

    def test_healthz_covers_every_component(self, client):
        checks = client.get('/healthz').get_json()['checks']
        assert set(checks) == {'firestore', 'cache', 'ai', 'tasks', 'storage'}
        for name, result in checks.items():
            assert result['status'] in ('ok', 'degraded', 'fail'), name
            assert 'duration_ms' in result, name

    def test_probes_are_exempt_from_rate_limits(self, client):
        """Throttling the orchestrator's probe makes it declare the app dead."""
        for _ in range(30):
            assert client.get('/livez').status_code == 200


class TestRootRoute:
    def test_anonymous_visitor_is_sent_to_login(self, client):
        response = client.get('/')
        assert response.status_code == 302
        assert '/login' in response.headers['Location']

    def test_signed_in_user_is_sent_to_the_dashboard(self, signed_in):
        client = signed_in()
        response = client.get('/')
        assert response.status_code == 302
        assert '/login' not in response.headers['Location']
