"""Tests for the agent's web-search service.

Two things are being pinned down here, and only one of them is the happy path.

**The parsers.** Each provider returns a different shape, and the response shape
is the part of a third-party API most likely to change without notice. Parsing
is therefore tested against literal payloads rather than through a mock of the
whole service, so a provider changing a field name fails a specific test with a
specific name instead of surfacing as "the agent stopped citing sources".

**The failure modes, which are the point.** ``search`` never raises. A wrong
key, a rate limit, a timeout, a non-JSON body, a payload whose shape the parser
does not recognise -- all of them come back as a :class:`SearchResult` the agent
can talk about. A search that raised would end a conversation over something the
user should simply be offered a choice about.
"""
from __future__ import annotations

import json

import pytest
import requests

from app.services.search_service import (
    MAX_SNIPPET_CHARS, SearchService,
)


class FakeResponse:
    def __init__(self, payload=None, status_code=200, text=None):
        self._payload = payload
        self.status_code = status_code
        self._text = text

    def json(self):
        if self._text is not None:
            raise ValueError('not json')
        return self._payload


class FakeSession:
    """Captures the request and returns a canned response."""

    def __init__(self, response=None, raises=None):
        self.response = response
        self.raises = raises
        self.calls = []

    def _record(self, method, url, **kwargs):
        self.calls.append({'method': method, 'url': url, **kwargs})
        if self.raises:
            raise self.raises
        return self.response

    def post(self, url, **kwargs):
        return self._record('POST', url, **kwargs)

    def get(self, url, **kwargs):
        return self._record('GET', url, **kwargs)


@pytest.fixture(autouse=True)
def clean_cache():
    """Results are cached in the shared cache, which is a singleton."""
    from app.utils.cache import cache
    cache.clear()
    yield
    cache.clear()


def configured(provider='tavily', response=None, raises=None):
    service = SearchService()
    service.configure(provider=provider, api_key='k', max_results=5)
    service._session = FakeSession(response, raises)
    return service


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class TestConfiguration:

    def test_no_provider_means_unavailable_not_broken(self):
        service = SearchService()
        service.configure(provider='', api_key='')

        result = service.search('anything')

        assert service.is_available is False
        assert result.available is False
        assert result.items == []
        # No error, because nothing failed -- this deployment has no search.
        assert result.error is None

    def test_a_misspelled_provider_is_refused_not_ignored(self):
        """A typo must not present as an agent that quietly stopped citing."""
        service = SearchService()
        service.configure(provider='tavilly', api_key='k')
        assert service.is_available is False
        assert service.provider == 'none'

    def test_a_provider_without_a_key_is_disabled(self):
        service = SearchService()
        service.configure(provider='tavily', api_key='')
        assert service.is_available is False

    def test_the_result_ceiling_is_enforced(self):
        service = SearchService()
        service.configure(provider='tavily', api_key='k', max_results=500)
        assert service._max_results <= 10


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

class TestParsers:

    def test_tavily(self):
        service = configured('tavily', FakeResponse({'results': [
            {'title': 'A', 'url': 'https://a.example', 'content': 'about a'},
            {'title': 'No url', 'content': 'dropped'},
        ]}))

        result = service.search('q')

        # The entry without a URL is dropped: a source the reader cannot open
        # is not a source.
        assert result.items == [
            {'title': 'A', 'url': 'https://a.example', 'snippet': 'about a'},
        ]

    def test_serper(self):
        service = configured('serper', FakeResponse({'organic': [
            {'title': 'B', 'link': 'https://b.example', 'snippet': 'about b'},
        ]}))

        result = service.search('q')

        assert result.items[0]['url'] == 'https://b.example'
        assert result.items[0]['snippet'] == 'about b'

    def test_brave(self):
        service = configured('brave', FakeResponse({'web': {'results': [
            {'title': 'C', 'url': 'https://c.example', 'description': 'about c'},
        ]}}))

        result = service.search('q')

        assert result.items[0]['title'] == 'C'
        assert result.items[0]['snippet'] == 'about c'

    def test_an_empty_payload_is_no_results_not_a_failure(self):
        service = configured('tavily', FakeResponse({}))
        result = service.search('q')
        assert result.items == []
        assert result.error is None

    def test_a_snippet_is_clipped(self):
        """A provider returning a whole page must not spend the context window."""
        service = configured('tavily', FakeResponse({'results': [
            {'title': 'A', 'url': 'https://a.example', 'content': 'x' * 5000},
        ]}))

        result = service.search('q')

        assert len(result.items[0]['snippet']) <= MAX_SNIPPET_CHARS

    def test_whitespace_is_collapsed(self):
        service = configured('tavily', FakeResponse({'results': [
            {'title': 'A\n\n  B', 'url': 'https://a.example',
             'content': 'one\n\ttwo   three'},
        ]}))

        result = service.search('q')

        assert result.items[0]['title'] == 'A B'
        assert result.items[0]['snippet'] == 'one two three'

    def test_the_result_count_is_passed_and_capped(self):
        service = configured('tavily', FakeResponse({'results': []}))
        service.search('q', max_results=99)
        assert service._session.calls[0]['json']['max_results'] <= 10


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------

