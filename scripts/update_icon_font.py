#!/usr/bin/env python3
"""
Update the self-hosted Material Symbols icon font.

The nav icon font is subset to exactly the glyphs the app uses — 5KB instead of
the ~4MB full variable font. The trade-off is that an icon added to a template
is not in the font until this script regenerates it, and an icon the font is
missing renders as its raw ligature name ("edit_square") in the UI.

So: run this whenever you add or remove a `material-symbols-outlined` icon.

Usage:
    python scripts/update_icon_font.py            # scan, fetch, write
    python scripts/update_icon_font.py --check    # fail if the font is stale

--check is the CI-friendly form: it reports missing glyphs without writing.
"""

import argparse
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FONT_PATH = ROOT / 'app' / 'static' / 'fonts' / 'material-symbols-outlined.woff2'
MANIFEST_PATH = FONT_PATH.with_suffix('.txt')

SCAN_DIRS = [ROOT / 'app' / 'templates', ROOT / 'app' / 'static' / 'js', ROOT / 'app' / 'static' / 'css']
SCAN_SUFFIXES = {'.html', '.js', '.css'}

# <i class="material-symbols-outlined icon-inline" aria-hidden="true">name</i>
# Anything up to the tag's '>' can sit between the class and the ligature --
# more classes, aria-hidden, a style attribute -- so match to the ">" rather
# than to the closing quote of the class attribute.
RE_MARKUP = re.compile(r'material-symbols-outlined[^>]*>\s*([a-z0-9_]+)')
# icon.textContent = <expr>;  — take every string literal in the expression, so
# both arms of `cond ? 'light_mode' : 'dark_mode'` are picked up. Non-icons in
# the expression (a comparison against 'dark', say) are dropped by the
# codepoints check below rather than by guessing here.
RE_JS_ASSIGN = re.compile(r'textContent\s*=\s*([^;\n]+)')
RE_JS_STRING = re.compile(r'[\'"]([a-z0-9_]+)[\'"]')
# el('i', 'material-symbols-outlined icon-inline', 'chevron_right'), and the
# icon(...) helper that sits next to el() in analytics.js. Both end at the
# call's closing paren, so there is no need to reason about quoting.
RE_JS_EL = re.compile(r'material-symbols-outlined[^,]*,\s*([^)]+)\)')
RE_JS_ICON_CALL = re.compile(r'\bicon\(\s*([^)]+)\)')
# content: 'icon_name'  inside a rule that sets the Material Symbols family
RE_CSS_CONTENT = re.compile(r"content:\s*'([a-z0-9_]+)'")

# FILL is a range, not a fixed 0: the `-fill` cuts the app used to get from
# Bootstrap Icons are now the same glyph drawn with 'FILL' 1, which only a
# variable font can do. The other axes stay pinned -- a fully variable
# subset costs several times the size for nothing we actually vary.
CSS_URL = (
    'https://fonts.googleapis.com/css2'
    '?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@24,300,0..1,0'
    '&icon_names={names}&display=block'
)
# The authoritative name list. Google's CSS endpoint answers 200 with an empty
# font for names that do not exist, so a typo would otherwise ship silently and
# surface as raw ligature text in the UI. Validate before building.
CODEPOINTS_URL = (
    'https://raw.githubusercontent.com/google/material-design-icons/master/'
    'variablefont/MaterialSymbolsOutlined%5BFILL%2CGRAD%2Copsz%2Cwght%5D.codepoints'
)
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')


# Ligatures picked at runtime from data the scanner cannot follow: a lookup
# table keyed by record type, a severity ladder, a delta direction, a name
# handed to a helper. Listed here because a glyph missing from the subset
# renders as its own name in the UI, and nothing else would catch it.
EXTRA_ICONS = {
    # activity -- TYPE_ICON, keyed on the record type
    'article', 'person', 'chat_bubble', 'settings', 'mail', 'label', 'monitoring',
    # analytics -- delta direction, the KPI tuples, the live/offline pill
    'north_east', 'south_east', 'remove', 'visibility', 'ads_click', 'group',
    'sensors', 'block', 'chevron_right', 'description', 'alt_route',
    # seo-tools -- issue severity ladder, and the recommendation bullet
    'report', 'warning', 'info', 'arrow_right_alt',
    # menu and empty-state helpers that take a ligature as an argument
    'visibility_off', 'undo', 'delete', 'drafts', 'done_all', 'manage_accounts',
    'link', 'send', 'smart_toy', 'error', 'search', 'post_add', 'check_circle',
    'campaign', 'lightbulb', 'bookmark',
    # app.js -- toast type
    'cancel',
    # site_settings -- panel_head / nav_item icon arguments
    'palette', 'web', 'title', 'verified_user', 'share', 'public', 'table',
    'star', 'subject', 'aspect_ratio', 'translate', 'calendar_month',
    'schedule', 'format_list_numbered', 'business',
}


