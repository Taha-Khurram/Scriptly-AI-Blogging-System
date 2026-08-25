"""Tool-level tests for the conversational agent.

Every tool is called directly, with a fake repository and no Flask request, no
app context and no model. That is the point of :class:`ToolContext`: authority
and dependencies arrive as an argument, so a tool is an ordinary function and
its guarantees can be asserted on in isolation.

The heart of this file is :class:`TestApprovalGate`. Everything else here is
ordinary coverage; those tests are the ones that hold the product's central
promise -- that the agent cannot write a post the user has not approved -- and
they assert it against *stored state*, not against a prompt. If someone later
"helpfully" adds a ``force=True`` parameter to ``create_blog``, these fail.
"""
from __future__ import annotations

import pytest

from app.agent.approval import is_affirmative, is_revision_request
from app.agent.context import ToolBudgetError, ToolContext
from app.agent.tools import blogs as blog_tools
from app.agent.tools import outlines as outline_tools
from app.agent.tools import research as research_tools


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeDb:
    """A repository stand-in holding dicts, with the real access semantics.

    Only the methods the tools call. It enforces the same ownership rule the
    Firestore repository does -- returning ``None`` for another user's document
    rather than raising -- because a fake that is more permissive than the real
    thing turns an access-control test into a test of nothing.
    """

    def __init__(self, user_id='u1'):
        self.user_id = user_id
        self.blogs = {}
        self.outlines = {}
        self.confirmations = {}
        self.categories = []
        self.activity = []
        self.generations = []
        self.deleted = []
        self.messages = []
        self.next_id = 1

    # --- chat ---
    def append_chat_message(self, session_id, user_id, role, text, **fields):
        """Record one message and hand back its id and sequence.

        Present on the fake because the *loop* persists a reply on every exit
        path, so a fake without it would make every loop test fail on the way
        out rather than on what it was testing.
        """
        seq = len(self.messages)
        self.messages.append({'session_id': session_id, 'user_id': user_id,
                              'role': role, 'text': text, 'seq': seq, **fields})
        return {'id': f'msg-{seq}', 'seq': seq}

    def get_chat_messages(self, session_id, user_id, limit=30, after_seq=None):
        return [m for m in self.messages if m['session_id'] == session_id]

    def update_chat_session(self, session_id, user_id, **fields):
        return True

    def _id(self, prefix):
        self.next_id += 1
        return f'{prefix}-{self.next_id}'

    # --- blogs ---
    def get_blog_by_id(self, blog_id):
        blog = self.blogs.get(blog_id)
        return dict(blog, id=blog_id) if blog else None

    def create_draft(self, document, user_id):
        blog_id = self._id('blog')
        self.blogs[blog_id] = dict(document, author_id=user_id,
                                   site_owner_id=user_id)
        return blog_id

    def update_blog_content(self, blog_id, title, content, **kwargs):
        if blog_id not in self.blogs:
            return False
        self.blogs[blog_id].update(title=title, content=content)
        return True

    def delete_blog(self, blog_id):
        if blog_id not in self.blogs:
            return False
        del self.blogs[blog_id]
        self.deleted.append(blog_id)
        return True

    def get_all_blogs_filtered(self, **kwargs):
        rows = [dict(b, id=i) for i, b in self.blogs.items()]
        search = (kwargs.get('search') or '').lower()
        if search:
            rows = [r for r in rows if search in (r.get('title') or '').lower()]
        status = kwargs.get('status_filter', 'all')
        if status and status != 'all':
            rows = [r for r in rows if r.get('status') == status]
        return {'blogs': rows, 'total': len(rows), 'page': 1}

    def get_site_owner_for_user(self, user_id):
        return user_id

    def get_my_sub_users(self, user_id):
        return []

    def get_all_categories(self, user_id, **kwargs):
        return self.categories

    def log_activity(self, **kwargs):
        self.activity.append(kwargs)

    def record_generation(self, user_id, prompt, **fields):
        self.generations.append({'user_id': user_id, 'prompt': prompt, **fields})
        return 'gen-1'

    # --- outlines ---
    def create_outline_record(self, user_id, session_id, outline, **fields):
        outline_id = self._id('outline')
        self.outlines[outline_id] = {
            'user_id': user_id, 'session_id': session_id,
            'status': 'pending_approval', 'approved_at': None,
            'blog_id': '', 'revision': fields.get('revision', 1),
            **{k: v for k, v in fields.items() if k != 'revision'},
            **outline,
        }
        return outline_id

    def get_outline(self, outline_id, user_id):
        record = self.outlines.get(outline_id)
        if not record or record.get('user_id') != user_id:
            return None
        return dict(record, id=outline_id)

    def approve_outline(self, outline_id, user_id, via='ui'):
        record = self.outlines.get(outline_id)
        if not record or record.get('user_id') != user_id:
            return None
        if record['status'] == 'superseded':
            return None
        record['status'] = 'approved'
        record['approved_at'] = '2026-08-25T00:00:00+00:00'
        record['approved_via'] = via
        return dict(record, id=outline_id)

    def supersede_outline(self, outline_id, user_id):
        record = self.outlines.get(outline_id)
        if not record or record.get('user_id') != user_id:
            return False
        record['status'] = 'superseded'
        return True

    def mark_outline_written(self, outline_id, user_id, blog_id):
        record = self.outlines.get(outline_id)
        if not record:
            return False
        record['blog_id'] = blog_id
        return True

    # --- confirmations ---
    def create_confirmation(self, user_id, *, session_id, action, target_id,
                            summary, payload=None, ttl=600):
        token = self._id('token')
        self.confirmations[token] = {
            'user_id': user_id, 'session_id': session_id, 'action': action,
            'target_id': target_id, 'summary': summary,
            'payload': payload or {}, 'consumed': False,
        }
        return token

    def consume_confirmation(self, token, user_id, action=None):
        record = self.confirmations.get(token)
        if not record or record['user_id'] != user_id or record['consumed']:
            return None
        if action and record['action'] != action:
            return None
        record['consumed'] = True
        return dict(record, id=token)


