"""Creation history: the transcript written after a run, and the screen that reads it.

Three layers, tested where each of them can actually go wrong.

**The repository** is tested against a fake Firestore rather than a MagicMock,
because the interesting behaviour *is* the query: the keyset cursor, the
extra-row trick that answers "is there more?", the ownership check that must
not distinguish "not yours" from "not there". A MagicMock would return whatever
it was told and prove none of it.

**The write path** is tested through ``_run_generation_task`` itself, because
the thing worth guaranteeing is that a run records itself on *every* exit --
including the two failure exits, which are the ones a reader is most likely to
come back looking for and the ones easiest to leave out.

**The routes** are tested for scoping and for the shapes the page and its
script actually read.
"""
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# A Firestore stand-in
#
# Enough of the client surface for one collection: where/order_by/select/
# start_after/limit/stream, document().get()/set()/delete(), and batches. The
# repository composes those calls in an order that matters, and only a fake
# that actually applies them can tell a correct composition from a plausible
# one.
# ---------------------------------------------------------------------------

class FakeDoc:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data
        self.exists = True
        self.reference = self

    def to_dict(self):
        return dict(self._data)


class FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def where(self, filter=None):
        field, op, value = filter.field_path, filter.op_string, filter.value
        assert op == '=='
        return FakeQuery([r for r in self._rows if r[1].get(field) == value])

    def order_by(self, field, direction=None):
        reverse = direction == 'DESCENDING'
        return FakeQuery(sorted(self._rows, key=lambda r: r[1][field], reverse=reverse))

    def select(self, fields):
        # Projections are applied for real, so a test notices when a field the
        # rail renders is missing from the projection list.
        kept = []
        for doc_id, data in self._rows:
            kept.append((doc_id, {k: v for k, v in data.items() if k in fields}))
        return FakeQuery(kept)

    def start_after(self, snapshot):
        field, value = next(iter(snapshot.items()))
        out, seen = [], False
        for row in self._rows:
            if seen:
                out.append(row)
            elif row[1].get(field) == value:
                seen = True
        return FakeQuery(out)

    def limit(self, n):
        return FakeQuery(self._rows[:n])

    def stream(self):
        return [FakeDoc(doc_id, data) for doc_id, data in self._rows]


class FakeDocRef:
    def __init__(self, store, doc_id):
        self._store = store
        self.id = doc_id

    def set(self, data):
        self._store[self.id] = data

    def get(self):
        if self.id not in self._store:
            missing = FakeDoc(self.id, {})
            missing.exists = False
            return missing
        return FakeDoc(self.id, self._store[self.id])

    def delete(self):
        self._store.pop(self.id, None)


class FakeBatch:
    def __init__(self, store):
        self._store = store
        self._deletes = []

    def delete(self, ref):
        self._deletes.append(ref.id)

    def commit(self):
        for doc_id in self._deletes:
            self._store.pop(doc_id, None)
        self._deletes = []


class FakeCollection:
    def __init__(self, store):
        self._store = store
        self._counter = 0

    def _rows(self):
        return list(self._store.items())

    def document(self, doc_id=None):
        if doc_id is None:
            self._counter += 1
            doc_id = 'gen-%d' % self._counter
        return FakeDocRef(self._store, doc_id)

    def where(self, filter=None):
        return FakeQuery(self._rows()).where(filter=filter)

    def order_by(self, field, direction=None):
        return FakeQuery(self._rows()).order_by(field, direction=direction)


class FakeClient:
    def __init__(self):
        self.stores = {}

    def collection(self, name):
        return FakeCollection(self.stores.setdefault(name, {}))

    def batch(self):
        return FakeBatch(self.stores.setdefault('generations', {}))


@pytest.fixture
def repo(monkeypatch):
    """A ``GenerationRepository`` over the fake client."""
    from app.repositories.generations import GenerationRepository
    from firebase_admin import firestore

    # The repository asks for DESCENDING by name; the fake compares against the
    # string, so bind them together rather than assuming the SDK's value.
    monkeypatch.setattr(firestore.Query, 'DESCENDING', 'DESCENDING', raising=False)

    class Repo(GenerationRepository):
        def __init__(self):
            self.db = FakeClient()
            self.generation_collection = 'generations'

    return Repo()


