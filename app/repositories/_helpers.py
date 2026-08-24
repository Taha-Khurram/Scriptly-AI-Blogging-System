"""Helpers shared by more than one repository mixin.

Kept in their own module rather than duplicated per mixin, and separate from
any one repository because sanitisation and URL validation are cross-cutting:
blog content and site settings both need them.
"""
import contextvars
import functools
from datetime import datetime, timezone

from app.core.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Projections
# ---------------------------------------------------------------------------
#
# A blog document carries the whole post: ``content`` (the rendered body and
# markdown source), ``embedding`` (the semantic-search vector), ``outline``,
# ``seo``, ``formatting`` and ``metadata``. Measured on this dataset, 25 blog
# documents are 1.9 MB, with the largest single document at 101 KB.
#
# Every list screen -- all-blogs, drafts, the approval queue, the dashboard
# cards, the published-post picker in site settings -- renders a title, an
# author, a category, a status and a date. Streaming the full document to
# render five fields is the Firestore equivalent of ``SELECT *``, and it is
# the single most expensive thing this application does: the same query with a
# projection applied measured 750-820 ms against 4.6-8.0 s unprojected, a 7x
# difference that is entirely wire time.
#
# ``Query.select()`` pushes the projection server-side, so the omitted fields
# are never serialised or transferred. The document id is always returned and
# does not need to be listed.
BLOG_LIST_FIELDS = (
    'title',
    'status',
    'category',
    'author',
    'author_id',
    'site_owner_id',
    'slug',
    'created_at',
    'updated_at',
)

# The approval queue additionally shows the schedule a writer asked for.
BLOG_QUEUE_FIELDS = BLOG_LIST_FIELDS + ('requested_schedule_at',)

# The RSS feed and the site's card grids: the body is genuinely needed (the feed
# can be configured to carry full content), the image is rendered, and
# everything else on the document is not. This is the projection to reach for
# when a caller needs the post *and* the post's text -- it still leaves behind
# the embedding vector, the outline, the seo map, the formatting map and the
# metadata map, which together are the bulk of a blog document.
BLOG_FEED_FIELDS = BLOG_LIST_FIELDS + ('content', 'cover_image')

# A hero/teaser card: title, image, category, date, and a link. No body at all.
BLOG_CARD_FIELDS = BLOG_LIST_FIELDS + ('cover_image',)

# A full public article page. This is the most-visited page on the public site,
# and it is the one place that genuinely needs the whole post -- so the
# projection is an exclusion list in spirit: everything site_post.html and
# site_base.html read, and nothing else.
#
# What it leaves behind is what matters. Measured across 21 published posts
# (1703 KB total): `embedding` is 51.1% of every blog document, `formatting`
# 6.6% and `outline` 1.2% -- 59% of each read, for three fields no template
# touches. `embedding` is the semantic-search vector; nothing on any request
# path reads it (``get_blogs_with_embeddings`` has no callers), so it is pure
# transfer cost on every read that does not exclude it.
BLOG_ARTICLE_FIELDS = BLOG_LIST_FIELDS + (
    'content',
    'cover_image',
    'excerpt',
    'tags',
    'seo',
    'seo_title',
    'seo_description',
    'author_avatar',
    'author_role',
    'old_slugs',
    'numeric_id',
)


def apply_projection(query, fields):
    """Return ``query`` restricted to ``fields``, or unchanged if none given.

    Centralised so a call site can pass ``fields=None`` to mean "the whole
    document" -- which the public site genuinely needs, since it renders the
    body -- without every caller repeating the conditional.
    """
    if not fields:
        return query
    return query.select(list(fields))


# ---------------------------------------------------------------------------
# Request-scoped memoisation
# ---------------------------------------------------------------------------

# The per-request memo store. A ContextVar rather than ``flask.g`` because the
# page routes fan their queries out across a ThreadPoolExecutor
# (``run_parallel_simple``), and ``g`` is bound to the app context of the
# thread that created it -- a worker thread cannot see it, so every parallel
# task would miss the cache and re-issue the round trip the memo exists to
# avoid. ``run_parallel_simple`` copies the calling context into each worker,
# and because the var holds a reference to one shared dict, a value written by
# the request thread is visible to every worker and vice versa.
#
# It must be reset per request (see :func:`init_request_cache`): left to
# persist, a worker thread reused by the next request would serve one user's
# team membership to another.
_request_cache = contextvars.ContextVar('repo_request_cache', default=None)


def init_request_cache(app):
    """Open a fresh memo store per request and drop it on teardown."""

    @app.before_request
    def _open_store():
        _request_cache.set({})

    @app.teardown_request
    def _close_store(exc=None):
        _request_cache.set(None)


# How long a moderation-style listing stays good. These screens filter, search
# and page entirely client-of-Firestore -- every interaction re-ran the same
# full scan -- so without a shared cache each click cost another round trip for
# a set the previous click had already fetched. Short, and invalidated
# explicitly by every method that writes to the collection, so a moderator's
# own action is reflected immediately rather than after the window.
LISTING_TTL_SECONDS = 30