def scan_icon_names():
    """Every Material Symbols ligature referenced anywhere in the app."""
    names = set()
    for base in SCAN_DIRS:
        if not base.exists():
            continue
        for path in base.rglob('*'):
            if path.suffix.lower() not in SCAN_SUFFIXES or not path.is_file():
                continue
            text = path.read_text(encoding='utf-8', errors='ignore')

            names.update(RE_MARKUP.findall(text))

            if path.suffix == '.js':
                for pattern in (RE_JS_ASSIGN, RE_JS_EL, RE_JS_ICON_CALL):
                    for expr in pattern.findall(text):
                        names.update(RE_JS_STRING.findall(expr))

            # Only trust `content:` where the same file names the icon family.
            if path.suffix == '.css' and 'Material Symbols Outlined' in text:
                names.update(RE_CSS_CONTENT.findall(text))

    return names


def fetch_valid_names():
    """The full set of real Material Symbols names, from Google's codepoints."""
    text = fetch(CODEPOINTS_URL, as_text=True)
    return {line.split()[0] for line in text.splitlines() if line.strip()}


def fetch(url, as_text=False):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
    return data.decode('utf-8') if as_text else data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true',
                        help='report drift without writing the font')
    args = parser.parse_args()

    candidates = scan_icon_names() | EXTRA_ICONS
    if not candidates:
        print('No Material Symbols icons found — refusing to build an empty font.')
        return 1

    valid = fetch_valid_names()
    names = sorted(candidates & valid)
    rejected = sorted(candidates - valid)

    print(f'Found {len(names)} icons in use:')
    print('  ' + ', '.join(names))
    if rejected:
        # Usually incidental strings the scanner swept up next to a real icon
        # assignment; occasionally a genuine typo in a template.
        print(f'\nIgnored {len(rejected)} non-icon string(s): {", ".join(rejected)}')
        print('  If one of these was meant to be an icon, it is misspelled and '
              'would render as raw text.')

    previous = []
    if MANIFEST_PATH.exists():
        previous = MANIFEST_PATH.read_text(encoding='utf-8').split()

    if args.check:
        missing = sorted(set(names) - set(previous))
        extra = sorted(set(previous) - set(names))
        if not FONT_PATH.exists():
            print('\nFAIL: font file is missing. Run without --check.')
            return 1
        if missing:
            print(f'\nFAIL: not in the font, will render as raw text: {", ".join(missing)}')
            print('Run: python scripts/update_icon_font.py')
            return 1
        if extra:
            print(f'\nNote: {len(extra)} unused glyph(s) still bundled: {", ".join(extra)}')
        print('\nOK: every icon in use is in the font.')
        return 0

    css = fetch(CSS_URL.format(names=','.join(names)), as_text=True)
    match = re.search(r'url\((https://fonts\.gstatic\.com[^)]+)\)', css)
    if not match:
        print('Could not find a font URL in the Google Fonts response:')
        print(css[:500])
        return 1

    font = fetch(match.group(1))
    if font[:4] != b'wOF2':
        print(f'Downloaded file is not woff2 (starts with {font[:4]!r}).')
        return 1

    FONT_PATH.parent.mkdir(parents=True, exist_ok=True)
    FONT_PATH.write_bytes(font)
    MANIFEST_PATH.write_text('\n'.join(names) + '\n', encoding='utf-8')

    print(f'\nWrote {FONT_PATH.relative_to(ROOT)} ({len(font):,} bytes)')
    print(f'Wrote {MANIFEST_PATH.relative_to(ROOT)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
