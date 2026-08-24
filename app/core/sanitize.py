"""HTML sanitisation for content that is rendered unescaped.

Three templates bypass Jinja autoescaping with ``|safe``: the public post body,
the site About page, and the legal pages. The content behind them is written by
authenticated authors -- including ``USER``-role accounts whose posts an admin
later publishes -- and then served on the site owner's public domain to every
visitor. Without sanitisation that is a stored XSS: a ``<script>`` in a draft
executes in every reader's browser, with access to the site's origin and to any
admin session that views the post.

Sanitising at *write* time rather than render time is deliberate:

* it runs once per save instead of once per page view, so the public site pays
  nothing for it under load;
* what is stored is what is served, so there is no path where a template
  forgets the filter and the raw payload escapes;
* an author sees the cleaned result immediately in the editor and can tell
  their markup was altered, instead of discovering it after publishing.

Two policies, because the trust levels genuinely differ. ``POST_ALLOWED_TAGS``
is generous -- blog bodies legitimately need tables, figures, code blocks and
embedded iframes for video. ``BASIC_ALLOWED_TAGS`` covers short prose fields
where a heading or a table would be a formatting mistake anyway.
"""
from __future__ import annotations

import logging
import re

import bleach
from bleach.css_sanitizer import CSSSanitizer

logger = logging.getLogger(__name__)

# --- Rich content (blog bodies) -----------------------------------------
POST_ALLOWED_TAGS = frozenset({
    'p', 'br', 'hr', 'div', 'span', 'section', 'article',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'strong', 'b', 'em', 'i', 'u', 's', 'strike', 'del', 'ins', 'mark',
    'sub', 'sup', 'small', 'abbr', 'cite', 'q', 'blockquote',
    'ul', 'ol', 'li', 'dl', 'dt', 'dd',
    'a', 'img', 'figure', 'figcaption', 'picture', 'source',
    'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption', 'colgroup', 'col',
    'pre', 'code', 'kbd', 'samp', 'var',
    'details', 'summary', 'time', 'address',
    'iframe',   # video embeds; src is restricted to an allowlist below
})

POST_ALLOWED_ATTRIBUTES = {
    '*': ['class', 'id', 'title', 'dir', 'lang', 'style'],
    'a': ['href', 'target', 'rel', 'download'],
    'img': ['src', 'alt', 'width', 'height', 'loading', 'decoding', 'srcset', 'sizes'],
    'source': ['src', 'srcset', 'type', 'media', 'sizes'],
    'iframe': ['src', 'width', 'height', 'allow', 'allowfullscreen',
               'frameborder', 'loading', 'title'],
    'th': ['colspan', 'rowspan', 'scope', 'abbr'],
    'td': ['colspan', 'rowspan', 'headers'],
    'col': ['span'],
    'colgroup': ['span'],
    'ol': ['start', 'reversed', 'type'],
    'li': ['value'],
    'time': ['datetime'],
    'code': ['data-language'],
    'pre': ['data-language'],
    'details': ['open'],
    'blockquote': ['cite'],
    'q': ['cite'],
}

# --- Short prose fields (About, legal pages, comment display text) ------
BASIC_ALLOWED_TAGS = frozenset({
    'p', 'br', 'strong', 'b', 'em', 'i', 'u', 'a',
    'ul', 'ol', 'li', 'blockquote', 'code', 'span',
    'h2', 'h3', 'h4',
})

BASIC_ALLOWED_ATTRIBUTES = {
    '*': ['class'],
    'a': ['href', 'target', 'rel'],
}

# Only these schemes may appear in an href/src. Excluding ``javascript:`` is
# the point; ``data:`` is excluded too because ``data:text/html`` in an iframe
# or anchor is a script-execution vector in some browsers.
ALLOWED_PROTOCOLS = frozenset({'http', 'https', 'mailto', 'tel'})

