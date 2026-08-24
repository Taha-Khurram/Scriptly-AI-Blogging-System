"""The mechanisms that keep page latency proportional to work, not to trips.

Page latency in this application is round-trip *count* multiplied by round-trip
latency, and a Firestore round trip measured 0.5-3.5 s from a client that is
not co-located with the database. So the things worth protecting with tests are
the three mechanisms that reduce the count and the size of each trip:

* the per-request memo, which collapses a lookup several repositories each need
* ``run_parallel_simple`` propagating context, without which the memo is
  invisible to the very fan-out that needs it most
* the projections, which decide how much of a document crosses the wire

Each of these is easy to break silently -- a memo that quietly stops memoising,
or a projection that quietly drops a field a template reads, produces no error.
"""
from __future__ import annotations

import pytest


# =========================================================================
# Per-request memoisation
# =========================================================================

class TestRequestCache:
    def test_repeated_calls_hit_the_backend_once_per_request(self, app):
        from app.repositories._helpers import request_cached

        calls = []

        class Repo:
            @request_cached(lambda owner: owner)
            def load(self, owner):
                calls.append(owner)
                return {'owner': owner}

        repo = Repo()
        with app.test_request_context('/'):
            app.preprocess_request()
            assert repo.load('a') == {'owner': 'a'}
            assert repo.load('a') == {'owner': 'a'}
            assert repo.load('b') == {'owner': 'b'}

        assert calls == ['a', 'b'], 'the second load("a") should not have run'

    def test_the_store_does_not_leak_between_requests(self, app):
        """The critical safety property: one user must never see another's.

        The store lives in a ContextVar, and a worker thread from a pool is
        reused across requests. If the store were created lazily and never
        reset, a second request landing on that thread would read the first
        request's cached values -- which here would mean serving one admin's
        team membership, contact submissions or comments to another.
        """
        from app.repositories._helpers import request_cached

        calls = []

        class Repo:
            @request_cached(lambda owner: owner)
            def load(self, owner):
                calls.append(owner)
                return owner

        repo = Repo()
        for _ in range(2):
            with app.test_request_context('/'):
                app.preprocess_request()
                repo.load('a')
                repo.load('a')

        assert calls == ['a', 'a'], 'each request must do its own first read'

    def test_calls_through_with_no_request(self):
        """The scheduler and CLI scripts have nowhere to hang a store."""
        from app.repositories._helpers import request_cached

        calls = []

        class Repo:
            @request_cached(lambda owner: owner)
            def load(self, owner):
                calls.append(owner)
                return owner

        repo = Repo()
        assert repo.load('a') == 'a'
        assert repo.load('a') == 'a'
        assert calls == ['a', 'a']


# =========================================================================
# Context propagation across the fan-out
# =========================================================================

class TestParallelContext:
    def test_workers_share_the_requests_memo(self, app):
        """Without this, every parallel task re-issues the memoised query.

        ``ThreadPoolExecutor`` does not propagate ``contextvars``, so a worker
        would start with an empty store, miss, and pay a full round trip -- the
        exact cost the memo exists to remove, reintroduced by the optimisation
        that was supposed to compound with it.
        """
        from app.repositories._helpers import request_cached
        from app.utils.parallel import run_parallel_simple

        calls = []

        class Repo:
            @request_cached(lambda owner: owner)
            def load(self, owner):
                calls.append(owner)
                return owner

        repo = Repo()
        with app.test_request_context('/'):
            app.preprocess_request()
            repo.load('a')                       # warm it in the request thread
            results = run_parallel_simple(
                [(repo.load, ('a',)) for _ in range(4)], max_workers=4
            )

        assert results == ['a'] * 4
        assert calls == ['a'], f'workers missed the memo: {calls}'

    def test_a_failing_task_does_not_fail_the_others(self, app):
        from app.utils.parallel import run_parallel_simple

        def boom():
            raise RuntimeError('backend down')

        results = run_parallel_simple(
            [(boom, ()), (lambda: 'ok', ())], max_workers=2
        )
        assert results == [None, 'ok']


# =========================================================================
# Projections
# =========================================================================

