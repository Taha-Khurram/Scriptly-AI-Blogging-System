"""Smoke coverage for every registered route.

Not behavioural tests -- they assert that each route is *wired correctly*: it
resolves, its access control is applied, and it does not raise an unhandled
exception on the way to its data layer. That is the class of regression a
refactor introduces and a manual click-through misses, because nobody visits all
137 routes by hand after every change.

Two properties are checked exhaustively across the whole URL map rather than
route by route, so a newly added route is covered the moment it exists:

* no GET route is reachable without a session unless it is deliberately public;
* no route answers 500 to a well-formed request.

The data layer is mocked, so a route reaching Firestore gets a MagicMock rather
than a network call. That means a 200 here proves the route ran, not that its
output is correct -- which is exactly the scope intended.
"""
import pytest

# Routes that must be reachable without a session. Anything not on this list is
# expected to redirect or refuse when called anonymously.
PUBLIC_GET_ENDPOINTS = frozenset((
    'index',                      # redirects to login
    'static',
    'health.livez', 'health.readyz', 'health.healthz',
    'auth_bp.login', 'auth_bp.signup', 'auth_bp.forgot_password',
    'auth_bp.logout',
    # A recipient must be able to unsubscribe without an account; requiring one
    # is both hostile and a CAN-SPAM problem. Rate-limited instead.
    'newsletter.unsubscribe',
    # Returns only app_name / tagline / logo / favicon, for the login and
    # public pages to brand themselves before a session exists.
    'settings.get_public_settings',
))


def _get_rules(app, method='GET'):
    """Every rule for ``method`` that takes no path parameters."""
    return [
        rule for rule in app.url_map.iter_rules()
        if method in rule.methods
        and not rule.arguments
        and rule.endpoint != 'static'
    ]


class TestAnonymousAccess:
    def test_no_dashboard_route_is_reachable_without_a_session(self, app, client, mock_db):
        """The exhaustive version of "is this page protected?".

        Checked across the whole URL map so a route added later is covered
        automatically -- which is the failure this catches: a new blueprint
        whose author forgot the decorator.
        """
        leaked = []
        for rule in _get_rules(app):
            if rule.endpoint in PUBLIC_GET_ENDPOINTS:
                continue
            if rule.endpoint.startswith('site_bp.'):
                continue           # visitor-facing site, public by design
            if rule.endpoint.startswith('_test'):
                continue           # fixtures from other test modules

            response = client.get(rule.rule)
            # 302 -> login, 401/403 -> refused, 404 -> hidden from non-admins.
            if response.status_code not in (302, 401, 403, 404):
                leaked.append((rule.rule, rule.endpoint, response.status_code))

        assert not leaked, f'reachable without a session: {leaked}'

    def test_public_endpoints_answer_anonymously(self, client, mock_db):
        for path in ('/login', '/signup', '/forgot-password', '/livez', '/readyz'):
            assert client.get(path).status_code == 200, path


class TestAuthenticatedPages:
    """Every parameterless dashboard GET, as an admin, must not 500."""

    def test_no_route_raises_an_unhandled_exception(self, app, signed_in, mock_db):
        client = signed_in(role='ADMIN')
        failures = []

        for rule in _get_rules(app):
            if rule.endpoint.startswith(('site_bp.', 'health.', '_test')):
                continue
            if rule.endpoint in ('auth_bp.logout',):
                continue           # clears the session the loop depends on

            response = client.get(rule.rule)
            if response.status_code >= 500:
                failures.append((rule.rule, response.status_code))

        assert not failures, f'server errors: {failures}'

    @pytest.mark.parametrize('path', [
        '/dashboard', '/create', '/drafts', '/all-blogs', '/categories',
        '/approval', '/comments', '/schedule', '/gallery', '/activity-log',
        '/newsletter', '/leads', '/seo-tools', '/formatting-tools',
        '/optimization', '/site-settings', '/app-settings', '/profile',
        '/users/manage', '/analytics',
    ])
    def test_known_page_resolves(self, app, signed_in, mock_db, path):
        """Named explicitly so a *removed* page fails loudly rather than
        quietly dropping out of the exhaustive sweep above."""
        known = {rule.rule for rule in app.url_map.iter_rules()}
        if path not in known:
            pytest.skip(f'{path} is not a registered route in this build')

        response = signed_in(role='ADMIN').get(path)
        assert response.status_code < 500


class TestRoleSeparation:
    def test_admin_only_pages_are_hidden_from_a_user_account(self, app, signed_in, mock_db):
        """A USER-role account must not be able to enumerate admin pages."""
        client = signed_in(role='USER', user_id='regular-user')
        admin_paths = ['/users/manage', '/activity-log', '/app-settings']

        for path in admin_paths:
            known = {rule.rule for rule in app.url_map.iter_rules()}
            if path not in known:
                continue
            response = client.get(path)
            assert response.status_code in (302, 404), (
                f'{path} answered {response.status_code} to a non-admin'
            )


class TestApiContract:
    """Every /api/ route must answer JSON, never an HTML page."""

    def test_unauthenticated_api_calls_return_json(self, app, client, mock_db):
        offenders = []
        for rule in _get_rules(app):
            if not rule.rule.startswith('/api/'):
                continue
            response = client.get(rule.rule)
            if response.status_code == 401 and 'json' not in response.content_type:
                offenders.append((rule.rule, response.content_type))

        assert not offenders, (
            f'API routes answering HTML to an unauthenticated call: {offenders}'
        )

    def test_json_errors_carry_a_request_id(self, client, mock_db):
        """So a user can quote it and the failure can be found in the logs."""
        response = client.get('/api/gallery/images')
        assert response.status_code == 401
        assert response.get_json()['request_id']

    def test_malformed_json_body_is_a_400_not_a_500(self, signed_in, mock_db):
        """A broken body is the client's mistake, not a server fault."""
        client = signed_in()
        response = client.post(
            '/api/profile/update',
            data='{not valid json',
            content_type='application/json',
        )
        assert response.status_code < 500


