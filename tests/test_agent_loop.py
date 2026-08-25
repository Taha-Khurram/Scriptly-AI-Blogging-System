"""Loop-level tests: tool chaining, the runaway guards, and durability.

The loop is driven by a scripted fake client, because the properties worth
testing here cannot be produced on demand by a real model. You cannot ask Gemini
to please loop forever so the iteration cap can be observed; you script a client
that always asks for another tool call and assert the loop stops anyway.

Four groups:

* **Chaining** -- several tool calls resolved inside one turn, which is the
  requirement that a multi-step request ("research it, outline it") is one
  message rather than three.
* **The guards** -- iteration cap, per-tool budget, duplicate suppression, and
  tool failures that must not end a turn. These are the difference between an
  agent and an incident.
* **Durability** -- every exit path persists a reply and closes the turn log.
  A turn that ends without either leaves a browser attached to nothing and a
  conversation whose last message is the user's.
* **The gate, from above** -- the loop must not be able to route around the
  approval refusal its own tools return.
"""
from __future__ import annotations

import pytest

from app.agent import events
from app.agent.events import TurnLog
from app.agent.loop import AgentLoop

from tests.test_agent_tools import (
    FakeDb, FakeSearch, _StubCategoriser, _StubFormatter, _StubOutlineAgent,
    _StubWriter,
)


# ---------------------------------------------------------------------------
# A scripted model
# ---------------------------------------------------------------------------

class ScriptedClient:
    """Replays a list of turns in place of Gemini.

    Each script entry is a list of ``('text', str)`` / ``('call', {...})``
    tuples -- exactly what :meth:`GeminiClient.stream_with_tools` yields. When
    the script runs out, the last entry repeats: that is what makes the
    iteration-cap test possible, since a model that will not stop is precisely
    the thing being guarded against.
    """

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.contents_seen = []
        self.instructions_seen = []

    def stream_with_tools(self, contents, tools, **kwargs):
        self.contents_seen.append(list(contents))
        self.instructions_seen.append(kwargs.get('system_instruction', ''))
        index = min(self.calls, len(self.script) - 1)
        self.calls += 1
        for item in self.script[index]:
            yield item


class ExplodingClient:
    def stream_with_tools(self, contents, tools, **kwargs):
        from app.services.gemini_client import GeminiQuotaError
        raise GeminiQuotaError('at its request limit')
        yield  # pragma: no cover - makes this a generator


def run(db, client, message='write me something', session=None, **kwargs):
    """Drive one turn and hand back ``(result, log)``."""
    log = TurnLog('turn-1', 's1', 'u1')
    loop = AgentLoop(db, search=kwargs.pop('search', None), **kwargs)
    loop.client = client
    result = loop.run(
        log=log,
        session=session if session is not None else {'id': 's1', 'blog_count': 0},
        user_id='u1', user_name='Ada', user_role='USER',
        message=message, history=kwargs.pop('history', None),
    )
    return result, log


def types_in(log):
    return [event['type'] for event in log.since(-1)['events']]


def stub_writers(monkeypatch):
    monkeypatch.setattr('app.agent.tools.blogs.ContentAgent', _StubWriter)
    monkeypatch.setattr('app.agent.tools.blogs.FormattingAgent', _StubFormatter)
    monkeypatch.setattr('app.agent.tools.blogs.CategoryAgent', _StubCategoriser)


# ---------------------------------------------------------------------------
# Chaining
# ---------------------------------------------------------------------------

