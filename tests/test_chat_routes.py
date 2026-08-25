"""Route tests for the chat surface.

Driven through the real application (see ``tests/conftest.py`` -- the factory is
inert under ``TestingConfig``, so a route test exercises the whole request
pipeline: middleware, error handlers, security headers and all).

The turn itself is never allowed to run. ``_no_turn`` replaces the task pool's
submit with a no-op, so what is under test is the *contract* of these endpoints
-- what they accept, what they refuse, what they return and what they leave the
browser able to do -- rather than the loop, which has its own file.

Two things are worth reading closely:

* :class:`TestAccess`, because a chat holds text somebody typed and every read
  here has to answer identically for "does not exist" and "not yours".
* :class:`TestApprovalEndpoint` and :class:`TestConfirm`, because these two
  routes *are* the human-in-the-loop guarantee. If either becomes reachable by
  anything other than a user's own request, the approval step is decoration.
"""
from __future__ import annotations

import json

import pytest


SESSION = {
    'id': 's1',
    'user_id': 'admin-1',
    'title': 'Pricing post',
    'preview': '',
    'status': 'active',
    'message_count': 2,
    'blog_count': 0,
    'focus_blog_id': '',
    'focus_blog_title': '',
    'focus_outline_id': '',
    'created_at': '2026-08-25T10:00:00+00:00',
    'updated_at': '2026-08-25T10:05:00+00:00',
}


@pytest.fixture
def no_turn(monkeypatch):
    """Accept turns without running them.

    The loop has its own tests with a scripted model; letting it run here would
    make every route test depend on Gemini. The submitted arguments are captured
    so a test can assert on *what* would have run.
    """
    submitted = []

    def fake_submit(task_id, fn, *args, **kwargs):
        submitted.append({'task_id': task_id, 'fn': fn, 'kwargs': kwargs})

    monkeypatch.setattr('app.utils.task_manager.task_manager.submit', fake_submit)
    return submitted


@pytest.fixture(autouse=True)
def clean_turns():
    """The turn registry is a process-wide singleton, like the task manager.

    Without this, a turn opened by one test is still 'running' in the next, and
    the one-turn-at-a-time check would fail tests that never started a turn.
    """
    from app.agent.events import turns

    turns._turns.clear()
    yield
    turns._turns.clear()


def post(client, url, payload=None):
    return client.post(url, data=json.dumps(payload or {}),
                       content_type='application/json')


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------

class TestPage:

    def test_the_page_requires_a_session(self, client, mock_db):
        response = client.get('/chat')
        assert response.status_code == 302
        assert '/login' in response.headers['Location']

    def test_the_page_renders_with_no_conversations(self, signed_in, mock_db):
        response = signed_in().get('/chat')
        assert response.status_code == 200
        assert b'data-chat' in response.data

    def test_a_deep_linked_conversation_is_rendered_server_side(
            self, signed_in, mock_db):
        """First paint carries the conversation, not a shell that fetches it."""
        mock_db.get_chat_session.return_value = SESSION
        mock_db.get_chat_messages.return_value = [
            {'id': 'm1', 'seq': 0, 'role': 'user',
             'text': 'Write about pricing pages', 'cards': [], 'tool_calls': []},
            {'id': 'm2', 'seq': 1, 'role': 'agent', 'text': 'Here is a plan.',
             'cards': [{'kind': 'outline', 'data': {'title': 'Pricing'}}],
             'tool_calls': [{'name': 'create_outline', 'summary': 'ok'}]},
        ]

        response = signed_in().get('/chat?s=s1')

        assert response.status_code == 200
        assert b'Write about pricing pages' in response.data
        assert b'Here is a plan.' in response.data
        # And the cards ride along as JSON for the one card renderer.
        assert b'data-chat-bootstrap' in response.data

    def test_an_unknown_conversation_renders_the_empty_state(
            self, signed_in, mock_db):
        mock_db.get_chat_session.return_value = None
        response = signed_in().get('/chat?s=nope')
        # Not a 404: a stale link should land on a usable screen, not an error.
        assert response.status_code == 200
        assert b'data-state="blank"' in response.data


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------

