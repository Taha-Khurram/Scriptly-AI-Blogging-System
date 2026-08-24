"""Helpers shared by more than one repository mixin.

Kept in their own module rather than duplicated per mixin, and separate from
any one repository because sanitisation and URL validation are cross-cutting:
blog content and site settings both need them.
"""
from datetime import datetime, timezone

from app.core.logging import get_logger

logger = get_logger(__name__)


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
