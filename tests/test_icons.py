"""The icon font must contain every glyph the app asks for.

Material Symbols icons are ligatures: the ligature name *is* the element's text
content. So a glyph the subset is missing does not render as blank or as a
placeholder box -- it renders as the literal string ``more_vert`` in the middle
of the UI. That is a visible defect produced by forgetting one step
(``python scripts/update_icon_font.py``) after adding an icon to a template.

``update_icon_font.py --check`` covers the same ground, but it needs the network
to validate names against Google's published codepoints. These tests need
nothing, so they run in the ordinary pass.
"""
import re
from pathlib import Path

import pytest

FONT_DIR = Path('app/static/fonts')
MANIFEST = FONT_DIR / 'material-symbols-outlined.txt'
WOFF2 = FONT_DIR / 'material-symbols-outlined.woff2'
ICONS_CSS = Path('app/static/css/icons.css')

# class="… material-symbols-outlined …" … >ligature<
RE_RENDERED_ICON = re.compile(
    r'class="([^"]*material-symbols-outlined[^"]*)"[^>]*>([^<]*)<')


@pytest.fixture(scope='module')
def subset():
    return set(MANIFEST.read_text(encoding='utf-8').split())


def _pages_with_icons(app, client):
    """Every parameterless GET route that answers 200 and draws an icon."""
    urls = sorted({str(rule) for rule in app.url_map.iter_rules()
                   if 'GET' in rule.methods and not rule.arguments
                   and rule.endpoint != 'static'})
    for url in urls:
        response = client.get(url)
        if response.status_code != 200:
            continue
        html = response.get_data(as_text=True)
        if 'material-symbols-outlined' in html:
            yield url, html


class TestTheFontFile:
    def test_the_subset_and_its_manifest_both_exist(self):
        assert WOFF2.exists(), 'missing font: python scripts/update_icon_font.py'
        assert MANIFEST.exists(), 'missing manifest: python scripts/update_icon_font.py'

    def test_the_file_really_is_woff2(self):
        """A Google Fonts error page written over the font is still a file.

        The build script checks this too; asserted here so a bad font committed
        by any other route is caught.
        """
        assert WOFF2.read_bytes()[:4] == b'wOF2'

    def test_the_face_is_declared_exactly_once(self):
        """Two @font-face rules for one family means one of them is stale.

        The declaration used to sit in dashboard.css, which the public site
        does not load -- so the site had no icon font at all. It lives in
        icons.css now, and it should live in exactly one place.
        """
        declarations = [
            path for path in Path('app/static/css').rglob('*.css')
            if 'Material Symbols Outlined' in path.read_text(encoding='utf-8')
            and '@font-face' in path.read_text(encoding='utf-8')
        ]
        assert declarations == [ICONS_CSS], declarations


class TestRenderedIcons:
    def test_every_icon_on_every_page_is_in_the_font(self, app, client, signed_in,
                                                     mock_db, subset):
        """The regression this file exists for."""
        signed_in()
        missing = {}
        checked = 0
        for url, html in _pages_with_icons(app, client):
            for _classes, text in RE_RENDERED_ICON.findall(html):
                ligature = text.strip()
                checked += 1
                if ligature and ligature not in subset:
                    missing.setdefault(ligature, set()).add(url)
        assert checked > 200, 'only %d icons rendered -- did the scan break?' % checked
        assert not missing, 'not in the font subset (run update_icon_font.py): %s' % {
            k: sorted(v) for k, v in missing.items()}

    def test_no_icon_element_is_empty(self, app, client, signed_in, mock_db):
        """An icon class with no ligature draws nothing at all.

        The failure mode of a find-and-replace that moved the class but dropped
        the glyph name -- invisible in review, invisible in the DOM.
        """
        empty = []
        signed_in()
        for url, html in _pages_with_icons(app, client):
            for classes, text in RE_RENDERED_ICON.findall(html):
                # A Jinja expression that resolved to nothing counts as empty.
                if not text.strip():
                    empty.append((url, classes))
        assert not empty, 'icon elements with no ligature: %s' % empty[:10]

    def test_every_icon_is_hidden_from_assistive_tech(self, app, client,
                                                      signed_in, mock_db):
        """The ligature name is text, so an unhidden icon gets read aloud.

        "more_vert" announced next to a button is the audible version of the
        raw-ligature bug.
        """
        exposed = []
        signed_in()
        for url, html in _pages_with_icons(app, client):
            for match in re.finditer(
                    r'<i\b[^>]*material-symbols-outlined[^>]*>([a-z0-9_]+)</i>', html):
                if 'aria-hidden' not in match.group(0):
                    exposed.append((url, match.group(1)))
        assert not exposed, 'icons missing aria-hidden="true": %s' % exposed[:10]


class TestNoSecondIconSet:
    """Bootstrap Icons is gone; nothing should quietly bring it back."""

    def test_no_template_or_asset_references_bootstrap_icons(self):
        offenders = []
        for pattern in ('app/templates/**/*.html', 'app/static/**/*.js',
                        'app/static/**/*.css'):
            for path in Path('.').glob(pattern):
                text = path.read_text(encoding='utf-8', errors='ignore')
                if 'bootstrap-icons' in text or re.search(r'\bbi bi-', text):
                    offenders.append(str(path))
        assert not offenders, 'Bootstrap Icons is back in: %s' % offenders

    def test_the_csp_allows_no_cdn_font_host(self, client):
        """Icons are served from our own origin, so font-src needs no CDN.

        The whole outage this migration answered was a CDN font blocked by a
        font-src that did not list its host.
        """
        csp = client.get('/login').headers['Content-Security-Policy']
        font_src = next(d for d in csp.split('; ') if d.startswith('font-src'))
        assert 'jsdelivr' not in font_src, font_src
        assert "'self'" in font_src, font_src
