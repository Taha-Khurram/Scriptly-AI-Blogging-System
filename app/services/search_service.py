"""Web search for the blog agent.

The one capability the agent needs that this application had no path to. Every
other tool it calls reads or writes something the app already owns -- a blog
document, a category, a formatted draft. Research is the exception: to write
about something current, the agent has to be able to look outside.

Why a service and not a call inside the tool
--------------------------------------------

Three reasons, all of which cost something later if this lives in the tool:

* **Providers are interchangeable and short-lived.** Search APIs change terms,
  pricing and response shapes far more often than model APIs do. Which one is
  configured is a deployment decision (``SEARCH_PROVIDER``), so a swap is an
  environment variable rather than an edit to agent code.
* **Search results are the most cacheable thing the agent touches.** Two users
  researching the same topic in the same hour should cost one upstream call.
  The cache lives here so no caller has to remember it.
* **The absence of a provider must be a first-class state, not a crash.** With
  no key configured the agent still has to work -- it just has to *say* that it
  is writing from its own knowledge rather than from sources. That is a
  :class:`SearchResult` with ``available=False``, not an exception, because a
  missing API key is a deployment fact and not a request failure.

What it deliberately does not do
--------------------------------

It does not fetch page bodies. A search API's snippets are enough to plan an
outline against, and fetching N arbitrary URLs from a server is a different
problem with a different threat model (SSRF, redirect chains, unbounded
bodies). If full-text research is wanted later it belongs behind its own
allowlisted fetcher, not bolted onto this.
"""
from __future__ import annotations

import hashlib
import logging
import threading

import requests

from app.core.errors import ExternalServiceError
from app.utils.cache import cache

logger = logging.getLogger(__name__)


class SearchError(ExternalServiceError):
    """A configured provider was reached and failed."""

    code = 'search_failed'
    message = 'The web search provider could not be reached. Please try again.'

    def __init__(self, message=None, **kwargs):
        kwargs.setdefault('service', 'search')
        super().__init__(message, **kwargs)


# Upper bound on results asked for, whatever a caller (or the model) requests.
# Snippets are what land in the prompt, so this is a token budget as much as a
# rate-limit courtesy: twenty results is roughly 3 KB of context for one
# research step.
MAX_RESULTS_CEILING = 10
DEFAULT_MAX_RESULTS = 5

# Snippets are trimmed at this before they ever reach a prompt. A provider that
# returns a whole page in `content` should not be able to spend the model's
# context on one result.
MAX_SNIPPET_CHARS = 400
MAX_TITLE_CHARS = 200

# Results for the same query are the same for far longer than this; the window
# is short because "the latest X" is the main reason the agent searches at all.
CACHE_TTL_SECONDS = 900

# A search sits in the middle of a turn the user is watching, so its deadline
# has to be short enough that a slow provider degrades to "no sources" rather
# than to a stalled conversation.
DEFAULT_TIMEOUT_SECONDS = 12


class SearchResult:
    """One search's outcome, in the shape the agent's tool layer returns.

    ``available`` and ``error`` are separate on purpose. Not configured and
    tried-and-failed lead to different sentences in the chat ("I don't have web
    search set up, so this is from my own knowledge" versus "the search
    provider is down, want me to write it anyway?"), and collapsing them into
    an empty list makes both come out as silence.
    """

    __slots__ = ('query', 'items', 'provider', 'available', 'error', 'cached')

    def __init__(self, query, items=None, *, provider='none', available=True,
                 error=None, cached=False):
        self.query = query
        self.items = items or []
        self.provider = provider
        self.available = available
        self.error = error
        self.cached = cached

    def to_dict(self):
        return {
            'query': self.query,
            'provider': self.provider,
            'available': self.available,
            'cached': self.cached,
            'error': self.error,
            'result_count': len(self.items),
            'results': self.items,
        }

    def __repr__(self):  # pragma: no cover - diagnostics only
        return (f'<SearchResult {self.provider} {len(self.items)} items '
                f'available={self.available}>')


def _clip(value, limit):
    text = value if isinstance(value, str) else ('' if value is None else str(value))
    text = ' '.join(text.split())
    return text[:limit]


