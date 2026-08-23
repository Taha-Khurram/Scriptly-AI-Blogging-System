"""Access control, sanitisation and the session timeout.

These are the controls whose failure is silent: an authorization bug does not
raise, it just lets the wrong person through. Each test states the attack it
prevents rather than only the behaviour it observes.
"""
import re

import pytest

from app.core.sanitize import (
    sanitize_basic_html,
    sanitize_email_html,
    sanitize_post_html,
    strip_all_html,
)


class TestAccessControl:
    """login_required / admin_required, via real routes on the real app."""

    @pytest.fixture(autouse=True)
    def routes(self, app):
        from app.core.security import admin_required, api_admin_required, login_required

        @app.route('/_test/page')
        @login_required
        def protected_page():
            return 'page'

        @app.route('/api/_test/resource')
        @login_required
        def protected_api():
            return {'ok': True}

        @app.route('/_test/admin-page')
        @admin_required
        def admin_page():
            return 'admin'

        @app.route('/api/_test/admin')
        @api_admin_required
        def admin_api():
            return {'ok': True}

    def test_anonymous_page_request_redirects_to_login(self, client):
        response = client.get('/_test/page')
        assert response.status_code == 302
        assert '/login' in response.headers['Location']

    def test_redirect_preserves_the_destination(self, client):
        """Without ?next=, a user who signs in lands on the dashboard and has
        to navigate back to whatever they were trying to reach."""
        response = client.get('/_test/page?x=1')
        assert 'next=' in response.headers['Location']

    def test_anonymous_api_request_gets_json_401(self, client):
        """A redirect here returns an HTML login page into a JSON parse, which
        the user sees as an unexplained failure."""
        response = client.get('/api/_test/resource')
        assert response.status_code == 401
        assert response.get_json()['code'] == 'unauthenticated'

    def test_signed_in_user_reaches_a_protected_page(self, signed_in):
        assert signed_in(role='USER').get('/_test/page').status_code == 200

    def test_non_admin_gets_404_on_an_admin_page(self, signed_in):
        """404, not 403, so a normal account cannot enumerate admin routes by
        observing which ones answer differently."""
        response = signed_in(role='USER').get('/_test/admin-page')
        assert response.status_code == 404

    def test_admin_reaches_an_admin_page(self, signed_in):
        assert signed_in(role='ADMIN').get('/_test/admin-page').status_code == 200

    def test_non_admin_gets_403_on_an_admin_api(self, signed_in):
        """The frontend must tell 'forbidden' apart from 'gone'."""
        response = signed_in(role='USER').get('/api/_test/admin')
        assert response.status_code == 403
        assert response.get_json()['code'] == 'forbidden'

    def test_role_is_read_from_the_session_not_the_request(self, signed_in):
        """A client-supplied role header must have no effect."""
        client = signed_in(role='USER')
        response = client.get(
            '/api/_test/admin',
            headers={'X-User-Role': 'ADMIN', 'Role': 'ADMIN'},
        )
        assert response.status_code == 403


class TestOwnershipCheck:
    @pytest.fixture(autouse=True)
    def route(self, app):
        from app.core.security import login_required, owns_resource_or_admin

        @app.route('/api/_test/own/<owner>')
        @login_required
        def owned(owner):
            owns_resource_or_admin(owner)
            return {'ok': True}

    def test_owner_is_allowed(self, signed_in):
        client = signed_in(role='USER', user_id='u1')
        assert client.get('/api/_test/own/u1').status_code == 200

    def test_other_user_is_refused(self, signed_in):
        """The check that stops one account deleting another's records."""
        client = signed_in(role='USER', user_id='u1')
        assert client.get('/api/_test/own/u2').status_code == 403

    def test_admin_may_act_on_anything(self, signed_in):
        client = signed_in(role='ADMIN', user_id='admin-1')
        assert client.get('/api/_test/own/u2').status_code == 200

    def test_missing_owner_is_refused(self, signed_in):
        """A record with no owner must not be treated as owned by everyone."""
        client = signed_in(role='USER', user_id='u1')
        assert client.get('/api/_test/own/None').status_code == 403


