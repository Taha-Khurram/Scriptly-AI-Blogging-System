"""The composed data layer's surface and its per-domain mixins.

Written after a real-Firestore run caught a regression the existing checks could
not: the repository split moved `ast.FunctionDef` nodes, and the AST comparison
that verified it compared *methods*, so a class-level constant
(`GalleryRepository.GALLERY_SORTS`) was dropped silently. Every mocked test
still passed, because the attribute is only touched on a code path that runs
after a successful Firestore query -- and the mock returned before reaching it.

So these tests assert on the composed class's whole surface, not just its
methods, and they exercise each repository's pure logic (sorting, filtering,
pagination arithmetic, sanitisation) against real data shapes rather than
mock returns.
"""
import inspect

import pytest


# =========================================================================
# Composition
# =========================================================================

class TestComposition:
    def test_every_mixin_is_composed(self):
        from app.firebase.firestore_service import FirestoreService
        from app.repositories import __all__ as exported

        bases = {base.__name__ for base in FirestoreService.__mro__}
        missing = set(exported) - bases
        assert not missing, f'mixins declared but not composed: {missing}'

    def test_no_method_is_shadowed_between_mixins(self):
        """Two mixins defining the same name means one silently wins.

        MRO order would decide it, which is not a decision anyone made
        deliberately -- and the loser's behaviour vanishes without a trace.
        """
        from app.repositories import __all__ as exported
        import app.repositories as package

        seen = {}
        collisions = []
        for class_name in exported:
            mixin = getattr(package, class_name)
            for name, member in vars(mixin).items():
                if name.startswith('__') or not callable(member):
                    continue
                if name in seen:
                    collisions.append((name, seen[name], class_name))
                seen[name] = class_name

        assert not collisions, f'methods defined in two mixins: {collisions}'

    def test_class_attributes_survive_composition(self):
        """The regression that a real-Firestore run had to find.

        A constant defined on a mixin's class body must be reachable through
        the composed class. The AST method comparison used to verify the split
        could not see these, and no mocked test reached the code path that
        uses one.
        """
        from app.firebase.firestore_service import FirestoreService
        from app.repositories import __all__ as exported
        import app.repositories as package

        missing = []
        for class_name in exported:
            mixin = getattr(package, class_name)
            for name, value in vars(mixin).items():
                if name.startswith('__') or callable(value) or isinstance(
                    value, (staticmethod, classmethod, property)
                ):
                    continue
                if not hasattr(FirestoreService, name):
                    missing.append(f'{class_name}.{name}')

        assert not missing, f'class attributes lost in composition: {missing}'

    def test_every_self_attribute_reference_resolves(self):
        """Catch the *general* form of the GALLERY_SORTS bug.

        Any `self.NAME` a repository reads must be provided by something --
        __init__, a mixin's class body, or a method. A reference to a name
        nothing defines is an AttributeError waiting for the right code path,
        and it will not surface until that path runs against real data.
        """
        import glob
        import io
        import re

        from app.firebase.firestore_service import FirestoreService

        provided = set(dir(FirestoreService))
        # Set in FirestoreService.__init__ rather than on a class body.
        provided |= {'db', 'collection_name', 'activity_collection',
                     'user_collection'}

        unresolved = set()
        for path in glob.glob('app/repositories/*.py'):
            source = io.open(path, encoding='utf-8').read()
            for name in re.findall(r'self\.([A-Za-z_][A-Za-z0-9_]*)', source):
                if name not in provided:
                    unresolved.add(f'{path}:{name}')

        assert not unresolved, f'unresolved self references: {sorted(unresolved)}'

    def test_construction_is_cheap_and_shares_one_client(self):
        """Many modules build one at import time; they must share a client."""
        from app.firebase.firestore_service import FirestoreService

        first, second = FirestoreService(), FirestoreService()
        assert first.db is second.db

    def test_public_surface_is_stable(self):
        """The split promised an unchanged surface; this pins it.

        A method disappearing is a broken call site somewhere in 150 of them,
        and nothing else in the suite would necessarily catch it.
        """
        from app.firebase.firestore_service import FirestoreService

        methods = {
            name for name, _ in inspect.getmembers(
                FirestoreService, predicate=inspect.isfunction
            )
            if not name.startswith('__')
        }
        # A representative slice across all thirteen domains, so the assertion
        # fails loudly if a whole mixin stops being composed.
        expected = {
            'get_blog_by_id', 'create_draft', 'update_blog_content',
            'get_published_blogs', 'delete_blog', 'update_blog_status',
            'get_all_categories', 'update_category_count',
            'log_activity', 'get_activity_stats',
            'save_user', 'get_user_by_id', 'create_invitation',
            'get_due_scheduled_blogs', 'save_schedule_entry',
            'get_dashboard_data', 'get_admin_dashboard_data',
            'get_app_settings', 'get_site_settings', 'resolve_site_identifier',
            'save_contact_submission', 'get_contact_stats',
            'create_comment', 'get_comment_stats',
            'save_newsletter_subscriber', 'unsubscribe_newsletter',
            'update_blog_embedding', 'get_blogs_with_embeddings',
            'save_gallery_image', 'get_gallery_images',
            'save_seo_report', 'get_user_seo_reports',
        }
        assert expected <= methods, f'missing: {sorted(expected - methods)}'

    def test_retry_decorators_are_intact(self):
        """The transient-failure protection on the hottest reads."""
        import glob
        import io
        import re

        decorated = set()
        for path in glob.glob('app/repositories/*.py'):
            source = io.open(path, encoding='utf-8').read()
            decorated |= set(
                re.findall(r'@retry_on_unavailable\s+def (\w+)', source)
            )

        assert decorated == {
            'get_admin_dashboard_data', 'get_all_categories',
            'get_app_settings', 'get_blog_by_id', 'get_blogs_by_status',
            'get_dashboard_data', 'get_published_blogs',
            'get_published_count', 'get_user_by_id',
            # The count() aggregations that replaced "fetch the documents and
            # call len() on them" on the dashboard and site-settings screens.
            # They are reads on the hot path, so they get the same protection.
            'count_blogs_by_status', 'count_team_blogs_by_status',
        }