def owner_listing(prefix, ttl=LISTING_TTL_SECONDS):
    """Cache a per-owner listing for the request *and* for a short window.

    Two layers, because they solve different problems:

    * the request-scoped memo collapses the several callers *within* one request
      (a stats row and a table that need the same rows),
    * the shared cache collapses the several *requests* a user generates by
      filtering, searching and paging through what is one underlying set.

    Invalidate with :func:`invalidate_owner_listing` from anything that writes.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(self, owner_id, *args, **kwargs):
            store = _request_cache.get()
            memo_key = (func.__qualname__, owner_id)
            if store is not None and memo_key in store:
                return store[memo_key]

            from app.utils.cache import cache

            cache_key = '%s:%s' % (prefix, owner_id)
            value = cache.get(cache_key)
            if value is None:
                value = func(self, owner_id, *args, **kwargs)
                # An empty list is a legitimate answer and worth caching; only
                # a None (which these functions never return) would not be.
                if value is not None:
                    cache.set(cache_key, value, ttl)
            if store is not None:
                store[memo_key] = value
            return value

        wrapper.cache_prefix = prefix
        return wrapper
    return decorator


def invalidate_owner_listing(prefix, owner_id):
    """Drop a cached per-owner listing after a write."""
    from app.utils.cache import cache

    cache.delete('%s:%s' % (prefix, owner_id))


def request_cached(key_fn):
    """Memoise a repository method for the lifetime of one request.

    Distinct from the shared :mod:`app.utils.cache`, which has a TTL and is
    visible across requests and workers. This is narrower and safer: it only
    ever collapses *repeated calls within a single request*, so it cannot serve
    stale data -- the value it returns was read during this same request.

    It exists because the composed repositories call each other, and a page
    route calling three of them re-derives the same lookup several times. The
    activity log measured three separate ``get_my_sub_users`` round trips in
    one request (once from the route, once inside ``get_activity_stats``, once
    inside ``get_all_activity_for_admin``) at ~550 ms each. Two of those three
    were pure waste, and no call site had to change to remove them.

    Two workers that miss simultaneously will both compute the value and one
    will overwrite the other -- the same cost as no cache, never a wrong
    answer, and avoided in practice by resolving a shared lookup once in the
    route before fanning out.

    Outside a request -- the scheduler, a CLI script, warm-up -- there is no
    store, so the wrapper simply calls through.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            store = _request_cache.get()
            if store is None:
                return func(self, *args, **kwargs)

            key = (func.__qualname__, key_fn(*args, **kwargs))
            if key in store:
                return store[key]

            result = func(self, *args, **kwargs)
            store[key] = result
            return result

        return wrapper
    return decorator


def _parse_filter_date(value, end_of_day=False):
    """Parse a ``YYYY-MM-DD`` filter bound into an aware UTC datetime.

    Filter bounds arrive from a query string as a bare date with no offset.
    Treating them as UTC is the only defensible reading, and returning an aware
    value keeps every comparison in this module on one side of the naive/aware
    divide.
    """
    try:
        parsed = datetime.strptime(value, '%Y-%m-%d')
    except (ValueError, TypeError):
        return None
    if end_of_day:
        parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
    return parsed.replace(tzinfo=timezone.utc)

def _safe_asset_url(url):
    """Accept only an https URL, a same-origin path, or a data: image.

    These values are written straight into ``src``/``href`` attributes on
    public pages. ``javascript:`` there is a stored XSS; plain ``http:`` is
    blocked as mixed content on an https site and simply renders as a broken
    image, which is worth rejecting at input rather than debugging later.
    An empty string is valid -- it means "no image set".
    """
    if not url:
        return ''
    lowered = url.strip().lower()
    if lowered.startswith(('https://', '/')):
        return url.strip()
    # data:image is how an inline favicon or small logo is legitimately stored.
    if lowered.startswith('data:image/'):
        return url.strip()
    logger.warning('Rejected unsafe asset URL in site settings: %s', url[:80])
    return ''

def _sanitize_blog_content(content):
    """Clean the HTML in a blog ``content`` map before it is stored.

    Blog bodies are rendered on the public site with Jinja autoescaping
    disabled, so whatever is stored here executes in every visitor's browser.
    Authors include ``USER``-role accounts whose posts an admin later
    publishes, which makes an unsanitised body a privilege-escalation path:
    inject a script into a draft, wait for it to be approved, and it runs on
    the owner's domain against every reader -- including the admin session
    that reviews it.

    Sanitising here rather than at render time means the stored value is
    already safe, so a template that forgets ``|safe`` handling, an API
    response, or an RSS feed all serve the same clean HTML. The Markdown
    ``body`` is left untouched: it is the author's source text, is never
    rendered unescaped, and is re-converted to HTML through this same path.
    """
    from app.core.sanitize import sanitize_post_html

    if not content:
        return content

    if isinstance(content, str):
        return sanitize_post_html(content)

    if not isinstance(content, dict):
        return content

    cleaned = dict(content)
    for key in ('html', 'toc_html'):
        if cleaned.get(key):
            cleaned[key] = sanitize_post_html(cleaned[key])
    return cleaned