# CSS properties an author may set inline. A style attribute is where
# ``expression()`` and ``url(javascript:...)`` historically lived, so the
# vocabulary is an allowlist rather than a blocklist.
ALLOWED_CSS_PROPERTIES = [
    'color', 'background-color', 'font-size', 'font-weight', 'font-style',
    'font-family', 'text-align', 'text-decoration', 'text-transform',
    'line-height', 'letter-spacing',
    'margin', 'margin-top', 'margin-bottom', 'margin-left', 'margin-right',
    'padding', 'padding-top', 'padding-bottom', 'padding-left', 'padding-right',
    'border', 'border-radius', 'border-color', 'border-width', 'border-style',
    'width', 'height', 'max-width', 'max-height', 'min-width',
    'display', 'float', 'clear', 'vertical-align', 'white-space',
    'list-style-type', 'opacity',
]

# Hosts permitted as an ``<iframe src>``. An unrestricted iframe is a
# clickjacking and phishing surface -- an author could frame a credential
# prompt onto their own published page.
ALLOWED_IFRAME_HOSTS = (
    'www.youtube.com', 'youtube.com', 'www.youtube-nocookie.com',
    'player.vimeo.com', 'w.soundcloud.com', 'open.spotify.com',
    'codepen.io', 'gist.github.com', 'www.google.com',
)

_css_sanitizer = CSSSanitizer(allowed_css_properties=ALLOWED_CSS_PROPERTIES)

_IFRAME_SRC_RE = re.compile(r'<iframe\b[^>]*?\bsrc\s*=\s*["\']([^"\']*)["\']', re.I)
_IFRAME_TAG_RE = re.compile(r'<iframe\b[^>]*>.*?</iframe>|<iframe\b[^>]*/?>', re.I | re.S)

# Elements whose *contents* are raw text rather than markup. bleach removes the
# tags but keeps what was between them, so a stripped <script> leaves its body
# behind as visible (inert) text -- and an escaped one leaves the reader looking
# at raw markup. Neither is acceptable in published output, so both the tag and
# its contents are dropped before bleach runs. Genuine code samples are
# unaffected: inside <pre><code> they are already entity-escaped, so there is
# no literal "<script" for this to match.
_RAW_TEXT_ELEMENT_RE = re.compile(
    r'<\s*(script|style|noscript|template|xmp|plaintext)\b[^>]*>.*?'
    r'<\s*/\s*\1\s*>'
    r'|<\s*(script|style|noscript|template)\b[^>]*/?>',
    re.I | re.S,
)


def _drop_raw_text_elements(html):
    """Remove script/style-family elements together with their contents."""
    previous = None
    # Loop because a crafted payload can nest one inside another
    # ("<scr<script>ipt>"), where a single pass would reassemble a live tag.
    while previous != html:
        previous = html
        html = _RAW_TEXT_ELEMENT_RE.sub('', html)
    return html


def _iframe_host_allowed(src):
    from urllib.parse import urlparse
    try:
        parsed = urlparse(src)
    except ValueError:
        return False
    if parsed.scheme not in ('http', 'https', ''):
        return False
    return parsed.netloc.lower() in ALLOWED_IFRAME_HOSTS


def _strip_disallowed_iframes(html):
    """Remove iframes whose src is not on the host allowlist.

    Done as a pass over the markup because bleach's attribute filters can
    decide whether an attribute is allowed, but cannot drop the whole element
    based on that attribute's value.
    """
    def replace(match):
        tag = match.group(0)
        src_match = _IFRAME_SRC_RE.search(tag)
        src = src_match.group(1) if src_match else ''
        if src and _iframe_host_allowed(src):
            return tag
        logger.info('Stripped iframe with disallowed src: %s', src[:120] or '(none)')
        return ''
    return _IFRAME_TAG_RE.sub(replace, html)


def _harden_links(html):
    """Give every ``target="_blank"`` anchor a safe ``rel``.

    Without ``noopener`` the opened page can reach back through
    ``window.opener`` and navigate the original tab -- a reverse-tabnabbing
    phishing route from any author-supplied outbound link.
    """
    def fix(match):
        tag = match.group(0)
        if 'target' not in tag.lower():
            return tag
        if re.search(r'\brel\s*=', tag, re.I):
            return re.sub(
                r'\brel\s*=\s*["\']([^"\']*)["\']',
                lambda m: f'rel="{_merge_rel(m.group(1))}"',
                tag, count=1, flags=re.I,
            )
        return tag[:-1].rstrip() + ' rel="noopener noreferrer">'
    return re.sub(r'<a\b[^>]*>', fix, html, flags=re.I)