class TestChaining:

    def test_a_plain_answer_takes_one_round_trip(self):
        db = FakeDb()
        client = ScriptedClient([[('text', 'Hello — what shall we write?')]])

        result, log = run(db, client)

        assert result.status == 'completed'
        assert result.iterations == 1
        assert 'what shall we write' in result.text
        assert types_in(log)[-1] == events.DONE

    def test_search_then_outline_is_one_turn(self, monkeypatch):
        """The requirement: "research this and outline it" is one message."""
        monkeypatch.setattr('app.agent.tools.outlines.OutlineAgent',
                            _StubOutlineAgent)
        db = FakeDb()
        search = FakeSearch([
            {'title': 'A source', 'url': 'https://a.example', 'snippet': 'facts'},
        ])
        client = ScriptedClient([
            [('text', 'Let me look that up.'),
             ('call', {'name': 'search_web', 'args': {'query': 'pricing 2026'}})],
            [('call', {'name': 'create_outline',
                       'args': {'topic': 'pricing pages',
                                'research_notes': 'facts'}})],
            [('text', 'Here is the plan — does it work?')],
        ])

        result, log = run(db, client, search=search)

        assert result.status == 'completed'
        assert result.iterations == 3
        assert [c['name'] for c in result.tool_calls] == [
            'search_web', 'create_outline',
        ]
        assert search.calls == ['pricing 2026']
        # The outline reached the user as a card, and it is unapproved.
        outline_cards = [c for c in result.cards if c['kind'] == 'outline']
        assert len(outline_cards) == 1
        assert outline_cards[0]['data']['status'] == 'pending_approval'

    def test_text_before_a_tool_call_is_streamed_first(self):
        """"Let me look that up" must be on screen before the search runs."""
        db = FakeDb()
        client = ScriptedClient([
            [('text', 'Let me check your drafts.'),
             ('call', {'name': 'list_blogs', 'args': {}})],
            [('text', 'Nothing there yet.')],
        ])

        _, log = run(db, client)

        order = types_in(log)
        assert order.index(events.TOKEN) < order.index(events.TOOL_START)

    def test_the_model_sees_its_own_calls_and_their_results(self):
        db = FakeDb()
        client = ScriptedClient([
            [('call', {'name': 'list_blogs', 'args': {}})],
            [('text', 'All done.')],
        ])

        run(db, client)

        # Second round trip: user message, the model's call, the response.
        second = client.contents_seen[1]
        roles = [content['role'] for content in second]
        assert roles[-2:] == ['model', 'user']
        assert 'function_call' in second[-2]['parts'][0]
        assert 'function_response' in second[-1]['parts'][0]

    def test_every_call_gets_a_response_part(self):
        """The API rejects a turn whose calls and responses do not pair up."""
        db = FakeDb()
        client = ScriptedClient([
            [('call', {'name': 'list_blogs', 'args': {}}),
             ('call', {'name': 'list_blogs', 'args': {'status': 'DRAFT'}})],
            [('text', 'Done.')],
        ])

        run(db, client)

        second = client.contents_seen[1]
        calls = [p for p in second[-2]['parts'] if 'function_call' in p]
        responses = [p for p in second[-1]['parts'] if 'function_response' in p]
        assert len(calls) == len(responses) == 2


# ---------------------------------------------------------------------------
# The guards
# ---------------------------------------------------------------------------

class TestRunawayGuards:

    def test_the_iteration_cap_stops_a_model_that_will_not_stop(self):
        db = FakeDb()
        # One entry, so the script repeats forever: every round trip asks for
        # another tool call and never answers.
        client = ScriptedClient([
            [('call', {'name': 'list_blogs', 'args': {'search': 'x'}})],
        ])

        result, log = run(db, client, max_iterations=4)

        assert result.iterations == 4
        assert client.calls == 4
        assert result.status == 'completed'
        # And the user is told, rather than being handed an empty bubble.
        assert result.text
        assert types_in(log)[-1] == events.DONE

    def test_an_identical_call_is_answered_not_repeated(self):
        db = FakeDb()
        db.blogs['b1'] = {'title': 'A post', 'status': 'DRAFT',
                          'author_id': 'u1', 'site_owner_id': 'u1'}
        client = ScriptedClient([
            [('call', {'name': 'get_blog', 'args': {'blog_id': 'b1'}})],
            [('call', {'name': 'get_blog', 'args': {'blog_id': 'b1'}})],
            [('text', 'Fine.')],
        ])

        result, _ = run(db, client)

        assert len(result.tool_calls) == 2
        assert result.tool_calls[0]['ok'] is True
        # The second was suppressed before it ran.
        assert result.tool_calls[1]['ok'] is False
        assert result.tool_calls[1]['summary'] == 'duplicate call suppressed'

    def test_a_per_tool_budget_ends_a_search_spree(self):
        db = FakeDb()
        search = FakeSearch([{'title': 'A', 'url': 'u', 'snippet': 's'}])
        # Each query differs, so duplicate suppression does not catch it -- the
        # budget has to.
        client = ScriptedClient([
            [('call', {'name': 'search_web', 'args': {'query': f'q{i}'}})]
            for i in range(1, 8)
        ] + [[('text', 'Enough.')]])

        result, _ = run(db, client, search=search, max_iterations=9)

        # DEFAULT_BUDGETS caps search_web at 3 per turn.
        assert len(search.calls) == 3
        refused = [c for c in result.tool_calls
                   if c['summary'] == 'budget exhausted']
        assert refused, 'the budget never refused a call'

    def test_a_tool_that_raises_does_not_end_the_turn(self, monkeypatch):
        db = FakeDb()

        def explode(ctx, **kwargs):
            raise RuntimeError('the database fell over')

        # The spec is replaced rather than the module attribute: the registry
        # captured the function reference at import time, so patching
        # app.agent.tools.blogs would leave BY_NAME pointing at the original.
        from app.agent import registry
        monkeypatch.setitem(registry.BY_NAME, 'list_blogs',
                            _spec_with('list_blogs', explode))

        client = ScriptedClient([
            [('call', {'name': 'list_blogs', 'args': {}})],
            [('text', 'That did not work, but I am still here.')],
        ])

        result, log = run(db, client)

        assert result.status == 'completed'
        assert result.tool_calls[0]['ok'] is False
        assert 'still here' in result.text
        assert types_in(log)[-1] == events.DONE

    def test_an_unknown_tool_is_corrected_not_fatal(self):
        db = FakeDb()
        client = ScriptedClient([
            [('call', {'name': 'publish_blog', 'args': {'blog_id': 'b1'}})],
            [('text', 'I cannot publish, but I can draft.')],
        ])

        result, _ = run(db, client)

        assert result.status == 'completed'
        assert result.tool_calls[0]['summary'] == 'unknown tool'

    def test_a_missing_required_argument_is_reported_precisely(self):
        db = FakeDb()
        client = ScriptedClient([
            # edit_blog requires `instructions`.
            [('call', {'name': 'edit_blog', 'args': {'blog_id': 'b1'}})],
            [('text', 'Which change did you want?')],
        ])

        result, _ = run(db, client)

        assert result.tool_calls[0]['ok'] is False
        assert result.status == 'completed'