class TestProjections:
    def test_apply_projection_is_a_no_op_without_fields(self):
        from app.repositories._helpers import apply_projection

        sentinel = object()
        assert apply_projection(sentinel, None) is sentinel
        assert apply_projection(sentinel, ()) is sentinel

    def test_apply_projection_selects_the_given_fields(self):
        from app.repositories._helpers import apply_projection

        class FakeQuery:
            def __init__(self):
                self.selected = None

            def select(self, fields):
                self.selected = fields
                return self

        query = FakeQuery()
        apply_projection(query, ('title', 'status'))
        assert query.selected == ['title', 'status']

    @pytest.mark.parametrize('field', [
        # The all-blogs table, the drafts list and the dashboard cards render
        # these; site_settings and the public site key on the owner fields and
        # the slug. Dropping any one of them from the projection renders an
        # empty column rather than raising, so it is asserted rather than
        # trusted.
        'title', 'status', 'category', 'author', 'author_id',
        'site_owner_id', 'slug', 'created_at', 'updated_at',
    ])
    def test_blog_list_projection_covers_what_the_list_views_render(self, field):
        from app.repositories._helpers import BLOG_LIST_FIELDS

        assert field in BLOG_LIST_FIELDS

    def test_blog_list_projection_excludes_the_expensive_payloads(self):
        """The whole point: 25 documents measured 1.9 MB unprojected.

        ``content`` is the post body, ``embedding`` is a float vector for
        semantic search, and ``outline``/``seo``/``formatting``/``metadata`` are
        generation artefacts. No list screen reads any of them -- the edit
        dialogs fetch a single document through ``/api/get_blog/<id>``.
        """
        from app.repositories._helpers import BLOG_LIST_FIELDS

        for field in ('content', 'embedding', 'outline', 'seo',
                      'formatting', 'metadata'):
            assert field not in BLOG_LIST_FIELDS

    def test_the_approval_queue_adds_the_requested_schedule(self):
        from app.repositories._helpers import BLOG_LIST_FIELDS, BLOG_QUEUE_FIELDS

        assert set(BLOG_LIST_FIELDS) < set(BLOG_QUEUE_FIELDS)
        assert 'requested_schedule_at' in BLOG_QUEUE_FIELDS

    def test_activity_projection_covers_the_row_and_every_filter(self):
        from app.repositories.activity import ACTIVITY_LIST_FIELDS

        # Rendered by activity.js
        for field in ('type', 'target_type', 'target_name', 'action_text',
                      'blog_title', 'user_name', 'timestamp'):
            assert field in ACTIVITY_LIST_FIELDS
        # Read by the user filter in get_all_activity_for_admin
        assert 'user_id' in ACTIVITY_LIST_FIELDS
        # An arbitrary per-entry map that nothing in the list reads
        assert 'metadata' not in ACTIVITY_LIST_FIELDS


# =========================================================================
# Cross-request listing cache
# =========================================================================

class TestOwnerListing:
    """The cache that makes filtering and paging cheap, and its invalidation.

    A stale listing here is the failure mode that matters: a moderator deletes a
    comment, the page re-fetches, and the comment is still there. So the tests
    are as much about the invalidation as about the caching.
    """

    def _repo(self, prefix, calls):
        from app.repositories._helpers import owner_listing

        class Repo:
            @owner_listing(prefix, ttl=60)
            def load(self, owner_id):
                calls.append(owner_id)
                return [{'owner': owner_id, 'n': len(calls)}]

        return Repo()

    def test_caches_across_separate_requests(self, app):
        calls = []
        repo = self._repo('test_listing_a', calls)

        for _ in range(3):
            with app.test_request_context('/'):
                app.preprocess_request()
                repo.load('owner-1')

        assert calls == ['owner-1'], f'expected one fetch, got {calls}'

    def test_scoped_per_owner(self, app):
        calls = []
        repo = self._repo('test_listing_b', calls)

        with app.test_request_context('/'):
            app.preprocess_request()
            repo.load('owner-1')
            repo.load('owner-2')
            repo.load('owner-1')

        assert calls == ['owner-1', 'owner-2']

    def test_invalidation_forces_a_refetch(self, app):
        from app.repositories._helpers import invalidate_owner_listing

        calls = []
        repo = self._repo('test_listing_c', calls)

        with app.test_request_context('/'):
            app.preprocess_request()
            repo.load('owner-1')

        invalidate_owner_listing('test_listing_c', 'owner-1')

        with app.test_request_context('/'):
            app.preprocess_request()
            repo.load('owner-1')

        assert calls == ['owner-1', 'owner-1'], 'invalidation did not take effect'

    def test_an_empty_result_is_still_cached(self, app):
        """An owner with no rows must not re-query on every request."""
        from app.repositories._helpers import owner_listing

        calls = []

        class Repo:
            @owner_listing('test_listing_d', ttl=60)
            def load(self, owner_id):
                calls.append(owner_id)
                return []

        repo = Repo()
        for _ in range(3):
            with app.test_request_context('/'):
                app.preprocess_request()
                assert repo.load('owner-1') == []

        assert calls == ['owner-1']