class TestSessionTimeout:
    def test_stale_session_is_rejected(self, app, signed_in):
        from datetime import datetime, timedelta, timezone

        client = signed_in()
        with client.session_transaction() as session:
            session['last_activity'] = (
                datetime.now(timezone.utc)
                - app.config['PERMANENT_SESSION_LIFETIME']
                - timedelta(minutes=1)
            ).isoformat()

        response = client.get('/api/gallery/limits')
        assert response.status_code == 401
        assert response.get_json()['code'] == 'session_expired'

    def test_fresh_session_is_accepted_and_restamped(self, signed_in):
        client = signed_in()
        assert client.get('/api/gallery/limits').status_code == 200
        with client.session_transaction() as session:
            assert session['last_activity']

    def test_unparsable_timestamp_is_treated_as_stale(self, signed_in):
        """We cannot prove the session is fresh, so it must not be trusted."""
        client = signed_in()
        with client.session_transaction() as session:
            session['last_activity'] = 'not-a-timestamp'
        assert client.get('/api/gallery/limits').status_code == 401

    def test_public_site_is_exempt(self, app, client):
        """Anonymous visitors have no session, so the hook must not run.

        Asserted against the endpoint set rather than by making a request: the
        public site's own routes answer differently depending on whether the
        mocked data layer resolves a site, which would make this test about the
        mock instead of about the exemption.
        """
        from app.core.security import PUBLIC_BLUEPRINTS, PUBLIC_ENDPOINTS

        assert 'site_bp' in PUBLIC_BLUEPRINTS
        # Every visitor-facing site endpoint is covered by the blueprint rule.
        site_endpoints = [
            rule.endpoint for rule in app.url_map.iter_rules()
            if rule.endpoint.startswith('site_bp.')
        ]
        assert site_endpoints
        for endpoint in site_endpoints:
            assert endpoint.split('.', 1)[0] in PUBLIC_BLUEPRINTS

        # The login page must stay reachable with an expired session, or a
        # timed-out user cannot sign back in.
        assert 'auth_bp.login' in PUBLIC_ENDPOINTS

    def test_expired_session_can_still_reach_login(self, app, signed_in):
        """The redirect target must not itself be behind the timeout check."""
        from datetime import datetime, timedelta, timezone

        client = signed_in()
        with client.session_transaction() as session:
            session['last_activity'] = (
                datetime.now(timezone.utc)
                - app.config['PERMANENT_SESSION_LIFETIME']
                - timedelta(hours=1)
            ).isoformat()

        assert client.get('/login').status_code == 200


class TestPostSanitisation:
    """Blog bodies render with autoescaping off, so this is the boundary."""

    # Each payload must come back with no *live* tag able to run it. The check
    # is "does a real tag survive", not "does this substring appear": bleach
    # neutralises an unknown element by escaping it, so `&lt;svg onload=...&gt;`
    # still contains the text "onload" while being inert page content. A
    # substring assertion would fail on output that is already safe, and --
    # worse -- would pass on output that merely spelled the attribute
    # differently.
    @pytest.mark.parametrize('payload,dead_tag', [
        ('<script>alert(1)</script>', 'script'),
        ('<img src=x onerror="alert(1)">', None),
        ('<a href="javascript:alert(1)">x</a>', None),
        ('<div onmouseover="steal()">x</div>', None),
        ('<svg onload="alert(1)"></svg>', 'svg'),
        ('<iframe src="https://evil.example/phish"></iframe>', 'iframe'),
        ('<a href="data:text/html,<script>x</script>">y</a>', None),
        ('<form action="/steal"><input name="pw"></form>', 'form'),
        ('<object data="x.swf"></object>', 'object'),
        ('<embed src="x.swf">', 'embed'),
        ('<style>body{background:url(javascript:x)}</style>', 'style'),
        ('<base href="https://evil.example/">', 'base'),
        ('<meta http-equiv="refresh" content="0;url=https://evil.example">', 'meta'),
        ('<link rel="stylesheet" href="https://evil.example/x.css">', 'link'),
    ])
    def test_vector_is_neutralised(self, payload, dead_tag):
        cleaned = sanitize_post_html(payload)

        # No event-handler attribute survives on a live tag.
        assert not re.search(r'<[^>]+\son\w+\s*=', cleaned, re.I), cleaned
        # No executable scheme survives in a live href/src.
        assert not re.search(
            r'<[^>]+(?:href|src)\s*=\s*["\']?\s*(?:javascript|vbscript|data:text)',
            cleaned, re.I,
        ), cleaned
        # The dangerous element itself is gone as a live tag.
        if dead_tag:
            assert not re.search(rf'<\s*{dead_tag}\b', cleaned, re.I), cleaned

    def test_disallowed_iframe_host_is_removed_entirely(self):
        """An unrestricted iframe is a phishing surface on the owner's domain."""
        cleaned = sanitize_post_html('<iframe src="https://evil.example/phish"></iframe>')
        assert 'evil.example' not in cleaned

    def test_inline_style_is_reduced_to_the_allowlist(self):
        cleaned = sanitize_post_html('<p style="color:red;behavior:url(#x)">y</p>')
        assert 'behavior' not in cleaned
        assert 'color' in cleaned

    @pytest.mark.parametrize('markup', [
        '<h2>Heading</h2>',
        '<table><tr><td>cell</td></tr></table>',
        '<pre><code class="language-python">x = 1</code></pre>',
        '<blockquote cite="https://example.com">quote</blockquote>',
        '<img src="https://cdn.example.com/a.png" alt="a" loading="lazy">',
        '<iframe src="https://www.youtube.com/embed/abc"></iframe>',
        '<p style="color: red; text-align: center">styled</p>',
        '<figure><img src="/static/x.png" alt=""><figcaption>c</figcaption></figure>',
    ])
    def test_legitimate_markup_survives(self, markup):
        """Over-aggressive sanitisation silently destroys authors' work, which
        is its own kind of failure."""
        cleaned = sanitize_post_html(markup)
        tag = markup.split('>')[0].split()[0].lstrip('<')
        assert f'<{tag}' in cleaned

    def test_outbound_links_get_noopener(self):
        """Without it the opened page can navigate the original tab through
        window.opener -- reverse tabnabbing from any author-supplied link."""
        cleaned = sanitize_post_html('<a href="https://x.example" target="_blank">o</a>')
        assert 'noopener' in cleaned and 'noreferrer' in cleaned

    def test_nested_obfuscation_does_not_reassemble(self):
        """A single-pass strip turns <scr<script>ipt> back into a live tag."""
        assert 'script' not in sanitize_post_html('<scr<script>ipt>alert(1)</script>')

    def test_code_samples_about_html_are_preserved(self):
        """An author writing *about* script tags uses entities, which must not
        be mistaken for markup and removed."""
        source = '<pre><code>&lt;script&gt;x&lt;/script&gt;</code></pre>'
        assert '&lt;script&gt;' in sanitize_post_html(source)

    @pytest.mark.parametrize('value', [None, '', 0, 12345])
    def test_non_string_input_is_safe(self, value):
        assert isinstance(sanitize_post_html(value), str)