def _spec_with(name, fn):
    """A copy of a real ToolSpec with its function swapped."""
    from app.agent.registry import BY_NAME, ToolSpec

    original = BY_NAME[name]
    return ToolSpec(original.name, fn, original.description,
                    original.parameters, original.label, original.destructive)


# ---------------------------------------------------------------------------
# Durability
# ---------------------------------------------------------------------------

class TestDurability:

    def test_a_reply_is_always_persisted(self):
        db = FakeDb()
        written = []
        db.append_chat_message = lambda *a, **k: (
            written.append((a, k)) or {'id': 'm1', 'seq': 1}
        )
        client = ScriptedClient([[('text', 'A reply.')]])

        result, _ = run(db, client)

        assert len(written) == 1
        assert written[0][0][2] == 'agent'
        assert result.message_id == 'm1'

    def test_a_turn_that_produced_no_prose_still_answers(self, monkeypatch):
        """A model that calls a tool and then says nothing must not show an
        empty bubble."""
        monkeypatch.setattr('app.agent.tools.outlines.OutlineAgent',
                            _StubOutlineAgent)
        db = FakeDb()
        db.append_chat_message = lambda *a, **k: {'id': 'm1', 'seq': 1}
        client = ScriptedClient([
            [('call', {'name': 'create_outline', 'args': {'topic': 'a topic'}})],
            [],   # says nothing at all
        ])

        result, _ = run(db, client)

        assert result.status == 'completed'
        assert result.text.strip()
        # It describes what actually happened, from the recorded cards.
        assert 'outline' in result.text.lower()

    def test_a_model_failure_becomes_a_reply_and_a_failed_log(self):
        db = FakeDb()
        db.append_chat_message = lambda *a, **k: {'id': 'm1', 'seq': 1}

        result, log = run(db, ExplodingClient())

        assert result.status == 'failed'
        # The user is told, in the thread -- not only in a banner that is gone
        # on reload.
        assert 'request limit' in result.text
        assert types_in(log)[-1] == events.ERROR
        assert log.is_terminal

    def test_work_completed_before_a_failure_is_mentioned(self, monkeypatch):
        stub_writers(monkeypatch)
        db = FakeDb()
        db.append_chat_message = lambda *a, **k: {'id': 'm1', 'seq': 1}
        outline_id = db.create_outline_record('u1', 's1', {
            'title': 'T', 'sections': [{'heading': 'A', 'points': []}],
        })
        db.approve_outline(outline_id, 'u1')

        class WriteThenDie:
            def __init__(self):
                self.calls = 0

            def stream_with_tools(self, contents, tools, **kwargs):
                from app.services.gemini_client import GeminiError
                self.calls += 1
                if self.calls == 1:
                    yield 'call', {'name': 'create_blog',
                                   'args': {'outline_id': outline_id}}
                    return
                raise GeminiError('the model fell over')

        result, _ = run(db, WriteThenDie(),
                        session={'id': 's1', 'focus_outline_id': outline_id})

        assert result.status == 'failed'
        assert result.blog_ids, 'the post was written but not recorded'
        # And the user is told the post survived, so they do not write it twice.
        assert 'not lost' in result.text

    def test_the_log_always_reaches_a_terminal_state(self):
        db = FakeDb()
        db.append_chat_message = lambda *a, **k: {'id': 'm1', 'seq': 1}

        for client in (ScriptedClient([[('text', 'fine')]]), ExplodingClient()):
            _, log = run(db, client)
            assert log.is_terminal, 'a browser would attach to this forever'

    def test_a_reply_that_could_not_be_saved_is_still_reported(self):
        db = FakeDb()
        db.append_chat_message = lambda *a, **k: None

        result, log = run(db, ScriptedClient([[('text', 'A reply.')]]))

        assert result.status == 'completed'
        assert result.message_id == ''
        assert log.is_terminal