class FakeSearch:
    """A search service stand-in. ``available=False`` models an unset key."""

    def __init__(self, items=None, available=True, error=None):
        self._items = items or []
        self.is_available = available
        self.calls = []
        self._error = error

    def search(self, query, max_results=None, use_cache=True):
        from app.services.search_service import SearchResult
        self.calls.append(query)
        if not self.is_available:
            return SearchResult(query, provider='none', available=False)
        return SearchResult(query, list(self._items), provider='fake',
                            error=self._error)


def make_ctx(db=None, **kwargs):
    """A context with no turn log -- tools must not require one."""
    db = db or FakeDb()
    defaults = dict(db=db, user_id='u1', user_name='Ada', user_role='USER',
                    session_id='s1')
    defaults.update(kwargs)
    return ToolContext(**defaults)


# ---------------------------------------------------------------------------
# The approval gate
# ---------------------------------------------------------------------------

class TestApprovalGate:
    """The promise: no post is written without a human approving the outline.

    Asserted against stored state rather than prompt text, because a prompt is
    a request and this has to be a guarantee.
    """

    def test_a_new_outline_is_never_born_approved(self):
        db = FakeDb()
        ctx = make_ctx(db)
        outline_id = db.create_outline_record('u1', 's1', {
            'title': 'T', 'sections': [{'heading': 'A', 'points': []}],
        })
        assert db.outlines[outline_id]['status'] == 'pending_approval'

    def test_create_blog_refuses_a_pending_outline(self):
        db = FakeDb()
        ctx = make_ctx(db)
        outline_id = db.create_outline_record('u1', 's1', {
            'title': 'Pricing pages that convert',
            'sections': [{'heading': 'A', 'points': ['p']}],
        })

        result = blog_tools.create_blog(ctx, outline_id=outline_id)

        assert result['ok'] is False
        assert result['error'] == 'outline_not_approved'
        # Nothing was written. The refusal has to be total: a "partial" draft
        # saved before the check would be exactly the outcome the gate exists
        # to prevent.
        assert db.blogs == {}
        assert ctx.created_blog_ids == []

    def test_create_blog_refuses_a_superseded_outline(self):
        db = FakeDb()
        ctx = make_ctx(db)
        outline_id = db.create_outline_record('u1', 's1', {'title': 'T'})
        db.approve_outline(outline_id, 'u1')
        db.outlines[outline_id]['status'] = 'superseded'

        result = blog_tools.create_blog(ctx, outline_id=outline_id)

        assert result['ok'] is False
        assert result['error'] == 'outline_not_approved'
        assert db.blogs == {}

    def test_create_blog_refuses_when_no_outline_exists_at_all(self):
        result = blog_tools.create_blog(make_ctx())
        assert result['ok'] is False
        assert result['error'] == 'no_outline'

    def test_create_blog_takes_no_argument_that_bypasses_the_gate(self):
        """A regression guard with teeth.

        The gate is only a gate while there is no parameter that opens it. A
        future ``force``/``approved``/``skip_approval`` argument would be
        swallowed by ``**_ignored`` and silently do nothing -- so this asserts
        the *behaviour* holds when such an argument is passed, which is what
        would actually break.
        """
        db = FakeDb()
        ctx = make_ctx(db)
        outline_id = db.create_outline_record('u1', 's1', {'title': 'T'})

        for sneaky in ({'force': True}, {'approved': True},
                       {'skip_approval': True}, {'confirmed': True}):
            result = blog_tools.create_blog(ctx, outline_id=outline_id, **sneaky)
            assert result['ok'] is False, sneaky
            assert result['error'] == 'outline_not_approved', sneaky

        assert db.blogs == {}

    def test_the_model_cannot_approve_on_the_users_behalf(self):
        """``submit_outline_approval`` verifies the user's real words."""
        db = FakeDb()
        outline_id = db.create_outline_record('u1', 's1', {'title': 'T'})

        # The model calls the tool, but the user's actual last message was a
        # revision request. Approval must not be recorded.
        ctx = make_ctx(db, focus_outline_id=outline_id,
                       last_user_message='hmm, can we make section 2 shorter?')
        result = outline_tools.submit_outline_approval(ctx, outline_id=outline_id)

        assert result['approved'] is False
        assert result['reason'] == 'not_an_approval'
        assert result['looks_like_revision'] is True
        assert db.outlines[outline_id]['status'] == 'pending_approval'

    def test_a_real_yes_records_approval(self):
        db = FakeDb()
        outline_id = db.create_outline_record('u1', 's1', {'title': 'T'})
        ctx = make_ctx(db, focus_outline_id=outline_id,
                       last_user_message='yes, go ahead')

        result = outline_tools.submit_outline_approval(ctx)

        assert result['approved'] is True
        assert db.outlines[outline_id]['status'] == 'approved'
        assert db.outlines[outline_id]['approved_via'] == 'chat'

    def test_approval_is_idempotent(self):
        db = FakeDb()
        outline_id = db.create_outline_record('u1', 's1', {'title': 'T'})
        db.approve_outline(outline_id, 'u1')
        ctx = make_ctx(db, focus_outline_id=outline_id, last_user_message='yes')

        result = outline_tools.submit_outline_approval(ctx)

        assert result['approved'] is True
        assert result['already'] is True

    def test_a_revision_supersedes_and_does_not_inherit_approval(self, monkeypatch):
        db = FakeDb()
        old_id = db.create_outline_record('u1', 's1', {
            'title': 'Old', 'sections': [{'heading': 'A', 'points': []}],
        }, topic='pricing')
        db.approve_outline(old_id, 'u1')

        monkeypatch.setattr(
            'app.agent.tools.outlines.OutlineAgent', _StubOutlineAgent
        )
        ctx = make_ctx(db, focus_outline_id=old_id)

        result = outline_tools.revise_outline(ctx, feedback='shorter please')

        assert result['ok'] is True
        assert db.outlines[old_id]['status'] == 'superseded'
        new_id = result['outline_id']
        assert new_id != old_id
        # The crucial assertion: approving the old plan does not carry to the
        # new one. Otherwise "change it" then "yes" would write a post from a
        # plan the user never read.
        assert db.outlines[new_id]['status'] == 'pending_approval'