def _seed(repo, count, user_id='u1'):
    """``count`` runs for ``user_id``, oldest first, one minute apart."""
    from datetime import datetime, timedelta, timezone

    base = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    ids = []
    for i in range(count):
        store = repo.db.stores.setdefault('generations', {})
        doc_id = '%s-run-%d' % (user_id, i)
        store[doc_id] = {
            'user_id': user_id,
            'prompt': 'prompt %d' % i,
            'title': 'Title %d' % i,
            'status': 'completed',
            'category': 'Growth',
            'blog_id': 'blog-%d' % i,
            'blog_status': 'DRAFT',
            'word_count': 900 + i,
            'excerpt': 'body %d' % i,
            'thoughts': [{'text': 'plan %d' % i, 'kind': 'plan'}],
            'created_at': base + timedelta(minutes=i),
        }
        ids.append(doc_id)
    return ids


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------

class TestRecordGeneration:
    def test_stores_the_prompt_and_the_outcome(self, repo):
        gen_id = repo.record_generation(
            'u1', 'write about ad budgets',
            title='Ad budgets', category='Growth', blog_id='blog-9',
            blog_status='DRAFT', word_count=1043, section_count=5,
            thoughts=[{'text': 'Angle: cost per lead', 'kind': 'plan'}],
            excerpt='Visibility feels like success.',
        )
        stored = repo.db.stores['generations'][gen_id]

        assert stored['user_id'] == 'u1'
        assert stored['prompt'] == 'write about ad budgets'
        assert stored['status'] == 'completed'
        assert stored['blog_id'] == 'blog-9'
        assert stored['thoughts'] == [{'text': 'Angle: cost per lead', 'kind': 'plan'}]

    def test_caps_the_excerpt_and_the_prompt(self, repo):
        """A transcript is a log entry, not a second copy of the post."""
        from app.repositories.generations import MAX_EXCERPT_CHARS, MAX_PROMPT_CHARS

        gen_id = repo.record_generation(
            'u1', 'p' * (MAX_PROMPT_CHARS + 500), excerpt='x' * 50_000
        )
        stored = repo.db.stores['generations'][gen_id]

        assert len(stored['excerpt']) == MAX_EXCERPT_CHARS
        assert len(stored['prompt']) == MAX_PROMPT_CHARS

    def test_caps_the_number_of_thoughts(self, repo):
        from app.repositories.generations import MAX_THOUGHTS

        gen_id = repo.record_generation(
            'u1', 'p', thoughts=[{'text': 'line %d' % i} for i in range(MAX_THOUGHTS + 20)]
        )
        assert len(repo.db.stores['generations'][gen_id]['thoughts']) == MAX_THOUGHTS

    def test_accepts_plain_strings_as_thoughts(self, repo):
        """The pipeline hands dicts; a caller handing strings must not 500."""
        gen_id = repo.record_generation('u1', 'p', thoughts=['just a line'])
        assert repo.db.stores['generations'][gen_id]['thoughts'] == [
            {'text': 'just a line', 'kind': 'note'}
        ]

    def test_never_raises_when_the_write_fails(self, repo):
        """A transcript is a record about work that already happened.

        Failing to store it must not turn a blog the user can already see in
        their drafts into an error on their screen.
        """
        repo.db = MagicMock()
        repo.db.collection.side_effect = RuntimeError('firestore is down')

        assert repo.record_generation('u1', 'p') is None