class TestCachedListingsAreNotMutatedInPlace:
    """Callers normalise timestamps in place; the cached set must survive it.

    ``get_comments_for_dashboard``, ``get_contact_submissions`` and
    ``get_all_activity_for_admin`` all rewrite datetimes to ISO strings on the
    rows they return. Those rows used to be freshly fetched, so mutating them
    was harmless. Now they come from a shared cache, and handing out references
    would corrupt it -- the next reader's date filter and sort would receive
    strings where they expect datetimes.
    """

    def _stub_db(self, docs):
        from unittest.mock import MagicMock

        snapshots = []
        for i, payload in enumerate(docs):
            snap = MagicMock()
            snap.id = 'doc-%d' % i
            snap.to_dict.return_value = payload
            snapshots.append(snap)

        query = MagicMock()
        query.where.return_value = query
        query.stream.return_value = iter(snapshots)

        db = MagicMock()
        db.collection.return_value = query
        return db

    def test_comment_page_does_not_mutate_the_cached_rows(self, app):
        from datetime import datetime, timezone

        from app.repositories.comments import CommentRepository

        created = datetime(2026, 5, 1, tzinfo=timezone.utc)

        class Repo(CommentRepository):
            pass

        repo = Repo()
        repo.db = self._stub_db([
            {'site_owner_id': 'o1', 'status': 'published', 'created_at': created,
             'admin_edits': [{'edited_at': created}]},
        ])

        with app.test_request_context('/'):
            app.preprocess_request()
            page = repo.get_comments_for_dashboard('o1', page=1, per_page=10)
            # Simulate exactly what the route does to the returned rows.
            for row in page['comments']:
                row['created_at'] = row['created_at'].isoformat()
                for edit in row['admin_edits']:
                    edit['edited_at'] = edit['edited_at'].isoformat()

            # The shared set must still hold real datetimes.
            cached = repo._owner_comments('o1')
            assert isinstance(cached[0]['created_at'], datetime)
            assert isinstance(cached[0]['admin_edits'][0]['edited_at'], datetime)

    def test_contact_page_does_not_mutate_the_cached_rows(self, app):
        from datetime import datetime, timezone

        from app.repositories.contact import ContactRepository

        created = datetime(2026, 5, 1, tzinfo=timezone.utc)

        class Repo(ContactRepository):
            pass

        repo = Repo()
        repo.db = self._stub_db([
            {'site_owner_id': 'o1', 'read': False, 'created_at': created,
             'name': 'A', 'email': 'a@example.com', 'subject': 'Hi'},
        ])

        with app.test_request_context('/'):
            app.preprocess_request()
            page = repo.get_contact_submissions('o1', page=1, per_page=10)
            assert isinstance(page['submissions'][0]['created_at'], str)

            cached = repo._owner_submissions('o1')
            assert isinstance(cached[0]['created_at'], datetime)

    def test_stats_and_table_share_one_fetch(self, app):
        """The whole point of _owner_comments: one scan answers both."""
        from app.repositories.comments import CommentRepository

        class Repo(CommentRepository):
            pass

        repo = Repo()
        db = self._stub_db([
            {'site_owner_id': 'o1', 'status': 'published'},
            {'site_owner_id': 'o1', 'status': 'removed'},
        ])
        repo.db = db

        with app.test_request_context('/'):
            app.preprocess_request()
            stats = repo.get_comment_stats('o1')
            table = repo.get_comments_for_dashboard('o1', page=1, per_page=10)

        assert stats == {'total': 2, 'published': 1, 'ai_edited': 0, 'removed': 1}
        assert table['total'] == 2
        # One stream() for both consumers. Two would be the original bug.
        assert db.collection.return_value.stream.call_count == 1


# =========================================================================
# Sort keys
# =========================================================================

class TestSortKeys:
    def test_comment_sort_tolerates_a_missing_timestamp(self):
        """``created_at`` is a SERVER_TIMESTAMP and can read back as None.

        The original leads sort compared a datetime against ``''``, which
        raises TypeError -- and because the caller wrapped everything in
        ``except Exception``, that surfaced as an empty list rather than an
        error. Sorting must degrade, not disappear.
        """
        from app.repositories.comments import _created_at_key

        rows = [{'created_at': None}, {'id': 'no-field'}]
        assert sorted(rows, key=_created_at_key) == rows

    def test_contact_sort_orders_newest_first_with_gaps(self):
        from datetime import datetime, timezone

        from app.repositories.contact import _created_at_key

        older = datetime(2026, 1, 1, tzinfo=timezone.utc)
        newer = datetime(2026, 6, 1, tzinfo=timezone.utc)
        rows = [
            {'id': 'older', 'created_at': older},
            {'id': 'missing'},
            {'id': 'newer', 'created_at': newer},
        ]
        ordered = [r['id'] for r in sorted(rows, key=_created_at_key, reverse=True)]
        assert ordered == ['newer', 'older', 'missing']