class _StubOutlineAgent:
    """Stands in for the model when only the surrounding flow is under test."""

    def create_outline(self, topic, **kwargs):
        return {
            'title': f'Outline for {topic or "something"}',
            'angle': 'a specific angle',
            'audience': 'a specific reader',
            'sections': [
                {'heading': 'First thing', 'points': ['a concrete claim']},
                {'heading': 'Second thing', 'points': ['another one']},
            ],
            'sources': [],
        }


# ---------------------------------------------------------------------------
# Destructive actions
# ---------------------------------------------------------------------------

class TestDelete:
    """Deleting is two-phase, and phase one deletes nothing."""

    def test_delete_blog_only_asks(self):
        db = FakeDb()
        db.blogs['b1'] = {'title': 'Old draft', 'status': 'DRAFT',
                          'author_id': 'u1', 'site_owner_id': 'u1'}
        ctx = make_ctx(db)

        result = blog_tools.delete_blog(ctx, blog_id='b1')

        assert result['ok'] is True
        assert result['pending_confirmation'] is True
        assert result['confirm_token']
        # Still there. The whole design rests on this.
        assert 'b1' in db.blogs
        assert db.deleted == []

    def test_the_confirmation_says_when_a_post_is_published(self):
        db = FakeDb()
        db.blogs['b1'] = {'title': 'Live post', 'status': 'PUBLISHED',
                          'author_id': 'u1', 'site_owner_id': 'u1'}
        ctx = make_ctx(db)

        blog_tools.delete_blog(ctx, blog_id='b1')

        card = next(c for c in ctx.cards if c['kind'] == 'confirm_delete')
        assert card['data']['published'] is True
        assert 'PUBLISHED' in card['data']['status']

    def test_a_token_deletes_exactly_once(self):
        db = FakeDb()
        db.blogs['b1'] = {'title': 'Old draft', 'status': 'DRAFT',
                          'author_id': 'u1', 'site_owner_id': 'u1'}
        token = blog_tools.delete_blog(make_ctx(db), blog_id='b1')['confirm_token']

        first = blog_tools.execute_confirmed_delete(db, 'u1', 'Ada', token)
        second = blog_tools.execute_confirmed_delete(db, 'u1', 'Ada', token)

        assert first['ok'] is True and first['deleted'] is True
        assert db.deleted == ['b1']
        # A double-click, or a replayed request, finds the token spent.
        assert second['ok'] is False
        assert second['error'] == 'invalid_confirmation'

    def test_another_users_token_is_refused(self):
        db = FakeDb()
        db.blogs['b1'] = {'title': 'Mine', 'status': 'DRAFT',
                          'author_id': 'u1', 'site_owner_id': 'u1'}
        token = blog_tools.delete_blog(make_ctx(db), blog_id='b1')['confirm_token']

        result = blog_tools.execute_confirmed_delete(db, 'someone-else', 'Eve', token)

        assert result['ok'] is False
        assert 'b1' in db.blogs

    def test_a_delete_that_could_not_be_recorded_is_refused(self, monkeypatch):
        db = FakeDb()
        db.blogs['b1'] = {'title': 'X', 'status': 'DRAFT',
                          'author_id': 'u1', 'site_owner_id': 'u1'}
        monkeypatch.setattr(db, 'create_confirmation',
                            lambda *a, **k: None)

        result = blog_tools.delete_blog(make_ctx(db), blog_id='b1')

        # Refuse, do not proceed unconfirmed.
        assert result['ok'] is False
        assert result['error'] == 'confirmation_failed'
        assert 'b1' in db.blogs


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------

