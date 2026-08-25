"""``search_web`` -- the agent's only window outside the application.

Every tool result in this package follows one convention, and this is the
simplest place to see it: a tool returns a **plain JSON-serialisable dict** that
goes straight back to the model as a ``function_response``, and it never raises
for a condition the conversation could recover from.

That second half is the part that is easy to get wrong. The instinct is to raise
on a failed search, because a failed search *is* a failure. But the caller is a
turn in a conversation, and the correct behaviour when research fails is for the
agent to say "I couldn't reach the web -- want me to write it from what I know?"
The user then decides. An exception takes that choice away and ends the turn with
an error banner instead.

So: ``ok`` is about whether the tool ran, not about whether it found anything.
An empty result set with ``ok: True`` is a perfectly good answer to a search for
something obscure, and the model has to be able to tell that apart from "the
search provider is down" and from "search is not configured here".
"""
from __future__ import annotations

from app.core.logging import get_logger

logger = get_logger(__name__)

# What one call may ask for. The model picks a number and models pick large
# numbers; the service clamps too, but clamping here is what makes the returned
# `max_results` honest about what was actually requested.
MAX_RESULTS = 8
DEFAULT_RESULTS = 5


def search_web(ctx, query=None, max_results=DEFAULT_RESULTS, **_ignored):
    """Search the web for current information on ``query``.

    ``**_ignored`` is on every tool in this package on purpose. Models invent
    plausible extra arguments -- ``region``, ``recency``, ``num``, ``lang`` --
    and a ``TypeError`` from a hallucinated keyword would fail a turn over
    something the tool could simply have disregarded. Unknown arguments are
    dropped and the call proceeds; a *missing required* argument is still an
    error, because that one changes what the call means.
    """
    query = (query or '').strip()
    if not query:
        return {
            'ok': False,
            'error': 'missing_query',
            'message': 'A search needs a query. Say what to search for.',
        }

    limit = _as_int(max_results, DEFAULT_RESULTS, 1, MAX_RESULTS)

    if ctx.search is None:
        # No service on the context at all. Distinct from "configured with no
        # key": this means the context was built without one, which is a wiring
        # mistake rather than a deployment choice, so it is logged.
        logger.warning('search_web called with no search service on the context')
        return {
            'ok': True,
            'available': False,
            'results': [],
            'message': (
                'Web search is not available in this deployment. Tell the user '
                'you are working from your own knowledge rather than live '
                'sources, then continue.'
            ),
        }

    ctx.emit('status', stage='searching', label=f'Searching the web for "{query[:60]}"')

    result = ctx.search.search(query, max_results=limit)

    if not result.available:
        return {
            'ok': True,
            'available': False,
            'results': [],
            'message': (
                'Web search is not configured on this deployment, so there are '
                'no live sources. Say so plainly in one short sentence, then '
                'continue from your own knowledge — and do not invent '
                'citations, URLs or statistics to fill the gap.'
            ),
        }

    if result.error:
        return {
            'ok': True,
            'available': True,
            'failed': True,
            'results': [],
            'error': 'search_failed',
            'message': (
                f'The search provider failed: {result.error} Tell the user, and '
                'ask whether to continue without research or try again. Do not '
                'invent sources.'
            ),
        }

    items = result.items or []

    if items:
        # A card, so the sources are visible in the transcript rather than only
        # inside a tool result the user never sees. Research the user cannot
        # inspect is research they have to take on faith.
        ctx.add_card('sources', {
            'query': result.query,
            'provider': result.provider,
            'cached': result.cached,
            'items': items,
        })

    return {
        'ok': True,
        'available': True,
        'query': result.query,
        'provider': result.provider,
        'cached': result.cached,
        'result_count': len(items),
        'results': items,
        'message': (
            f'{len(items)} result(s). Use these for specifics and cite them in '
            "the outline's `sources`. Do not cite anything not listed here."
            if items else
            'No results. Say so, and either try a different query (you have a '
            'limited number of searches this turn) or continue without research.'
        ),
    }


def _as_int(value, default, low, high):
    """Coerce a model-supplied number into range.

    Models pass ``"5"``, ``5.0`` and ``"five"`` for the same argument. The first
    two are obviously intended; the third falls back to the default rather than
    failing the call, because the request was still clear.
    """
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(low, min(number, high))