# =========================================================================
# Gallery: the logic the dropped constant drives
# =========================================================================

IMAGES = [
    {'id': '1', 'filename': 'Zebra.png', 'size': 300, 'created_at': '2026-01-03',
     'content_type': 'image/png'},
    {'id': '2', 'filename': 'apple.jpg', 'size': 100, 'created_at': '2026-01-01',
     'content_type': 'image/jpeg'},
    {'id': '3', 'filename': 'mango.gif', 'size': 200, 'created_at': '2026-01-02',
     'content_type': 'image/gif'},
]


class TestGallerySorting:
    @pytest.mark.parametrize('sort,expected_ids', [
        ('newest', ['1', '3', '2']),
        ('oldest', ['2', '3', '1']),
        ('largest', ['1', '3', '2']),
        ('smallest', ['2', '3', '1']),
    ])
    def test_each_option_orders_correctly(self, sort, expected_ids):
        from app.repositories.gallery import GalleryRepository

        key_fn, reverse = GalleryRepository.GALLERY_SORTS[sort]
        ordered = sorted(IMAGES, key=key_fn, reverse=reverse)
        assert [image['id'] for image in ordered] == expected_ids

    def test_name_sort_is_case_insensitive(self):
        """Otherwise 'Zebra.png' sorts before 'apple.jpg' on ASCII order,
        which reads as broken to anyone looking at the grid."""
        from app.repositories.gallery import GalleryRepository

        key_fn, reverse = GalleryRepository.GALLERY_SORTS['name']
        ordered = sorted(IMAGES, key=key_fn, reverse=reverse)
        assert [image['filename'] for image in ordered] == [
            'apple.jpg', 'mango.gif', 'Zebra.png'
        ]

    def test_every_advertised_sort_option_is_implemented(self):
        """The route validates ?sort= against SORT_OPTIONS and passes it
        through, so an option offered but not implemented is a KeyError."""
        from app.repositories.gallery import GalleryRepository
        from app.routes.gallery_routes import SORT_OPTIONS

        assert SORT_OPTIONS == set(GalleryRepository.GALLERY_SORTS)

    def test_missing_fields_do_not_raise(self):
        """Documents predating a field must still sort."""
        from app.repositories.gallery import GalleryRepository

        sparse = [{'id': 'x'}, {'id': 'y', 'size': 5}]
        for key_fn, reverse in GalleryRepository.GALLERY_SORTS.values():
            sorted(sparse, key=key_fn, reverse=reverse)


# =========================================================================
# Shared helpers
# =========================================================================

class TestFilterDateParsing:
    def test_returns_aware_utc(self):
        from app.repositories._helpers import _parse_filter_date

        parsed = _parse_filter_date('2026-03-15')
        assert parsed is not None and parsed.tzinfo is not None
        assert (parsed.year, parsed.month, parsed.day) == (2026, 3, 15)

    def test_end_of_day_includes_the_whole_day(self):
        """A `date_to` of 2026-03-15 must include a post from 23:59 that day."""
        from app.repositories._helpers import _parse_filter_date

        end = _parse_filter_date('2026-03-15', end_of_day=True)
        assert (end.hour, end.minute, end.second) == (23, 59, 59)

    @pytest.mark.parametrize('value', ['', 'not-a-date', '15/03/2026', None, '2026-13-45'])
    def test_unparsable_input_returns_none(self, value):
        from app.repositories._helpers import _parse_filter_date

        assert _parse_filter_date(value) is None


class TestBlogContentSanitisation:
    def test_html_is_cleaned_and_markdown_source_is_not(self):
        """The stored `html` is served with autoescaping off; `body` is the
        author's source text and is re-converted through this same path."""
        from app.repositories._helpers import _sanitize_blog_content

        source = '# Heading\n\nSome *markdown* with <script> in prose.'
        cleaned = _sanitize_blog_content({
            'html': '<p>ok</p><script>alert(1)</script>',
            'body': source,
            'toc_html': '<ul><li onclick="x()">Intro</li></ul>',
        })

        assert 'script' not in cleaned['html']
        assert 'onclick' not in cleaned['toc_html']
        assert cleaned['body'] == source

    @pytest.mark.parametrize('value', [None, '', {}])
    def test_empty_input_passes_through(self, value):
        from app.repositories._helpers import _sanitize_blog_content

        assert _sanitize_blog_content(value) == value

    def test_plain_string_content_is_sanitised(self):
        """Older documents stored content as a bare string."""
        from app.repositories._helpers import _sanitize_blog_content

        assert 'script' not in _sanitize_blog_content('<script>x</script><p>hi</p>')