class TestOwnership:
    """No tool takes a user id, and every one re-checks the context's."""

    def _foreign_blog(self):
        db = FakeDb()
        db.blogs['other'] = {'title': "Someone else's", 'status': 'DRAFT',
                             'author_id': 'u2', 'site_owner_id': 'u2',
                             'content': {'markdown': 'text ' * 200}}
        return db

    def test_get_blog_hides_another_users_post(self):
        result = blog_tools.get_blog(make_ctx(self._foreign_blog()),
                                    blog_id='other')
        assert result['ok'] is False
        # Not "forbidden": a distinguishable 403 would confirm the id exists.
        assert result['error'] == 'not_found'

    def test_edit_blog_hides_another_users_post(self):
        result = blog_tools.edit_blog(make_ctx(self._foreign_blog()),
                                     instructions='tighten it', blog_id='other')
        assert result['ok'] is False
        assert result['error'] == 'not_found'

    def test_delete_blog_hides_another_users_post(self):
        db = self._foreign_blog()
        result = blog_tools.delete_blog(make_ctx(db), blog_id='other')
        assert result['ok'] is False
        assert result['error'] == 'not_found'
        assert db.confirmations == {}

    def test_an_admin_reaches_their_own_sites_posts(self):
        db = self._foreign_blog()
        db.blogs['other']['site_owner_id'] = 'u1'
        ctx = make_ctx(db, user_role='ADMIN')

        result = blog_tools.get_blog(ctx, blog_id='other')

        assert result['ok'] is True

    def test_an_admin_does_not_reach_another_sites_posts(self):
        ctx = make_ctx(self._foreign_blog(), user_role='ADMIN')
        result = blog_tools.get_blog(ctx, blog_id='other')
        assert result['ok'] is False

    def test_an_outline_belonging_to_someone_else_is_invisible(self):
        db = FakeDb()
        outline_id = db.create_outline_record('u2', 's9', {'title': 'Theirs'})
        result = blog_tools.create_blog(make_ctx(db), outline_id=outline_id)
        assert result['error'] == 'outline_not_found'


# ---------------------------------------------------------------------------
# Focus resolution
# ---------------------------------------------------------------------------