class SearchService:
    """Pluggable web search with a shared cache and a hard deadline.

    Configured once from the app factory, like :data:`app.utils.cache.cache`
    and :data:`app.services.gemini_client.gemini`. Reconfigured, never
    replaced, because the agent's tool module imports the singleton at module
    scope.
    """

    #: Providers this service knows how to talk to. Keyed by the value of
    #: ``SEARCH_PROVIDER``; each entry is (endpoint, request builder, parser).
    PROVIDERS = ('tavily', 'serper', 'brave')

    def __init__(self):
        self._provider = None
        self._api_key = None
        self._timeout = DEFAULT_TIMEOUT_SECONDS
        self._max_results = DEFAULT_MAX_RESULTS
        self._session = None
        self._lock = threading.RLock()

    # --- Lifecycle --------------------------------------------------------

    def configure(self, *, provider=None, api_key=None,
                  timeout=DEFAULT_TIMEOUT_SECONDS,
                  max_results=DEFAULT_MAX_RESULTS):
        """Apply provider settings. Idempotent; call once at startup.

        An unknown provider name is refused rather than ignored: silently
        disabling search because ``SEARCH_PROVIDER=tavilly`` was misspelled
        would surface as an agent that quietly stopped citing sources, which is
        the hardest kind of failure to notice.
        """
        with self._lock:
            provider = (provider or '').strip().lower() or None

            if provider and provider not in self.PROVIDERS:
                logger.error(
                    'SEARCH_PROVIDER=%r is not one of %s; web search is '
                    'disabled until it is corrected.',
                    provider, ', '.join(self.PROVIDERS),
                )
                provider = None
            elif provider and not api_key:
                logger.warning(
                    'SEARCH_PROVIDER=%s is set but SEARCH_API_KEY is empty; '
                    'web search is disabled. The agent will write from its own '
                    'knowledge and say so.', provider,
                )
                provider = None

            self._provider = provider
            self._api_key = api_key or None
            self._timeout = timeout or DEFAULT_TIMEOUT_SECONDS
            self._max_results = max(1, min(int(max_results or DEFAULT_MAX_RESULTS),
                                           MAX_RESULTS_CEILING))
            # One pooled session per configuration. Providers are HTTPS, and a
            # fresh TLS handshake per search is most of a search's latency.
            self._session = requests.Session() if provider else None

            if provider:
                logger.info(
                    'Web search configured',
                    extra={'provider': provider, 'timeout_s': self._timeout,
                           'max_results': self._max_results},
                )
            else:
                logger.info(
                    'Web search is not configured; the research step will be '
                    'skipped and the agent will say it wrote from its own '
                    'knowledge.'
                )

    @property
    def is_available(self):
        return bool(self._provider and self._api_key)

    @property
    def provider(self):
        return self._provider or 'none'

    # --- Search -----------------------------------------------------------

    def search(self, query, *, max_results=None, use_cache=True):
        """Look ``query`` up and return a :class:`SearchResult`.

        Never raises. A provider failure comes back as ``available=True`` with
        an ``error`` set and no items, because the caller is a tool inside a
        conversation: the agent has to be told what happened so it can offer
        the user a choice, and an exception here would end the turn instead.
        """
        query = _clip(query, 300)
        if not query:
            return SearchResult('', provider=self.provider, available=self.is_available,
                                error='An empty query cannot be searched.')

        if not self.is_available:
            return SearchResult(query, provider='none', available=False)

        limit = max(1, min(int(max_results or self._max_results), MAX_RESULTS_CEILING))
        key = self._cache_key(query, limit)

        if use_cache:
            hit = cache.get(key)
            if hit is not None:
                return SearchResult(query, hit, provider=self._provider, cached=True)

        try:
            items = self._call_provider(query, limit)
        except SearchError as exc:
            # Logged as a warning, not an exception: an upstream 429 is an
            # expected operating condition for a metered API, and stack traces
            # for it drown the log that would show a real fault.
            logger.warning('Web search failed', extra={'provider': self._provider,
                                                       'error': str(exc)[:200]})
            return SearchResult(query, provider=self._provider, error=exc.message)
        except Exception:
            # The parsers index into a third-party response shape that can
            # change without notice. A KeyError in one of them must degrade the
            # research step, not end a conversation the user is mid-way through.
            logger.exception('Web search raised unexpectedly',
                             extra={'provider': self._provider})
            return SearchResult(
                query, provider=self._provider,
                error='The search provider returned something unusable.',
            )

        if items:
            cache.set(key, items, CACHE_TTL_SECONDS)
        return SearchResult(query, items, provider=self._provider)

    # --- Providers --------------------------------------------------------

    def _cache_key(self, query, limit):
        # Hashed rather than interpolated: a query is user text and goes into a
        # Redis key and a SQLite key column, neither of which should carry
        # arbitrary length or bytes.
        digest = hashlib.sha256(query.lower().encode('utf-8')).hexdigest()[:24]
        return f'websearch:{self._provider}:{limit}:{digest}'

    def _call_provider(self, query, limit):
        handler = getattr(self, f'_search_{self._provider}')
        try:
            response = handler(query, limit)
        except requests.Timeout as exc:
            raise SearchError(
                f'The search provider did not respond within {self._timeout}s.'
            ) from exc
        except requests.RequestException as exc:
            raise SearchError(f'Could not reach the search provider ({exc}).') from exc

        if response.status_code == 401 or response.status_code == 403:
            raise SearchError('The search provider rejected the API key.')
        if response.status_code == 429:
            raise SearchError('The search provider is at its request limit.')
        if response.status_code >= 400:
            raise SearchError(
                f'The search provider returned HTTP {response.status_code}.'
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise SearchError('The search provider returned a non-JSON body.') from exc

        parser = getattr(self, f'_parse_{self._provider}')
        return parser(payload)[:limit]

    # Tavily -- returns snippets already written for LLM consumption, which is
    # why it is the recommended default in .env.example.
    def _search_tavily(self, query, limit):
        return self._session.post(
            'https://api.tavily.com/search',
            json={
                'api_key': self._api_key,
                'query': query,
                'max_results': limit,
                'search_depth': 'basic',
                'include_answer': False,
            },
            timeout=self._timeout,
        )

    @staticmethod
    def _parse_tavily(payload):
        return [
            {
                'title': _clip(item.get('title'), MAX_TITLE_CHARS),
                'url': _clip(item.get('url'), 500),
                'snippet': _clip(item.get('content'), MAX_SNIPPET_CHARS),
            }
            for item in (payload.get('results') or [])
            if item.get('url')
        ]

    # Serper -- a Google SERP proxy. Cheapest per call of the three and the
    # closest to "what a person would see", at the cost of shorter snippets.
    def _search_serper(self, query, limit):
        return self._session.post(
            'https://google.serper.dev/search',
            headers={'X-API-KEY': self._api_key, 'Content-Type': 'application/json'},
            json={'q': query, 'num': limit},
            timeout=self._timeout,
        )

    @staticmethod
    def _parse_serper(payload):
        return [
            {
                'title': _clip(item.get('title'), MAX_TITLE_CHARS),
                'url': _clip(item.get('link'), 500),
                'snippet': _clip(item.get('snippet'), MAX_SNIPPET_CHARS),
            }
            for item in (payload.get('organic') or [])
            if item.get('link')
        ]

    # Brave -- independent index, useful where Google-derived results are
    # undesirable.
    def _search_brave(self, query, limit):
        return self._session.get(
            'https://api.search.brave.com/res/v1/web/search',
            headers={'X-Subscription-Token': self._api_key,
                     'Accept': 'application/json'},
            params={'q': query, 'count': limit},
            timeout=self._timeout,
        )

    @staticmethod
    def _parse_brave(payload):
        results = ((payload.get('web') or {}).get('results') or [])
        return [
            {
                'title': _clip(item.get('title'), MAX_TITLE_CHARS),
                'url': _clip(item.get('url'), 500),
                'snippet': _clip(item.get('description'), MAX_SNIPPET_CHARS),
            }
            for item in results
            if item.get('url')
        ]

    # --- Introspection ----------------------------------------------------

    def stats(self):
        """Reported by /healthz so a missing key is visible before a user finds it."""
        return {
            'provider': self.provider,
            'available': self.is_available,
            'timeout_s': self._timeout,
            'max_results': self._max_results,
        }


#: Module-level singleton, reconfigured (never replaced) by the app factory.
search = SearchService()
