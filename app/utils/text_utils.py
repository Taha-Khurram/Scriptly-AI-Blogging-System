"""Text helpers for turning stored post content into display strings."""

import re
from html import unescape

# Fenced/indented code blocks and inline code — dropped whole, since a fragment
# of code reads as noise in a one-line excerpt.
_FENCED_CODE = re.compile(r'```.*?```', re.DOTALL)
_INLINE_CODE = re.compile(r'`([^`]*)`')

_HTML_TAG = re.compile(r'<[^>]+>')
_IMAGE = re.compile(r'!\[[^\]]*\]\([^)]*\)')
_LINK = re.compile(r'\[([^\]]*)\]\([^)]*\)')
_HEADING = re.compile(r'^\s{0,3}#{1,6}\s*', re.MULTILINE)
_BLOCKQUOTE = re.compile(r'^\s{0,3}>\s?', re.MULTILINE)
_LIST_MARKER = re.compile(r'^\s{0,3}(?:[-*+]|\d+[.)])\s+', re.MULTILINE)
_RULE = re.compile(r'^\s{0,3}(?:[-*_]\s?){3,}$', re.MULTILINE)
_EMPHASIS = re.compile(r'(\*{1,3}|_{1,3})(\S(?:.*?\S)?)\1', re.DOTALL)
_STRIKE = re.compile(r'~~(.*?)~~', re.DOTALL)
_TABLE_PIPE = re.compile(r'^\s*\|.*\|\s*$', re.MULTILINE)
_WHITESPACE = re.compile(r'\s+')


def strip_markdown(value):
    """Flatten Markdown (or HTML) content to a single line of readable prose.

    Excerpts are built from raw post bodies, so without this a card can end up
    showing literal '## Heading' and '**bold**' markers.
    """
    if not value:
        return ''

    text = str(value)

    text = _FENCED_CODE.sub(' ', text)
    text = _IMAGE.sub(' ', text)
    text = _LINK.sub(r'\1', text)
    text = _HTML_TAG.sub(' ', text)
    text = _TABLE_PIPE.sub(' ', text)
    text = _RULE.sub(' ', text)
    text = _HEADING.sub('', text)
    text = _BLOCKQUOTE.sub('', text)
    text = _LIST_MARKER.sub('', text)
    text = _STRIKE.sub(r'\1', text)
    text = _INLINE_CODE.sub(r'\1', text)

    # Emphasis can nest (***bold italic***), so unwrap until it settles.
    for _ in range(3):
        new_text = _EMPHASIS.sub(r'\2', text)
        if new_text == text:
            break
        text = new_text

    text = unescape(text)
    return _WHITESPACE.sub(' ', text).strip()


def excerpt(value, length=180, suffix='…'):
    """A clean, word-boundary-trimmed summary of post content."""
    text = strip_markdown(value)
    if len(text) <= length:
        return text

    clipped = text[:length]
    space = clipped.rfind(' ')
    if space > length * 0.6:
        clipped = clipped[:space]
    return clipped.rstrip(' ,;:.—-') + suffix