class TestFocus:
    """"Make the intro punchier" has to resolve without an id."""

    def test_reading_a_post_puts_it_in_focus(self):
        db = FakeDb()
        db.blogs['b1'] = {'title': 'Pricing', 'status': 'DRAFT',
                          'author_id': 'u1', 'site_owner_id': 'u1',
                          'content': {'markdown': '## A\ntext'}}
        ctx = make_ctx(db)

        blog_tools.get_blog(ctx, blog_id='b1')

        assert ctx.focus_blog_id == 'b1'
        assert ctx.focus_blog_title == 'Pricing'

    def test_a_single_search_result_is_focused(self):
        db = FakeDb()
        db.blogs['b1'] = {'title': 'Pricing pages', 'status': 'DRAFT',
                          'author_id': 'u1', 'site_owner_id': 'u1'}
        ctx = make_ctx(db)

        blog_tools.list_blogs(ctx, search='pricing')

        assert ctx.focus_blog_id == 'b1'

    def test_several_results_are_not_focused(self):
        db = FakeDb()
        for i in (1, 2):
            db.blogs[f'b{i}'] = {'title': f'Pricing {i}', 'status': 'DRAFT',
                                 'author_id': 'u1', 'site_owner_id': 'u1'}
        ctx = make_ctx(db)

        blog_tools.list_blogs(ctx, search='pricing')

        # Guessing between two would be worse than asking.
        assert ctx.focus_blog_id == ''

    def test_a_tool_without_an_id_uses_the_focus(self):
        db = FakeDb()
        db.blogs['b1'] = {'title': 'Focused', 'status': 'DRAFT',
                          'author_id': 'u1', 'site_owner_id': 'u1'}
        ctx = make_ctx(db, focus_blog_id='b1')

        result = blog_tools.delete_blog(ctx)

        assert result['blog_id'] == 'b1'

    def test_no_focus_and_no_id_asks_rather_than_guessing(self):
        result = blog_tools.delete_blog(make_ctx())
        assert result['ok'] is False
        assert result['error'] == 'no_blog'


# ---------------------------------------------------------------------------
# Research
# ---------------------------------------------------------------------------

class TestSearchTool:

    def test_results_come_back_and_become_a_card(self):
        search = FakeSearch([
            {'title': 'A', 'url': 'https://a.example', 'snippet': 'about a'},
        ])
        ctx = make_ctx(search=search)

        result = research_tools.search_web(ctx, query='blog pricing 2026')

        assert result['ok'] is True
        assert result['result_count'] == 1
        assert any(card['kind'] == 'sources' for card in ctx.cards)

    def test_an_unconfigured_provider_is_a_state_not_an_error(self):
        ctx = make_ctx(search=FakeSearch(available=False))

        result = research_tools.search_web(ctx, query='anything')

        # The tool ran; there is simply no search here. The model is told to say
        # so rather than to invent citations.
        assert result['ok'] is True
        assert result['available'] is False
        assert 'invent' in result['message']

    def test_a_failing_provider_does_not_end_the_turn(self):
        ctx = make_ctx(search=FakeSearch(error='provider exploded'))

        result = research_tools.search_web(ctx, query='anything')

        assert result['ok'] is True
        assert result['failed'] is True
        assert result['results'] == []

    def test_an_empty_query_is_refused(self):
        result = research_tools.search_web(make_ctx(search=FakeSearch()),
                                          query='   ')
        assert result['ok'] is False
        assert result['error'] == 'missing_query'

    def test_invented_arguments_are_ignored_not_fatal(self):
        """Models pass plausible extra arguments. That must not fail a turn."""
        ctx = make_ctx(search=FakeSearch([{'title': 'A', 'url': 'u',
                                           'snippet': 's'}]))
        result = research_tools.search_web(
            ctx, query='x', region='PK', recency='week', num=3,
        )
        assert result['ok'] is True

    def test_the_result_count_is_clamped(self):
        search = FakeSearch([{'title': str(i), 'url': 'u', 'snippet': 's'}
                             for i in range(20)])
        ctx = make_ctx(search=search)
        # A model asking for 500 gets the ceiling, not a failure.
        result = research_tools.search_web(ctx, query='x', max_results=500)
        assert result['ok'] is True


# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------

class TestBudget:

    def test_a_budget_is_spent_per_tool(self):
        ctx = make_ctx(budgets={'search_web': 2})
        assert ctx.spend('search_web') == 1
        assert ctx.spend('search_web') == 2
        with pytest.raises(ToolBudgetError):
            ctx.spend('search_web')

    def test_budgets_do_not_leak_between_tools(self):
        ctx = make_ctx(budgets={'search_web': 1, 'get_blog': 1})
        ctx.spend('search_web')
        ctx.spend('get_blog')          # its own bucket
        with pytest.raises(ToolBudgetError):
            ctx.spend('search_web')

    def test_an_identical_call_is_counted(self):
        ctx = make_ctx()
        assert ctx.note_call('get_blog', {'blog_id': 'b1'}) == 1
        assert ctx.note_call('get_blog', {'blog_id': 'b1'}) == 2
        # Different arguments are a different call.
        assert ctx.note_call('get_blog', {'blog_id': 'b2'}) == 1

    def test_argument_order_does_not_disguise_a_repeat(self):
        ctx = make_ctx()
        ctx.note_call('edit_blog', {'blog_id': 'b1', 'instructions': 'x'})
        assert ctx.note_call('edit_blog',
                             {'instructions': 'x', 'blog_id': 'b1'}) == 2