class TestAccess:
    """A conversation holds text somebody typed. Not-yours reads as not-found."""

    @pytest.mark.parametrize('method,url', [
        ('get', '/api/chat/sessions/s1'),
        ('post', '/api/chat/sessions/s1/messages'),
        ('delete', '/api/chat/sessions/s1'),
    ])
    def test_someone_elses_conversation_is_not_found(
            self, signed_in, mock_db, method, url):
        # The repository returns None for a session that is missing *or* not
        # theirs -- the route cannot tell the difference, which is the point.
        mock_db.get_chat_session.return_value = None
        mock_db.delete_chat_session.return_value = False

        client = signed_in()
        response = getattr(client, method)(
            url, data=json.dumps({'message': 'hello'}),
            content_type='application/json',
        )

        assert response.status_code == 404

    def test_api_calls_without_a_session_are_401_not_a_redirect(
            self, client, mock_db):
        """A fetch() must get JSON it can read, not an HTML login page."""
        response = post(client, '/api/chat/sessions')
        assert response.status_code == 401
        assert response.is_json

    def test_another_users_turn_is_not_found(self, signed_in, mock_db):
        from app.agent.events import turns

        log = turns.open('s9', 'someone-else')
        response = signed_in().get(f'/api/chat/turns/{log.turn_id}')
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

class TestSessions:

    def test_a_conversation_can_be_opened(self, signed_in, mock_db):
        mock_db.get_chat_session.return_value = SESSION
        response = post(signed_in(), '/api/chat/sessions')
        assert response.status_code == 201
        assert response.get_json()['session']['id'] == 's1'

    def test_a_failed_create_is_reported(self, signed_in, mock_db):
        mock_db.create_chat_session.return_value = None
        response = post(signed_in(), '/api/chat/sessions')
        assert response.status_code == 500

    def test_deleting_says_the_posts_survive(self, signed_in, mock_db):
        """The response has to be explicit.

        A conversation that produced five published posts looks, from the
        sidebar, like the thing those posts live in. A delete that quietly took
        more than expected is the one nobody can undo.
        """
        mock_db.get_chat_session.return_value = SESSION
        mock_db.delete_chat_session.return_value = True

        response = signed_in().delete('/api/chat/sessions/s1')

        assert response.status_code == 200
        assert 'untouched' in response.get_json()['message']

    def test_renaming_requires_a_title(self, signed_in, mock_db):
        mock_db.get_chat_session.return_value = SESSION
        response = post(signed_in(), '/api/chat/sessions/s1', {'title': '  '})
        assert response.status_code == 400

    def test_the_rail_pages_by_cursor(self, signed_in, mock_db):
        mock_db.list_chat_sessions.return_value = {
            'items': [SESSION], 'has_more': True,
            'next_cursor': '2026-08-25T09:00:00+00:00',
        }
        response = signed_in().get('/api/chat/sessions?before=2026-08-25T10:00:00Z')
        assert response.status_code == 200
        assert response.get_json()['has_more'] is True


# ---------------------------------------------------------------------------
# Sending a message
# ---------------------------------------------------------------------------

class TestSendMessage:

    def test_a_message_is_accepted_with_a_turn_id(self, signed_in, mock_db, no_turn):
        """202, not 200: the reply does not exist yet and will not for minutes."""
        mock_db.get_chat_session.return_value = SESSION

        response = post(signed_in(), '/api/chat/sessions/s1/messages',
                        {'message': 'Write about pricing pages'})

        assert response.status_code == 202
        payload = response.get_json()
        assert payload['turn_id']
        assert payload['message']['text'] == 'Write about pricing pages'
        assert len(no_turn) == 1

    def test_the_user_message_is_persisted_before_the_turn_starts(
            self, signed_in, mock_db, no_turn):
        """If the worker never runs, the message must still be in the log."""
        mock_db.get_chat_session.return_value = SESSION

        post(signed_in(), '/api/chat/sessions/s1/messages', {'message': 'hello'})

        written = mock_db.append_chat_message.call_args
        assert written.args[2] == 'user'
        assert written.args[3] == 'hello'

    def test_history_is_read_before_the_new_message_is_appended(
            self, signed_in, mock_db, no_turn):
        """Otherwise the new message reaches the model twice."""
        mock_db.get_chat_session.return_value = SESSION
        mock_db.get_chat_messages.return_value = [
            {'id': 'm0', 'seq': 0, 'role': 'user', 'text': 'earlier'},
        ]

        post(signed_in(), '/api/chat/sessions/s1/messages', {'message': 'new one'})

        history = no_turn[0]['kwargs']['history']
        assert [m['text'] for m in history] == ['earlier']
        assert no_turn[0]['kwargs']['message'] == 'new one'

    def test_an_empty_message_is_refused(self, signed_in, mock_db, no_turn):
        mock_db.get_chat_session.return_value = SESSION
        response = post(signed_in(), '/api/chat/sessions/s1/messages',
                        {'message': '   '})
        assert response.status_code == 400
        assert not no_turn

    def test_an_oversized_message_is_refused(self, signed_in, mock_db, no_turn):
        mock_db.get_chat_session.return_value = SESSION
        response = post(signed_in(), '/api/chat/sessions/s1/messages',
                        {'message': 'x' * 9000})
        assert response.status_code == 400
        assert not no_turn

    def test_a_failed_save_does_not_start_a_turn(self, signed_in, mock_db, no_turn):
        mock_db.get_chat_session.return_value = SESSION
        mock_db.append_chat_message.return_value = None

        response = post(signed_in(), '/api/chat/sessions/s1/messages',
                        {'message': 'hello'})

        assert response.status_code == 500
        assert not no_turn

    def test_one_turn_at_a_time_per_conversation(self, signed_in, mock_db, no_turn):
        """Two concurrent turns would interleave two answers in one thread."""
        mock_db.get_chat_session.return_value = SESSION
        client = signed_in()

        first = post(client, '/api/chat/sessions/s1/messages', {'message': 'one'})
        second = post(client, '/api/chat/sessions/s1/messages', {'message': 'two'})

        assert first.status_code == 202
        assert second.status_code == 409
        payload = second.get_json()
        assert payload['error'] == 'agent_busy'
        # The in-flight turn id comes back, so the browser attaches to it rather
        # than showing an error for something that is working.
        assert payload['turn_id'] == first.get_json()['turn_id']
        assert len(no_turn) == 1