class TestHistoryListing:
    def test_newest_first(self, repo):
        _seed(repo, 5)
        items = repo.get_generation_history('u1')['items']
        assert [i['title'] for i in items] == [
            'Title 4', 'Title 3', 'Title 2', 'Title 1', 'Title 0'
        ]

    def test_scoped_to_one_user(self, repo):
        _seed(repo, 3, user_id='u1')
        _seed(repo, 2, user_id='u2')

        items = repo.get_generation_history('u2')['items']
        assert len(items) == 2
        assert {i['id'].split('-')[0] for i in items} == {'u2'}

    def test_reports_more_without_returning_the_extra_row(self, repo):
        """The +1 row exists to answer the question, not to be rendered."""
        _seed(repo, 5)
        page = repo.get_generation_history('u1', limit=3)

        assert len(page['items']) == 3
        assert page['has_more'] is True
        assert page['next_cursor'] == page['items'][-1]['created_at']

    def test_last_page_has_no_cursor(self, repo):
        _seed(repo, 3)
        page = repo.get_generation_history('u1', limit=10)

        assert page['has_more'] is False
        assert page['next_cursor'] == ''

    def test_the_cursor_continues_where_the_page_ended(self, repo):
        _seed(repo, 5)
        first = repo.get_generation_history('u1', limit=2)
        second = repo.get_generation_history('u1', limit=2, before=first['next_cursor'])

        assert [i['title'] for i in first['items']] == ['Title 4', 'Title 3']
        assert [i['title'] for i in second['items']] == ['Title 2', 'Title 1']

    def test_an_unreadable_cursor_returns_the_first_page(self, repo):
        """A bad cursor should show the top of the history, not an error."""
        _seed(repo, 3)
        page = repo.get_generation_history('u1', before='not-a-date')

        assert [i['title'] for i in page['items']] == ['Title 2', 'Title 1', 'Title 0']

    def test_the_listing_leaves_the_bulk_behind(self, repo):
        """`thoughts` and `excerpt` are the transcript; the rail shows neither."""
        _seed(repo, 1)
        item = repo.get_generation_history('u1')['items'][0]

        assert 'thoughts' not in item
        assert 'excerpt' not in item
        assert item['title'] and item['created_at']

    def test_dates_come_back_serialisable(self, repo):
        import json

        _seed(repo, 2)
        json.dumps(repo.get_generation_history('u1'))   # must not raise

    def test_limit_is_bounded(self, repo):
        from app.repositories.generations import MAX_PAGE_SIZE

        _seed(repo, MAX_PAGE_SIZE + 10)
        page = repo.get_generation_history('u1', limit=10_000)

        assert len(page['items']) == MAX_PAGE_SIZE


class TestReadOne:
    def test_returns_the_whole_transcript(self, repo):
        ids = _seed(repo, 1)
        run = repo.get_generation(ids[0], 'u1')

        assert run['prompt'] == 'prompt 0'
        assert run['thoughts'] == [{'text': 'plan 0', 'kind': 'plan'}]
        assert run['excerpt'] == 'body 0'

    def test_another_users_transcript_is_not_readable(self, repo):
        ids = _seed(repo, 1, user_id='u1')
        assert repo.get_generation(ids[0], 'u2') is None

    def test_missing_and_forbidden_are_indistinguishable(self, repo):
        """A 403 for someone else's id would confirm the id exists."""
        ids = _seed(repo, 1, user_id='u1')

        assert repo.get_generation(ids[0], 'u2') == repo.get_generation('nope', 'u2')


class TestDeletes:
    def test_deletes_one(self, repo):
        ids = _seed(repo, 2)

        assert repo.delete_generation(ids[0], 'u1') is True
        assert ids[0] not in repo.db.stores['generations']
        assert ids[1] in repo.db.stores['generations']

    def test_will_not_delete_someone_elses(self, repo):
        ids = _seed(repo, 1, user_id='u1')

        assert repo.delete_generation(ids[0], 'u2') is False
        assert ids[0] in repo.db.stores['generations']

    def test_clear_removes_only_the_callers_rows(self, repo):
        _seed(repo, 4, user_id='u1')
        _seed(repo, 2, user_id='u2')

        assert repo.clear_generation_history('u1') == 4
        assert sorted(repo.db.stores['generations']) == ['u2-run-0', 'u2-run-1']

    def test_clear_on_an_empty_history_is_zero(self, repo):
        assert repo.clear_generation_history('u1') == 0


# ---------------------------------------------------------------------------
# The write path — a run records itself on every exit
# ---------------------------------------------------------------------------