# ---------------------------------------------------------------------------
# Reading approval out of text
# ---------------------------------------------------------------------------

class TestAffirmative:
    """Narrow on purpose: a false positive writes an unwanted post."""

    @pytest.mark.parametrize('text', [
        'yes', 'Yes!', 'yep', 'sure', 'ok', 'go ahead', 'go for it',
        'looks good', 'lgtm', 'perfect', 'approve', 'approved',
        'that works', 'write it', 'ship it', 'sounds good to me',
        'yes please write it',
    ])
    def test_clear_approvals(self, text):
        assert is_affirmative(text) is True

    @pytest.mark.parametrize('text', [
        '', '   ',
        'no', 'nope', 'not yet', 'no thanks',
        'yes but make section 2 shorter',
        'yes, and add a pricing section',
        'looks good except the intro',
        'ok but first change the title',
        'sure, though can we drop section 3',
        'hmm, maybe',
        'not sure about the angle',
        'what about a comparison instead?',
        'yesterday I asked for something else',
        'actually, change the audience',
        'approve it after you fix the heading',
    ])
    def test_anything_conditional_is_not_approval(self, text):
        assert is_affirmative(text) is False

    def test_a_long_message_is_never_a_bare_approval(self):
        text = 'yes ' + ('and here is a whole further brief ' * 10)
        assert is_affirmative(text) is False

    @pytest.mark.parametrize('text', [
        'make section 2 shorter', 'can we change the title',
        'not sure about that angle', 'add a pricing section',
    ])
    def test_revision_requests_are_recognised(self, text):
        assert is_revision_request(text) is True


# ---------------------------------------------------------------------------
# Editing
# ---------------------------------------------------------------------------

class TestEdit:

    def _db(self):
        db = FakeDb()
        db.blogs['b1'] = {
            'title': 'Pricing pages', 'status': 'DRAFT',
            'author_id': 'u1', 'site_owner_id': 'u1',
            'content': {'markdown': '## One\n' + ('word ' * 300)},
        }
        return db

    def test_an_edit_that_changed_nothing_says_so(self, monkeypatch):
        db = self._db()
        original = db.blogs['b1']['content']['markdown']

        class Unchanged:
            def edit(self, content, instructions, title=''):
                from app.agents.edit_agent import describe_change
                return {'markdown': content, 'title_suggestion': '',
                        'change': describe_change(content, content)}

        monkeypatch.setattr('app.agent.tools.blogs.EditAgent', Unchanged)

        result = blog_tools.edit_blog(make_ctx(db), instructions='do a thing',
                                     blog_id='b1')

        assert result['ok'] is True
        assert result['changed'] is False
        # And nothing was written over the post.
        assert db.blogs['b1']['content']['markdown'] == original

    def test_a_retitle_is_proposed_not_applied(self, monkeypatch):
        db = self._db()

        class Retitler:
            def edit(self, content, instructions, title=''):
                from app.agents.edit_agent import describe_change
                new = content + '\n\nAnd a further paragraph.'
                return {'markdown': new, 'title_suggestion': 'A Much Better Title',
                        'change': describe_change(content, new)}

        monkeypatch.setattr('app.agent.tools.blogs.EditAgent', Retitler)
        monkeypatch.setattr('app.agent.tools.blogs.FormattingAgent',
                            _StubFormatter)

        result = blog_tools.edit_blog(make_ctx(db), instructions='extend it',
                                      blog_id='b1')

        assert result['ok'] is True
        # The slug follows the title, and a slug change breaks inbound links --
        # so a retitle is offered, never taken unilaterally.
        assert result['title_changed'] is False
        assert result['title_suggestion'] == 'A Much Better Title'
        assert db.blogs['b1']['title'] == 'Pricing pages'
        assert 'URL' in result['message']

    def test_the_stored_content_keeps_its_structure(self, monkeypatch):
        """Regression guard: an edit must not flatten the content dict.

        The public site and the draft editor read ``content.html`` and
        ``content.toc`` directly. Writing a bare markdown string over the
        structured dict is how a published post loses its table of contents.
        """
        db = self._db()

        class Extender:
            def edit(self, content, instructions, title=''):
                from app.agents.edit_agent import describe_change
                new = content + '\n\n## Two\nmore words here'
                return {'markdown': new, 'title_suggestion': '',
                        'change': describe_change(content, new)}

        monkeypatch.setattr('app.agent.tools.blogs.EditAgent', Extender)
        monkeypatch.setattr('app.agent.tools.blogs.FormattingAgent',
                            _StubFormatter)

        blog_tools.edit_blog(make_ctx(db), instructions='add a section',
                             blog_id='b1')

        stored = db.blogs['b1']['content']
        assert isinstance(stored, dict)
        for key in ('body', 'html', 'markdown', 'toc', 'toc_html'):
            assert key in stored, key
        assert stored['toc'], 'the table of contents was dropped'

    @pytest.mark.parametrize('flag', ['false', 'False', 'no', '0', '', None, False])
    def test_a_stringy_false_does_not_retitle(self, monkeypatch, flag):
        """``bool("false")`` is True, and here that would change the post's URL."""
        db = self._db()

        class Retitler:
            def edit(self, content, instructions, title=''):
                from app.agents.edit_agent import describe_change
                new = content + '\n\nMore.'
                return {'markdown': new, 'title_suggestion': 'A New Title',
                        'change': describe_change(content, new)}

        monkeypatch.setattr('app.agent.tools.blogs.EditAgent', Retitler)
        monkeypatch.setattr('app.agent.tools.blogs.FormattingAgent',
                            _StubFormatter)

        result = blog_tools.edit_blog(make_ctx(db), instructions='extend it',
                                      blog_id='b1', apply_title=flag)

        assert result['title_changed'] is False
        assert db.blogs['b1']['title'] == 'Pricing pages'

    @pytest.mark.parametrize('flag', ['true', 'True', 'yes', '1', True])
    def test_an_explicit_true_does_retitle(self, monkeypatch, flag):
        db = self._db()

        class Retitler:
            def edit(self, content, instructions, title=''):
                from app.agents.edit_agent import describe_change
                new = content + '\n\nMore.'
                return {'markdown': new, 'title_suggestion': 'A New Title',
                        'change': describe_change(content, new)}

        monkeypatch.setattr('app.agent.tools.blogs.EditAgent', Retitler)
        monkeypatch.setattr('app.agent.tools.blogs.FormattingAgent',
                            _StubFormatter)

        result = blog_tools.edit_blog(make_ctx(db), instructions='retitle it',
                                      blog_id='b1', apply_title=flag)

        assert result['title_changed'] is True
        assert db.blogs['b1']['title'] == 'A New Title'

    def test_a_missing_instruction_is_refused(self):
        result = blog_tools.edit_blog(make_ctx(self._db()), instructions='',
                                      blog_id='b1')
        assert result['ok'] is False
        assert result['error'] == 'missing_instructions'