# ---------------------------------------------------------------------------
# Watching a turn
# ---------------------------------------------------------------------------

class TestWatching:

    def _log(self, user_id='admin-1'):
        from app.agent.events import turns
        return turns.open('s1', user_id)

    def test_polling_replays_from_the_start_by_default(self, signed_in, mock_db):
        log = self._log()
        log.status_('thinking', 'Thinking')
        log.token('Hello')

        response = signed_in().get(f'/api/chat/turns/{log.turn_id}')

        payload = response.get_json()
        assert [e['type'] for e in payload['events']] == ['status', 'token']
        assert payload['cursor'] == 1
        assert payload['status'] == 'running'

    def test_polling_from_a_cursor_returns_only_what_is_new(self, signed_in, mock_db):
        log = self._log()
        log.status_('thinking')
        log.token('Hello')
        log.done()

        response = signed_in().get(f'/api/chat/turns/{log.turn_id}?cursor=0')

        payload = response.get_json()
        assert [e['type'] for e in payload['events']] == ['token', 'done']
        assert payload['status'] == 'completed'

    def test_a_cursor_past_the_end_is_empty_not_an_error(self, signed_in, mock_db):
        log = self._log()
        log.token('Hi')
        response = signed_in().get(f'/api/chat/turns/{log.turn_id}?cursor=99')
        assert response.status_code == 200
        assert response.get_json()['events'] == []

    def test_the_stream_is_sse_and_unbuffered(self, signed_in, mock_db):
        log = self._log()
        log.token('Hello')
        log.done()

        response = signed_in().get(f'/api/chat/turns/{log.turn_id}/stream')

        assert response.status_code == 200
        assert response.mimetype == 'text/event-stream'
        # nginx buffers proxied responses by default, which would hold the whole
        # stream until the turn ended -- turning SSE into a slow single response.
        assert response.headers['X-Accel-Buffering'] == 'no'
        assert 'no-cache' in response.headers['Cache-Control']

    def test_the_stream_carries_ids_named_events_and_ends(self, signed_in, mock_db):
        log = self._log()
        log.status_('thinking', 'Thinking')
        log.token('Hello')
        log.done()

        response = signed_in().get(f'/api/chat/turns/{log.turn_id}/stream')
        body = response.get_data(as_text=True)

        assert 'retry: 1500' in body
        # The `id:` is the cursor, which is what makes Last-Event-ID resume work.
        assert 'id: 0\nevent: status' in body
        assert 'event: token' in body
        assert 'event: done' in body
        # And it closes itself, so the client stops on a frame rather than on a
        # dropped connection.
        assert 'event: end' in body

    def test_a_finished_turn_is_reattachable(self, signed_in, mock_db):
        """A reload seconds after completion shows the reply, not a blank pane."""
        log = self._log()
        log.token('All done.')
        log.done()

        response = signed_in().get(f'/api/chat/turns/{log.turn_id}')

        assert response.status_code == 200
        assert response.get_json()['status'] == 'completed'

    def test_a_running_turn_is_advertised_on_the_session(self, signed_in, mock_db):
        mock_db.get_chat_session.return_value = SESSION
        log = self._log()

        response = signed_in().get('/api/chat/sessions/s1')

        assert response.get_json()['active_turn'] == log.turn_id

    def test_a_finished_turn_is_not_advertised_as_active(self, signed_in, mock_db):
        mock_db.get_chat_session.return_value = SESSION
        log = self._log()
        log.done()

        response = signed_in().get('/api/chat/sessions/s1')

        assert response.get_json()['active_turn'] is None