# ---------------------------------------------------------------------------
# The gate, from the loop's side
# ---------------------------------------------------------------------------

class TestGateFromTheLoop:

    def test_the_loop_cannot_route_around_the_refusal(self, monkeypatch):
        """A model that ignores the refusal and tries again still writes nothing."""
        stub_writers(monkeypatch)
        db = FakeDb()
        db.append_chat_message = lambda *a, **k: {'id': 'm1', 'seq': 1}
        outline_id = db.create_outline_record('u1', 's1', {
            'title': 'T', 'sections': [{'heading': 'A', 'points': []}],
        })

        client = ScriptedClient([
            [('call', {'name': 'create_blog', 'args': {'outline_id': outline_id}})],
            # Refused. It tries a slightly different call to dodge duplicate
            # suppression, which is exactly what a stubborn model does.
            [('call', {'name': 'create_blog',
                       'args': {'outline_id': outline_id, 'tone': 'casual'}})],
            [('text', 'I need you to approve the outline first.')],
        ])

        result, _ = run(db, client)

        assert db.blogs == {}
        assert result.blog_ids == []
        assert all(c['ok'] is False for c in result.tool_calls)

    def test_approval_then_writing_works_in_one_turn(self, monkeypatch):
        """The legitimate chain: the user said yes, so the turn completes it."""
        stub_writers(monkeypatch)
        db = FakeDb()
        db.append_chat_message = lambda *a, **k: {'id': 'm1', 'seq': 1}
        outline_id = db.create_outline_record('u1', 's1', {
            'title': 'T', 'sections': [{'heading': 'A', 'points': ['p']}],
        })

        client = ScriptedClient([
            [('call', {'name': 'submit_outline_approval',
                       'args': {'outline_id': outline_id}})],
            [('call', {'name': 'create_blog', 'args': {'outline_id': outline_id}})],
            [('text', 'Written and saved to your drafts.')],
        ])

        result, log = run(db, client, message='yes, go ahead',
                          session={'id': 's1', 'focus_outline_id': outline_id})

        assert db.outlines[outline_id]['status'] == 'approved'
        assert result.blog_ids, 'the approved post was not written'
        assert events.DRAFT in types_in(log)

    def test_the_state_block_tells_the_model_what_is_pending(self):
        db = FakeDb()
        db.append_chat_message = lambda *a, **k: {'id': 'm1', 'seq': 1}
        outline_id = db.create_outline_record('u1', 's1', {'title': 'Pending one'})
        client = ScriptedClient([[('text', 'ok')]])

        run(db, client, session={'id': 's1', 'focus_outline_id': outline_id})

        instruction = client.instructions_seen[0]
        assert 'AWAITING APPROVAL' in instruction
        assert outline_id in instruction

    def test_an_unconfigured_search_is_declared_in_the_prompt(self):
        db = FakeDb()
        db.append_chat_message = lambda *a, **k: {'id': 'm1', 'seq': 1}
        client = ScriptedClient([[('text', 'ok')]])

        run(db, client, search=FakeSearch(available=False))

        assert 'Web search is NOT configured' in client.instructions_seen[0]