class TestRunRecordsItself:
    """``_run_generation_task`` must leave a transcript however it ends."""

    @pytest.fixture
    def harness(self, app, monkeypatch):
        """Neutralise everything the task touches except the history write."""
        import app.routes.blog_routes as routes

        db = MagicMock(name='db')
        db.get_all_categories.return_value = []
        db.create_draft.return_value = 'blog-77'
        monkeypatch.setattr(routes, 'FirestoreService', lambda: db)

        category_agent = MagicMock()
        category_agent.categorize_blog.return_value = 'Growth'
        monkeypatch.setattr(routes, 'CategoryAgent', lambda: category_agent)

        monkeypatch.setattr(routes.task_manager, 'update_task', lambda *a, **k: None)
        monkeypatch.setattr(routes.task_manager, 'complete_task', lambda *a, **k: None)
        monkeypatch.setattr(routes.task_manager, 'fail_task', lambda *a, **k: None)
        monkeypatch.setattr(routes.task_manager, 'add_thought', lambda *a, **k: None)
        monkeypatch.setattr(routes.task_manager, 'append_content', lambda *a, **k: 0)
        return db

    def _run(self, app, monkeypatch, pipeline_result):
        import app.routes.blog_routes as routes

        agent = MagicMock()
        if isinstance(pipeline_result, Exception):
            agent.run_pipeline.side_effect = pipeline_result
        else:
            agent.run_pipeline.return_value = pipeline_result
        monkeypatch.setattr(routes, 'BlogAgent', lambda: agent)

        routes._run_generation_task(
            'task-1', app, 'u1', 'Writer', 'USER',
            'a guide to ad budgets', False,
        )

    def test_a_successful_run_is_recorded(self, app, monkeypatch, harness):
        self._run(app, monkeypatch, {
            'title': 'Ad budgets',
            'content': {'markdown': '# Ad budgets\n\nBody text.'},
            'formatting': {'statistics': {'word_count': 1043},
                           'toc': ['a', 'b', 'c'],
                           'reading_time': '5 min read'},
            'metadata': {'model_used': 'gemini-flash-lite-latest'},
        })

        harness.record_generation.assert_called_once()
        args, kwargs = harness.record_generation.call_args
        assert args[0] == 'u1'
        assert args[1] == 'a guide to ad budgets'
        assert kwargs['status'] == 'completed'
        assert kwargs['title'] == 'Ad budgets'
        assert kwargs['blog_id'] == 'blog-77'
        assert kwargs['blog_status'] == 'DRAFT'
        assert kwargs['word_count'] == 1043
        assert kwargs['section_count'] == 3
        assert kwargs['destination'] == 'draft'

    def test_a_pipeline_failure_is_recorded(self, app, monkeypatch, harness):
        """The run that leaves nothing in Drafts is the one worth keeping."""
        self._run(app, monkeypatch, {
            'status': 'failed',
            'error': 'The model is at its request limit.',
            'error_code': 'RATE_LIMIT',
        })

        harness.record_generation.assert_called_once()
        _, kwargs = harness.record_generation.call_args
        assert kwargs['status'] == 'failed'
        assert kwargs['error'] == 'The model is at its request limit.'
        assert kwargs['error_code'] == 'RATE_LIMIT'

    def test_an_unexpected_exception_is_recorded(self, app, monkeypatch, harness):
        self._run(app, monkeypatch, RuntimeError('socket closed'))

        harness.record_generation.assert_called_once()
        _, kwargs = harness.record_generation.call_args
        assert kwargs['status'] == 'failed'
        assert 'socket closed' in kwargs['error']

    def test_the_agents_reasoning_travels_with_the_record(self, app, monkeypatch, harness):
        """The plan is the only record of *why* a piece took the angle it did."""
        import app.routes.blog_routes as routes

        agent = MagicMock()

        def pipeline(prompt, **kw):
            kw['on_thought']('Angle: cost per lead, not vanity metrics', 'plan')
            return {
                'title': 'Ad budgets',
                'content': {'markdown': 'Body.'},
                'formatting': {'statistics': {'word_count': 10}, 'toc': []},
                'metadata': {},
            }

        agent.run_pipeline.side_effect = pipeline
        monkeypatch.setattr(routes, 'BlogAgent', lambda: agent)

        routes._run_generation_task('task-1', app, 'u1', 'Writer', 'USER', 'p', False)

        _, kwargs = harness.record_generation.call_args
        texts = [t['text'] for t in kwargs['thoughts']]
        assert 'Angle: cost per lead, not vanity metrics' in texts

    def test_a_failing_history_write_does_not_fail_the_run(self, app, monkeypatch, harness):
        """The blog is already in Drafts; the reader must not be told otherwise."""
        harness.record_generation.side_effect = RuntimeError('firestore is down')

        self._run(app, monkeypatch, {
            'title': 'Ad budgets',
            'content': {'markdown': 'Body.'},
            'formatting': {'statistics': {'word_count': 10}, 'toc': []},
            'metadata': {},
        })

        harness.create_draft.assert_called_once()

    def test_publishing_admin_records_the_destination(self, app, monkeypatch, harness):
        import app.routes.blog_routes as routes

        agent = MagicMock()
        agent.run_pipeline.return_value = {
            'title': 'Ad budgets',
            'content': {'markdown': 'Body.'},
            'formatting': {'statistics': {'word_count': 10}, 'toc': []},
            'metadata': {},
        }
        monkeypatch.setattr(routes, 'BlogAgent', lambda: agent)

        routes._run_generation_task('task-1', app, 'u1', 'Admin', 'ADMIN', 'p', True)

        _, kwargs = harness.record_generation.call_args
        assert kwargs['destination'] == 'submit'
        assert kwargs['blog_status'] == 'PUBLISHED'


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