def _merge_rel(existing):
    tokens = set((existing or '').lower().split())
    tokens.update({'noopener', 'noreferrer'})
    return ' '.join(sorted(tokens))


def sanitize_post_html(html):
    """Clean a blog body for unescaped rendering on the public site.

    Returns ``''`` for empty input. Escapes -- rather than strips -- unknown
    tags, so an author who types ``<mytag>`` in prose still sees it in the
    output instead of having their sentence silently eaten.
    """
    if not html:
        return ''
    if not isinstance(html, str):
        html = str(html)

    cleaned = _drop_raw_text_elements(html)
    cleaned = _strip_disallowed_iframes(cleaned)
    cleaned = bleach.clean(
        cleaned,
        tags=POST_ALLOWED_TAGS,
        attributes=POST_ALLOWED_ATTRIBUTES,
        protocols=ALLOWED_PROTOCOLS,
        css_sanitizer=_css_sanitizer,
        strip=False,
        strip_comments=True,
    )
    return _harden_links(cleaned)


def sanitize_basic_html(html):
    """Clean a short prose field (About, legal, moderated comment text).

    Unlike :func:`sanitize_post_html` this *strips* disallowed tags instead of
    escaping them, keeping their text. In a rich body, escaping is the better
    default -- an author who typed ``<mytag>`` as prose wants to see it. In a
    short field the disallowed tags are structural ones the editor produced
    (an ``<h1>``, a layout ``<table>``), and escaping them would show the
    reader raw markup where a sentence should be.
    """
    if not html:
        return ''
    if not isinstance(html, str):
        html = str(html)

    cleaned = bleach.clean(
        _drop_raw_text_elements(html),
        tags=BASIC_ALLOWED_TAGS,
        attributes=BASIC_ALLOWED_ATTRIBUTES,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,
        strip_comments=True,
    )
    return _harden_links(cleaned)


def strip_all_html(text, max_length=None):
    """Plain text with every tag removed. For meta descriptions and previews.

    Entities are unescaped after stripping so ``&amp;`` reads as ``&`` in a
    ``<meta>`` description rather than showing the entity to a search engine.
    """
    if not text:
        return ''
    plain = bleach.clean(
        _drop_raw_text_elements(str(text)), tags=set(), attributes={}, strip=True
    )
    import html as html_module
    plain = html_module.unescape(plain)
    plain = re.sub(r'\s+', ' ', plain).strip()
    if max_length and len(plain) > max_length:
        plain = plain[:max_length].rsplit(' ', 1)[0] + '…'
    return plain


def sanitize_email_html(html):
    """Clean newsletter HTML before it is sent.

    Same allowlist as a post body minus iframes -- no mail client renders them,
    and their presence in an outbound message is a spam-filter signal. They are
    removed outright rather than escaped, because an escaped tag would arrive as
    visible ``<iframe ...>`` text in the recipient's inbox.
    """
    if not html:
        return ''
    html = _drop_raw_text_elements(_IFRAME_TAG_RE.sub('', str(html)))
    tags = set(POST_ALLOWED_TAGS) - {'iframe'}
    attributes = dict(POST_ALLOWED_ATTRIBUTES)
    attributes.pop('iframe', None)
    # Email HTML still relies on table layout attributes that modern web markup
    # has dropped, so they are permitted here and nowhere else.
    attributes['table'] = ['width', 'cellpadding', 'cellspacing', 'border', 'align', 'bgcolor']
    attributes['td'] = attributes.get('td', []) + ['width', 'height', 'align', 'valign', 'bgcolor']
    attributes['tr'] = ['align', 'valign', 'bgcolor']
    return bleach.clean(
        str(html),
        tags=tags,
        attributes=attributes,
        protocols=ALLOWED_PROTOCOLS,
        css_sanitizer=_css_sanitizer,
        strip=False,
        strip_comments=True,
    )