# ---------------------------------------------------------------------------
# Approval
# ---------------------------------------------------------------------------

class TestApprovalEndpoint:
    """This route is the human in the loop. The agent cannot reach it."""

    OUTLINE = {
        'id': 'o1', 'user_id': 'admin-1', 'session_id': 's1',
        'status': 'pending_approval', 'title': 'Pricing pages',
        'sections': [{'heading': 'A', 'points': []}], 'revision': 1,
    }

    def test_approving_records_it_and_starts_the_write(
            self, signed_in, mock_db, no_turn):
        mock_db.get_outline.return_value = dict(self.OUTLINE)
        mock_db.get_chat_session.return_value = dict(SESSION)
        mock_db.approve_outline.return_value = dict(self.OUTLINE, status='approved')

        response = post(signed_in(), '/api/chat/outlines/o1/approve')

        assert response.status_code == 202
        payload = response.get_json()
        assert payload['started'] is True
        assert payload['turn_id']
        # Approved through the UI, which is recorded -- the only durable answer
        # to "how did this get approved".
        assert mock_db.approve_outline.call_args.kwargs['via'] == 'ui'
        # And the write goes through the ordinary turn path, so there is one
        # code path for one outcome.
        assert 'Approved' in no_turn[0]['kwargs']['message']

    def test_the_turn_sees_the_updated_focus(self, signed_in, mock_db, no_turn):
        """The session was read before the approval, so its copy is stale.

        The turn renders the focus into its state block; handing over the stale
        record would tell the model there is no approved outline.
        """
        mock_db.get_outline.return_value = dict(self.OUTLINE)
        mock_db.get_chat_session.return_value = dict(SESSION, focus_outline_id='')
        mock_db.approve_outline.return_value = dict(self.OUTLINE, status='approved')

        post(signed_in(), '/api/chat/outlines/o1/approve')

        assert no_turn[0]['kwargs']['session']['focus_outline_id'] == 'o1'

    def test_write_can_be_declined(self, signed_in, mock_db, no_turn):
        mock_db.get_outline.return_value = dict(self.OUTLINE)
        mock_db.get_chat_session.return_value = dict(SESSION)
        mock_db.approve_outline.return_value = dict(self.OUTLINE, status='approved')

        response = post(signed_in(), '/api/chat/outlines/o1/approve?write=0')

        assert response.status_code == 200
        assert response.get_json()['started'] is False
        assert not no_turn

    def test_a_superseded_outline_cannot_be_approved(
            self, signed_in, mock_db, no_turn):
        """Otherwise a late "yes" approves the version the user rejected."""
        mock_db.get_outline.return_value = dict(self.OUTLINE, status='superseded')
        mock_db.get_chat_session.return_value = dict(SESSION)
        mock_db.approve_outline.return_value = None

        response = post(signed_in(), '/api/chat/outlines/o1/approve')

        assert response.status_code == 409
        assert 'newer version' in response.get_json()['error']
        assert not no_turn

    def test_another_users_outline_is_not_found(self, signed_in, mock_db, no_turn):
        mock_db.get_outline.return_value = None
        response = post(signed_in(), '/api/chat/outlines/o1/approve')
        assert response.status_code == 404
        assert not no_turn


# ---------------------------------------------------------------------------
# Confirmation
# ---------------------------------------------------------------------------