class TestFailures:
    """``search`` never raises. Every failure is something the agent can say."""

    @pytest.mark.parametrize('status,fragment', [
        (401, 'rejected the API key'),
        (403, 'rejected the API key'),
        (429, 'request limit'),
        (500, 'HTTP 500'),
    ])
    def test_http_errors_become_messages(self, status, fragment):
        service = configured('tavily', FakeResponse(status_code=status))

        result = service.search('q')

        assert result.items == []
        assert fragment in result.error
        # Available, but this call failed -- a different sentence from "there is
        # no search here", and the agent needs to tell them apart.
        assert result.available is True

    def test_a_timeout_becomes_a_message(self):
        service = configured('tavily', raises=requests.Timeout())
        result = service.search('q')
        assert 'did not respond' in result.error

    def test_a_connection_failure_becomes_a_message(self):
        service = configured('tavily', raises=requests.ConnectionError('refused'))
        result = service.search('q')
        assert 'Could not reach' in result.error

    def test_a_non_json_body_becomes_a_message(self):
        service = configured('tavily', FakeResponse(text='<html>oops</html>'))
        result = service.search('q')
        assert 'non-JSON' in result.error

    def test_an_unrecognised_payload_shape_does_not_raise(self):
        """The parsers index into a third-party shape that can change."""
        service = configured('tavily', FakeResponse({'results': 'not a list'}))
        result = service.search('q')
        assert result.items == []
        assert result.error

    def test_an_empty_query_is_refused_without_a_call(self):
        service = configured('tavily', FakeResponse({'results': []}))
        result = service.search('   ')
        assert result.error
        assert service._session.calls == []


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------

class TestCaching:

    def test_a_repeated_query_is_served_from_cache(self):
        """Two users researching the same topic should cost one upstream call."""
        service = configured('tavily', FakeResponse({'results': [
            {'title': 'A', 'url': 'https://a.example', 'content': 'about a'},
        ]}))

        first = service.search('same query')
        second = service.search('same query')

        assert len(service._session.calls) == 1
        assert first.cached is False
        assert second.cached is True
        assert second.items == first.items

    def test_the_cache_is_case_insensitive(self):
        service = configured('tavily', FakeResponse({'results': [
            {'title': 'A', 'url': 'https://a.example', 'content': 'a'},
        ]}))

        service.search('Same Query')
        service.search('same query')

        assert len(service._session.calls) == 1

    def test_a_different_result_count_is_a_different_entry(self):
        service = configured('tavily', FakeResponse({'results': [
            {'title': 'A', 'url': 'https://a.example', 'content': 'a'},
        ]}))

        service.search('q', max_results=3)
        service.search('q', max_results=8)

        assert len(service._session.calls) == 2

    def test_an_empty_result_is_not_cached(self):
        """So a transient nothing does not stick for fifteen minutes."""
        service = configured('tavily', FakeResponse({'results': []}))

        service.search('q')
        service.search('q')

        assert len(service._session.calls) == 2

    def test_the_cache_key_does_not_carry_user_text(self):
        """Queries go into a Redis key and a SQLite column; they are hashed."""
        service = configured('tavily', FakeResponse({'results': []}))
        key = service._cache_key('a query with spaces and \n newlines', 5)
        assert 'a query' not in key
        assert key.startswith('websearch:tavily:5:')


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

class TestStats:

    def test_stats_say_whether_research_is_live(self):
        service = configured('serper', FakeResponse({'organic': []}))
        stats = service.stats()
        assert stats['provider'] == 'serper'
        assert stats['available'] is True

    def test_stats_are_json_serialisable_for_healthz(self):
        service = SearchService()
        service.configure(provider='', api_key='')
        json.dumps(service.stats())