class TestHistoryRoutes:
    def test_the_page_needs_a_session(self, client, mock_db):
        assert client.get('/history').status_code == 302

    def test_the_api_needs_a_session(self, client, mock_db):
        assert client.get('/api/history').status_code in (302, 401)

    def test_the_page_renders(self, signed_in, mock_db):
        response = signed_in().get('/history')
        assert response.status_code == 200
        assert b'data-history' in response.data

    def test_the_page_asks_for_the_signed_in_user(self, signed_in, mock_db):
        signed_in(user_id='writer-3').get('/history')
        args, _ = mock_db.get_generation_history.call_args
        assert args[0] == 'writer-3'

    def test_the_deep_link_reaches_the_template(self, signed_in, mock_db):
        response = signed_in().get('/history?run=abc123')
        assert b'data-selected="abc123"' in response.data

    def test_listing_passes_the_cursor_through(self, signed_in, mock_db):
        signed_in(user_id='u9').get('/api/history?before=2026-08-01T12:00:00%2B00:00')

        _, kwargs = mock_db.get_generation_history.call_args
        assert kwargs['before'] == '2026-08-01T12:00:00+00:00'

    def test_reading_one_is_scoped_to_the_caller(self, signed_in, mock_db):
        mock_db.get_generation.return_value = {'id': 'g1', 'prompt': 'p'}
        response = signed_in(user_id='u9').get('/api/history/g1')

        assert response.status_code == 200
        assert response.get_json()['run']['id'] == 'g1'
        assert mock_db.get_generation.call_args[0] == ('g1', 'u9')

    def test_an_unreadable_transcript_is_a_404(self, signed_in, mock_db):
        mock_db.get_generation.return_value = None
        assert signed_in().get('/api/history/nope').status_code == 404

    def test_delete_one(self, signed_in, mock_db):
        mock_db.delete_generation.return_value = True
        response = signed_in(user_id='u9').delete('/api/history/g1')

        assert response.status_code == 200
        assert mock_db.delete_generation.call_args[0] == ('g1', 'u9')

    def test_delete_says_the_blog_is_untouched(self, signed_in, mock_db):
        """The one thing a reader needs to know before pressing it."""
        mock_db.delete_generation.return_value = True
        body = signed_in().delete('/api/history/g1').get_json()
        assert 'untouched' in body['message'].lower()

    def test_deleting_what_is_not_there_is_a_404(self, signed_in, mock_db):
        mock_db.delete_generation.return_value = False
        assert signed_in().delete('/api/history/g1').status_code == 404

    def test_clear_all_reports_the_count(self, signed_in, mock_db):
        mock_db.clear_generation_history.return_value = 7
        body = signed_in(user_id='u9').delete('/api/history').get_json()

        assert body['deleted'] == 7
        assert mock_db.clear_generation_history.call_args[0] == ('u9',)


class TestNavigation:
    def test_the_sidebar_links_to_history(self, signed_in, mock_db):
        """A screen nothing links to is a screen nobody finds."""
        response = signed_in().get('/dashboard')
        assert b'data-page="history"' in response.data

    def test_pjax_recognises_the_route(self):
        """`/history` must be in both dashboard-path lists in app.js.

        One of them decides whether the link is intercepted; the other decides
        whether the full-page loader is thrown up over it. A route in the first
        and not the second navigates by PJAX *and* covers itself with a spinner
        that nothing then clears.
        """
        import io

        source = io.open('app/static/js/app.js', encoding='utf-8').read()
        assert source.count("'/history'") == 3   # two path lists + the skeleton map