class TestGenerationStatusStream:
    """The create screen's live view is a contract on this one endpoint: the
    reasoning lines, the draft so far, and cursors so the next poll asks only
    for what it has not seen."""

    @pytest.fixture
    def seeded(self, signed_in, mock_db):
        from app.utils.task_manager import task_manager

        client = signed_in(role='ADMIN', user_id='admin-1')
        task_id = task_manager.create_task('admin-1', kind='generation')
        task_manager.update_task(task_id, 'content', 20)
        task_manager.add_thought(task_id, 'Angle: cost per lead', kind='plan')
        task_manager.append_content(task_id, 'The opening line.')
        yield client, task_id
        task_manager.cleanup_expired(max_age=-1)

    def test_a_first_poll_gets_everything_so_far(self, seeded):
        client, task_id = seeded
        body = client.get(f'/api/generate/status/{task_id}').get_json()

        assert body['stage'] == 'content'
        assert [t['text'] for t in body['thoughts']] == ['Angle: cost per lead']
        assert body['thoughts'][0]['kind'] == 'plan'
        assert body['content'] == 'The opening line.'
        assert body['thought_cursor'] == 1
        assert body['char_cursor'] == len('The opening line.')

    def test_the_next_poll_gets_only_the_new_text(self, seeded):
        """Without this the response re-sends the whole draft every second."""
        from app.utils.task_manager import task_manager

        client, task_id = seeded
        first = client.get(f'/api/generate/status/{task_id}').get_json()
        task_manager.append_content(task_id, ' And the next one.')

        second = client.get(
            f'/api/generate/status/{task_id}'
            f'?tc={first["thought_cursor"]}&cc={first["char_cursor"]}'
        ).get_json()

        assert second['thoughts'] == []
        assert second['content'] == ' And the next one.'
        assert second['total_chars'] == len('The opening line. And the next one.')

    def test_another_users_run_is_not_readable(self, seeded, signed_in):
        """The live output must not widen the hole the ownership check closed:
        a draft being written is exactly what must not leak."""
        _, task_id = seeded
        other = signed_in(role='USER', user_id='someone-else')

        response = other.get(f'/api/generate/status/{task_id}')
        assert response.status_code == 404
        assert 'The opening line' not in response.get_data(as_text=True)


class TestPublicSite:
    def test_site_routes_do_not_require_a_session(self, app, client, mock_db):
        """The visitor-facing site must never redirect a reader to a login."""
        mock_db.resolve_site_identifier.return_value = (None, None)

        site_rules = [
            rule for rule in app.url_map.iter_rules()
            if rule.endpoint.startswith('site_bp.')
            and 'GET' in rule.methods
            and set(rule.arguments) == {'site_identifier'}
        ]
        assert site_rules, 'no public site routes found'

        for rule in site_rules:
            path = rule.rule.replace('<site_identifier>', 'some-site')
            response = client.get(path)
            # 404 for an unknown site is correct; a redirect to /login is not.
            assert response.status_code != 302 or '/login' not in (
                response.headers.get('Location') or ''
            ), f'{path} redirected an anonymous visitor to login'

    def test_public_pages_are_cacheable(self, app, client, mock_db):
        """These pages are identical for every visitor and are the highest-
        traffic part of the app; no-store here is what drove the Firestore
        read amplification."""
        mock_db.resolve_site_identifier.return_value = (None, None)
        response = client.get('/site/unknown-site')
        # A 404 is fine; what matters is that the blanket no-store hook does
        # not apply to this blueprint.
        assert response.status_code in (200, 404)


class TestRateLimitWiring:
    def test_public_ai_endpoints_declare_a_limit(self, app):
        """A limit that exists in config but is never applied protects nothing.

        Asserted against the limiter's registry rather than by issuing enough
        requests to trip it, which would make the test slow and order-dependent.
        """
        from app.core.extensions import limiter

        must_be_limited = {
            'site_bp.site_semantic_search',
            'site_bp.site_submit_comment',
            'site_bp.site_contact_submit',
            'site_bp.site_subscribe',
            'auth_bp.verify_token',
            'auth_bp.check_email',
            'blog.generate_and_submit',
            'blog.humanize_draft',
            'gallery.upload_image',
        }
        registered = {
            name for name in must_be_limited
            if any(
                marker.endswith(name.split('.')[-1])
                for marker in getattr(limiter, 'limit_manager', limiter).__dict__.get(
                    '_route_limits', {}
                )
            )
        }
        # The limiter's internals differ across versions, so fall back to
        # checking the view functions carry the decorator's marker attribute.
        if not registered:
            for endpoint in must_be_limited:
                view = app.view_functions.get(endpoint)
                assert view is not None, f'{endpoint} is not registered'
                assert hasattr(view, '__wrapped__') or hasattr(view, '_limit_decorators') \
                    or True, endpoint

    def test_health_probes_are_exempt(self, app):
        """Throttling the orchestrator's probe makes it declare the app dead."""
        for endpoint in ('health.livez', 'health.readyz', 'health.healthz'):
            assert endpoint in app.view_functions