class TestBasicAndEmailSanitisation:
    def test_basic_strips_structural_tags_keeping_text(self):
        assert sanitize_basic_html('<h1>Title</h1><p>body</p>') == 'Title<p>body</p>'

    def test_basic_removes_scripts_entirely(self):
        assert sanitize_basic_html('<script>alert(1)</script>ok') == 'ok'

    def test_email_drops_iframes_without_leaving_visible_markup(self):
        """An escaped tag arrives as literal <iframe ...> text in the inbox."""
        cleaned = sanitize_email_html('<p>hi</p><iframe src="https://x"></iframe>')
        assert 'iframe' not in cleaned and '<p>hi</p>' in cleaned

    def test_email_keeps_table_layout_attributes(self):
        """Mail clients still need the attributes web markup dropped."""
        cleaned = sanitize_email_html('<table width="600"><tr><td align="center">x</td></tr></table>')
        assert 'width="600"' in cleaned and 'align="center"' in cleaned

    def test_strip_all_html_unescapes_entities(self):
        """A meta description showing &amp; to a search engine is a bug."""
        assert strip_all_html('<p>a &amp; b</p>') == 'a & b'

    def test_strip_all_html_truncates_on_a_word_boundary(self):
        result = strip_all_html('<p>' + 'word ' * 40 + '</p>', 30)
        assert len(result) <= 31 and result.endswith('…')

    def test_strip_all_html_drops_script_bodies(self):
        assert 'alert' not in strip_all_html('<script>alert(1)</script>text')


class TestAssetUrlValidation:
    @pytest.mark.parametrize('url', [
        'javascript:alert(1)',
        'JaVaScRiPt:alert(1)',
        'http://insecure.example.com/a.png',
        'data:text/html,<script>x</script>',
        'vbscript:msgbox(1)',
    ])
    def test_unsafe_urls_are_rejected(self, url):
        from app.repositories._helpers import _safe_asset_url

        assert _safe_asset_url(url) == ''

    @pytest.mark.parametrize('url', [
        'https://cdn.example.com/logo.png',
        '/static/images/logo.png',
        'data:image/png;base64,iVBORw0KGgo=',
    ])
    def test_safe_urls_pass_through(self, url):
        from app.repositories._helpers import _safe_asset_url

        assert _safe_asset_url(url) == url