class _StubFormatter:
    """Stands in for FormattingAgent without a model or markdown pipeline."""

    def format_blog(self, content, title=''):
        import re
        headings = re.findall(r'^##\s+(.+)$', content, re.MULTILINE)
        return {
            'html': f'<article>{content}</article>',
            'toc': [{'level': 2, 'text': h, 'slug': h.lower()} for h in headings],
            'toc_html': '<ul></ul>',
            'reading_time_text': '5 min read',
            'reading_time_minutes': 5,
            'statistics': {'word_count': len(content.split())},
            'has_code_blocks': False,
            'has_images': False,
            'has_tables': False,
        }


# ---------------------------------------------------------------------------
# Writing, end to end, with the model stubbed
# ---------------------------------------------------------------------------

class TestCreateBlog:

    def test_an_approved_outline_produces_a_draft(self, monkeypatch):
        db = FakeDb()
        outline_id = db.create_outline_record('u1', 's1', {
            'title': 'Pricing pages that convert',
            'angle': 'cost per lead, not vanity metrics',
            'sections': [
                {'heading': 'What actually converts', 'points': ['a claim']},
                {'heading': 'What to measure', 'points': ['another']},
            ],
            'sources': [],
        }, topic='pricing pages', tone='conversational', length='medium')
        db.approve_outline(outline_id, 'u1')

        monkeypatch.setattr('app.agent.tools.blogs.ContentAgent', _StubWriter)
        monkeypatch.setattr('app.agent.tools.blogs.FormattingAgent',
                            _StubFormatter)
        monkeypatch.setattr('app.agent.tools.blogs.CategoryAgent',
                            _StubCategoriser)

        ctx = make_ctx(db)
        result = blog_tools.create_blog(ctx, outline_id=outline_id)

        assert result['ok'] is True
        blog_id = result['blog_id']
        assert blog_id in db.blogs
        # Filed as a draft, never published: publishing stays a human action.
        assert db.blogs[blog_id]['status'] == 'DRAFT'
        assert ctx.focus_blog_id == blog_id
        assert db.outlines[outline_id]['blog_id'] == blog_id

    def test_the_document_matches_what_the_create_screen_writes(self, monkeypatch):
        """The shape is load-bearing.

        Drafts, the approval queue, the public site and the SEO tools all read
        this document. A chat-written post that is subtly different from a
        create-screen post is a bug in every one of those screens at once.
        """
        db = FakeDb()
        outline_id = db.create_outline_record('u1', 's1', {
            'title': 'T', 'sections': [{'heading': 'A', 'points': []}],
        })
        db.approve_outline(outline_id, 'u1')

        monkeypatch.setattr('app.agent.tools.blogs.ContentAgent', _StubWriter)
        monkeypatch.setattr('app.agent.tools.blogs.FormattingAgent',
                            _StubFormatter)
        monkeypatch.setattr('app.agent.tools.blogs.CategoryAgent',
                            _StubCategoriser)

        result = blog_tools.create_blog(make_ctx(db), outline_id=outline_id)
        document = db.blogs[result['blog_id']]

        for key in ('title', 'content', 'formatting', 'seo', 'metadata',
                    'category', 'status', 'author_id', 'author', 'outline'):
            assert key in document, key
        for key in ('body', 'html', 'markdown', 'toc', 'toc_html'):
            assert key in document['content'], key
        # Provenance: the only durable answer to "why does this post exist".
        assert document['metadata']['source'] == 'chat_agent'
        assert document['metadata']['outline_id'] == outline_id
        assert document['metadata']['chat_session_id'] == 's1'

    def test_a_chat_written_post_lands_in_the_generation_history(self, monkeypatch):
        db = FakeDb()
        outline_id = db.create_outline_record('u1', 's1', {
            'title': 'T', 'sections': [{'heading': 'A', 'points': []}],
        }, topic='a topic')
        db.approve_outline(outline_id, 'u1')

        monkeypatch.setattr('app.agent.tools.blogs.ContentAgent', _StubWriter)
        monkeypatch.setattr('app.agent.tools.blogs.FormattingAgent',
                            _StubFormatter)
        monkeypatch.setattr('app.agent.tools.blogs.CategoryAgent',
                            _StubCategoriser)

        blog_tools.create_blog(make_ctx(db), outline_id=outline_id)

        assert len(db.generations) == 1
        assert db.generations[0]['status'] == 'completed'

    def test_a_failed_save_does_not_claim_success(self, monkeypatch):
        db = FakeDb()
        outline_id = db.create_outline_record('u1', 's1', {'title': 'T'})
        db.approve_outline(outline_id, 'u1')

        monkeypatch.setattr('app.agent.tools.blogs.ContentAgent', _StubWriter)
        monkeypatch.setattr('app.agent.tools.blogs.FormattingAgent',
                            _StubFormatter)
        monkeypatch.setattr('app.agent.tools.blogs.CategoryAgent',
                            _StubCategoriser)
        monkeypatch.setattr(db, 'create_draft', lambda *a, **k: None)

        result = blog_tools.create_blog(make_ctx(db), outline_id=outline_id)

        assert result['ok'] is False
        assert result['error'] == 'save_failed'

    def test_the_draft_is_streamed_to_the_turn_log(self, monkeypatch):
        from app.agent.events import DRAFT, TurnLog

        db = FakeDb()
        outline_id = db.create_outline_record('u1', 's1', {'title': 'T'})
        db.approve_outline(outline_id, 'u1')

        monkeypatch.setattr('app.agent.tools.blogs.ContentAgent', _StubWriter)
        monkeypatch.setattr('app.agent.tools.blogs.FormattingAgent',
                            _StubFormatter)
        monkeypatch.setattr('app.agent.tools.blogs.CategoryAgent',
                            _StubCategoriser)

        log = TurnLog('t1', 's1', 'u1')
        blog_tools.create_blog(make_ctx(db, log=log), outline_id=outline_id)

        drafted = [e for e in log.since(-1)['events'] if e['type'] == DRAFT]
        assert drafted, 'the post was written with no draft events'
        assert 'word' in drafted[0]['data']['text']


class _StubWriter:
    """Stands in for ContentAgent: streams a plausible post, no model."""

    def stream_from_outline(self, outline, on_content=None, **kwargs):
        text = '## ' + ((outline.get('sections') or [{}])[0].get('heading', 'A')) \
            + '\n' + ('word ' * 400)
        if on_content:
            # In chunks, as the real stream arrives.
            for start in range(0, len(text), 120):
                on_content(text[start:start + 120])
        return {'markdown': text, 'streamed': True, 'partial': False,
                'plan': [], 'duration_ms': 1.0}


class _StubCategoriser:
    def categorize_blog(self, title, content, categories=None):
        return 'Marketing'