class TestConfirm:
    """Phase two of a destructive action, reachable only by a user's request."""

    def test_a_valid_token_deletes(self, signed_in, mock_db):
        mock_db.consume_confirmation.return_value = {
            'id': 'tok', 'user_id': 'admin-1', 'action': 'delete_blog',
            'target_id': 'b1', 'payload': {'title': 'Old draft'},
        }
        mock_db.get_blog_by_id.return_value = {
            'id': 'b1', 'title': 'Old draft', 'author_id': 'admin-1',
        }
        mock_db.delete_blog.return_value = True
        mock_db.get_chat_session.return_value = dict(SESSION)

        response = post(signed_in(), '/api/chat/confirm',
                        {'token': 'tok', 'session_id': 's1'})

        assert response.status_code == 200
        assert response.get_json()['deleted'] is True
        mock_db.delete_blog.assert_called_once_with('b1')

    def test_the_outcome_is_recorded_in_the_conversation(self, signed_in, mock_db):
        mock_db.consume_confirmation.return_value = {
            'id': 'tok', 'user_id': 'admin-1', 'action': 'delete_blog',
            'target_id': 'b1', 'payload': {'title': 'Old draft'},
        }
        mock_db.get_blog_by_id.return_value = {
            'id': 'b1', 'title': 'Old draft', 'author_id': 'admin-1',
        }
        mock_db.delete_blog.return_value = True
        mock_db.get_chat_session.return_value = dict(SESSION)

        post(signed_in(), '/api/chat/confirm',
             {'token': 'tok', 'session_id': 's1'})

        # A system message, not a model call: the result is a fact, and spending
        # a turn to have the agent narrate it would be slower and less reliable.
        written = mock_db.append_chat_message.call_args
        assert written.args[2] == 'system'
        assert 'deleted' in written.args[3]

    def test_the_deleted_post_stops_being_the_one_in_focus(self, signed_in, mock_db):
        """Otherwise the next "shorten it" resolves to an id that is gone."""
        mock_db.consume_confirmation.return_value = {
            'id': 'tok', 'user_id': 'admin-1', 'action': 'delete_blog',
            'target_id': 'b1', 'payload': {'title': 'Old draft'},
        }
        mock_db.get_blog_by_id.return_value = {
            'id': 'b1', 'title': 'Old draft', 'author_id': 'admin-1',
        }
        mock_db.delete_blog.return_value = True
        mock_db.get_chat_session.return_value = dict(SESSION, focus_blog_id='b1')

        post(signed_in(), '/api/chat/confirm',
             {'token': 'tok', 'session_id': 's1'})

        cleared = mock_db.update_chat_session.call_args
        assert cleared.kwargs['focus_blog_id'] == ''

    def test_a_spent_token_deletes_nothing(self, signed_in, mock_db):
        mock_db.consume_confirmation.return_value = None

        response = post(signed_in(), '/api/chat/confirm', {'token': 'tok'})

        assert response.status_code == 400
        assert 'expired' in response.get_json()['message']
        mock_db.delete_blog.assert_not_called()

    def test_a_missing_token_is_refused(self, signed_in, mock_db):
        response = post(signed_in(), '/api/chat/confirm', {})
        assert response.status_code == 400
        mock_db.delete_blog.assert_not_called()

    def test_ownership_is_rechecked_at_the_moment_of_deletion(
            self, signed_in, mock_db):
        """A token proves intent, not authority -- and time passed in between."""
        mock_db.consume_confirmation.return_value = {
            'id': 'tok', 'user_id': 'admin-1', 'action': 'delete_blog',
            'target_id': 'b1', 'payload': {'title': 'Not mine'},
        }
        mock_db.get_blog_by_id.return_value = {
            'id': 'b1', 'title': 'Not mine', 'author_id': 'someone-else',
            'site_owner_id': 'another-site',
        }
        mock_db.get_site_owner_for_user.return_value = 'admin-1'

        response = post(signed_in(), '/api/chat/confirm', {'token': 'tok'})

        assert response.status_code == 400
        mock_db.delete_blog.assert_not_called()

    def test_an_already_deleted_post_is_not_an_error(self, signed_in, mock_db):
        mock_db.consume_confirmation.return_value = {
            'id': 'tok', 'user_id': 'admin-1', 'action': 'delete_blog',
            'target_id': 'b1', 'payload': {'title': 'Gone already'},
        }
        mock_db.get_blog_by_id.return_value = None

        response = post(signed_in(), '/api/chat/confirm', {'token': 'tok'})

        assert response.status_code == 200
        assert response.get_json()['already_gone'] is True


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class TestHealth:

    def test_search_and_agent_are_reported(self, client, mock_db):
        response = client.get('/healthz?checks=search,agent')
        assert response.status_code in (200, 503)
        payload = response.get_json()
        assert 'search' in payload['checks']
        assert 'agent' in payload['checks']
        # Unconfigured search is degraded, not failed: the agent works without
        # it and says so, but a deployment that meant to have it can see that.
        assert payload['checks']['search']['status'] in ('ok', 'degraded')
