"""The public unsubscribe endpoint.

Given its own module because it is the one place in the application where an
anonymous request renders HTML built from its own input, and because it had four
distinct defects at once -- three security, one that made it entirely
non-functional. Regression tests for each.
"""
from html.parser import HTMLParser

import pytest


class _TagCollector(HTMLParser):
    """Records the elements and event-handler attributes a browser would see.

    Parsing rather than pattern-matching, because a regex over the raw body
    cannot tell a live tag from an escaped one. The safely-rendered payload
    ``value="&lt;img src=x onerror=alert(1)&gt;"`` contains the characters
    ``onerror=`` inside an attribute *value*, so any substring or
    ``<[^>]+ on\\w+=`` check reports a false positive on output that is already
    correct -- and would just as happily pass on a real injection spelled a
    little differently. What a browser does is parse; so does this.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.event_handlers = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        for name, _value in attrs:
            if name and name.lower().startswith('on'):
                self.event_handlers.append((tag, name))


def parse_html(body):
    collector = _TagCollector()
    collector.feed(body)
    return collector


# Elements the unsubscribe template itself renders. Anything else in the parsed
# output came from the request, which is the definition of an injection here.
TEMPLATE_TAGS = frozenset({
    'html', 'head', 'meta', 'title', 'style', 'body', 'main', 'div',
    'h1', 'p', 'form', 'input', 'button',
})


XSS_PAYLOADS = [
    '"><script>alert(1)</script>',
    "'><img src=x onerror=alert(1)>",
    '</strong><script>alert(document.cookie)</script>',
    'javascript:alert(1)',
    '"><svg onload=alert(1)>',
    '{{7*7}}',                       # template injection, not just HTML
    '${7*7}',
]


class TestReflectedXss:
    """The parameter is rendered back to the visitor, so this is the boundary."""

    @pytest.mark.parametrize('payload', XSS_PAYLOADS)
    def test_payload_introduces_no_element_or_handler(self, client, mock_db, payload):
        """The payload must not become markup.

        Checked by parsing the response the way a browser would, so escaped
        output -- which still contains the *characters* of a tag -- is correctly
        read as text.
        """
        response = client.get('/unsubscribe', query_string={'email': payload})
        assert response.status_code == 200

        parsed = parse_html(response.get_data(as_text=True))

        injected = set(parsed.tags) - TEMPLATE_TAGS
        assert not injected, f'payload introduced elements: {injected}'
        assert not parsed.event_handlers, (
            f'payload introduced handlers: {parsed.event_handlers}'
        )

    def test_template_expressions_are_not_evaluated(self, client, mock_db):
        """Jinja renders the value as data; it must never be treated as source."""
        response = client.get('/unsubscribe', query_string={'email': '{{7*7}}'})
        body = response.get_data(as_text=True)
        assert '49' not in body

    def test_owner_parameter_is_also_escaped(self, client, mock_db):
        """It is rendered into a hidden input, which is just as exploitable."""
        response = client.get(
            '/unsubscribe',
            query_string={'email': 'a@b.com', 'owner': '"><script>alert(1)</script>'},
        )
        assert 'script' not in parse_html(response.get_data(as_text=True)).tags


class TestRequestShapes:
    """`(request.json or {})` raised UnsupportedMediaType on anything that was
    not JSON, so the link *and* its own confirm button both returned 415."""

    def test_bare_get_renders_the_confirmation_page(self, client, mock_db):
        response = client.get('/unsubscribe')
        assert response.status_code == 200
        assert 'Unsubscribe' in response.get_data(as_text=True)

    def test_get_with_an_address_shows_it(self, client, mock_db):
        response = client.get('/unsubscribe', query_string={'email': 'reader@example.com'})
        assert 'reader@example.com' in response.get_data(as_text=True)

    def test_form_encoded_post_is_accepted(self, client, mock_db):
        """This is what the page's own confirm button sends."""
        mock_db.unsubscribe_newsletter.return_value = True
        response = client.post(
            '/unsubscribe',
            data={'email': 'reader@example.com', 'owner': 'owner-1'},
        )
        assert response.status_code == 200
        mock_db.unsubscribe_newsletter.assert_called_once()

    def test_json_post_is_accepted(self, client, mock_db):
        mock_db.unsubscribe_newsletter.return_value = True
        response = client.post(
            '/unsubscribe', json={'email': 'reader@example.com', 'owner': 'owner-1'}
        )
        assert response.status_code == 200

    def test_malformed_address_is_refused(self, client, mock_db):
        response = client.post('/unsubscribe', data={'email': 'not-an-address'})
        assert response.status_code == 400
        mock_db.unsubscribe_newsletter.assert_not_called()

    def test_missing_address_is_refused(self, client, mock_db):
        assert client.post('/unsubscribe', data={}).status_code == 400


class TestFailureHandling:
    def test_upstream_failure_leaks_nothing(self, client, mock_db):
        """The original returned `f'Error: {e}'` to an anonymous caller."""
        secret = 'gRPC failed: /etc/secrets/serviceAccount.json'
        mock_db.unsubscribe_newsletter.side_effect = RuntimeError(secret)

        response = client.post(
            '/unsubscribe', data={'email': 'a@example.com', 'owner': 'o1'}
        )
        body = response.get_data(as_text=True)

        assert response.status_code == 500
        assert 'gRPC' not in body
        assert 'serviceAccount' not in body

    def test_unknown_address_is_indistinguishable_from_success(self, client, mock_db):
        """Otherwise this open endpoint is a subscriber-enumeration oracle.

        The outcome the visitor cares about -- no more email -- holds either
        way, so there is nothing lost by refusing to confirm membership.
        """
        mock_db.unsubscribe_newsletter.return_value = True
        found = client.post('/unsubscribe', data={'email': 'known@example.com', 'owner': 'o1'})

        mock_db.unsubscribe_newsletter.return_value = False
        missing = client.post('/unsubscribe', data={'email': 'unknown@example.com', 'owner': 'o1'})

        assert found.status_code == missing.status_code == 200
        assert found.get_data(as_text=True) == missing.get_data(as_text=True)


class TestAccessibilityOfTheEndpoint:
    def test_requires_no_session(self, client, mock_db):
        """A recipient must be able to unsubscribe without an account."""
        assert client.get('/unsubscribe').status_code == 200

    def test_is_excluded_from_search_indexing(self, client, mock_db):
        body = client.get('/unsubscribe').get_data(as_text=True)
        assert 'noindex' in body

    def test_is_exempt_from_csrf(self):
        """No session means no ambient authority for a token to protect -- and
        a token requirement would simply break the link."""
        from app.core.extensions import CSRF_EXEMPT_ENDPOINTS

        assert 'newsletter.unsubscribe' in CSRF_EXEMPT_ENDPOINTS
