"""The tool-calling loop: one user message in, one persisted reply out.

The shape is the standard one -- call the model, run whatever tools it asked
for, feed the results back, repeat until it answers in prose -- and almost all
of the code here is about the three ways that goes wrong in production.

**It does not terminate.** A model that dislikes a tool result will call the
tool again, and again. Four guards, each catching a different flavour:
``max_iterations`` bounds the round trips, a wall-clock ``deadline`` bounds the
minutes, per-tool budgets on the context bound the calls of one kind, and
duplicate suppression in the registry bounds identical calls. The first three
are policy and live here; the fourth is mechanical and lives with dispatch.

**It answers with nothing.** A model that calls a tool and then produces no
text leaves the user looking at a spinner that stops. Every exit path from
:meth:`AgentLoop.run` ends with a persisted message, including the paths where
the model said nothing at all -- :meth:`_fallback_text` writes the sentence the
model should have.

**It loses the turn.** The work outlives the request, so nothing that matters
may live only in the HTTP response. Each turn writes its user message before
starting and its reply when finishing, both to Firestore; the event log is
merely the live view. A dropped connection costs the animation, not the post.

Where the streaming happens
---------------------------

Two different things stream, for two different reasons. The agent's prose
streams because it is conversation and latency is felt. The blog draft streams
because it takes minutes and a progress bar would be a lie. Both land in the
same :class:`~app.agent.events.TurnLog` as different event types, so the client
can put the prose in the bubble and the draft in a card without inferring which
is which.
"""
from __future__ import annotations

import time

from app.agent import events
from app.agent.context import ToolContext
from app.agent.events import turns
from app.agent.prompts import history_contents, state_block, system_prompt
from app.agent.registry import declarations, dispatch
from app.core.logging import get_logger
from app.services.gemini_client import GeminiError, gemini

logger = get_logger(__name__)


# How many model round trips one user message may take. Each is a model call
# plus its tools, so this is also the ceiling on cost per turn.
#
# Seven, because the longest legitimate chain is: search → search → outline →
# (approval arrives) → approve → write → reply. Six calls with one spare. A
# turn that wants an eighth is not making progress.
DEFAULT_MAX_ITERATIONS = 7

# Wall-clock ceiling for one turn. Generous, because writing a long post is
# legitimately 60-120 seconds and two writes in a turn is allowed -- but finite,
# because a worker thread is held for the whole time and the pool is small.
DEFAULT_DEADLINE_SECONDS = 420

# The model's own deadline per round trip. Shorter than the turn deadline so a
# single stalled call cannot consume the whole budget.
MODEL_TIMEOUT_SECONDS = 120

# Text shorter than this is not an answer; it is a stray token. Used to decide
# whether a fallback sentence is needed.
MIN_REPLY_CHARS = 2


class TurnResult:
    """What one turn produced, for the caller that has to report it."""

    __slots__ = ('text', 'cards', 'tool_calls', 'iterations', 'status',
                 'error', 'message_id', 'blog_ids', 'focus_blog_id',
                 'focus_blog_title', 'focus_outline_id', 'duration_s')

    def __init__(self, **fields):
        for slot in self.__slots__:
            setattr(self, slot, fields.get(slot))

    def to_dict(self):
        return {slot: getattr(self, slot) for slot in self.__slots__}


class AgentLoop:
    """Runs one conversational turn against the tool set.

    Constructed per turn. The dependencies -- repository, search service, model
    client -- are injected rather than imported at call time so a test can drive
    the whole loop with fakes and no network. ``tests/test_agent_loop.py`` does
    exactly that, which is the only way to test the guards: you cannot reliably
    make a real model loop on demand.
    """

    def __init__(self, db, *, search=None, client=None,
                 max_iterations=DEFAULT_MAX_ITERATIONS,
                 deadline_seconds=DEFAULT_DEADLINE_SECONDS,
                 model=None):
        self.db = db
        self.search = search
        self.client = client or gemini
        self.max_iterations = max(1, int(max_iterations))
        self.deadline_seconds = max(30, int(deadline_seconds))
        self.model = model

    # --- Public API -------------------------------------------------------

    def run(self, *, log, session, user_id, user_name='', user_role='USER',
            message='', history=None):
        """Run one turn. Returns a :class:`TurnResult`; never raises.

        ``log`` is the turn's event log, already registered so a browser can
        attach before this returns -- which it does, since the whole point is
        that the turn outlives the request that started it.
        """
        started = time.time()
        session_id = session.get('id') if session else ''

        # The pending outline is loaded once, up front, and rendered into the
        # state block. The tools re-read it when they act, so this copy is for
        # telling the model what is true, never for deciding what is allowed.
        outline = None
        if session and session.get('focus_outline_id'):
            outline = self.db.get_outline(session['focus_outline_id'], user_id)

        ctx = ToolContext(
            db=self.db,
            user_id=user_id,
            user_name=user_name,
            user_role=user_role,
            session_id=session_id,
            log=log,
            search=self.search,
            focus_blog_id=(session or {}).get('focus_blog_id', ''),
            focus_blog_title=(session or {}).get('focus_blog_title', ''),
            focus_outline_id=(session or {}).get('focus_outline_id', ''),
            last_user_message=message,
        )

        instruction = system_prompt(user_name=user_name) + state_block(
            focus_blog_id=ctx.focus_blog_id,
            focus_blog_title=ctx.focus_blog_title,
            outline=outline,
            search_available=bool(self.search and self.search.is_available),
            blog_count=int((session or {}).get('blog_count') or 0),
        )

        contents = history_contents(history)
        contents.append({'role': 'user', 'parts': [{'text': message}]})

        reply_parts = []
        iterations = 0
        status = 'completed'
        error = None
        stop_note = None

        try:
            while iterations < self.max_iterations:
                iterations += 1

                elapsed = time.time() - started
                if elapsed > self.deadline_seconds:
                    stop_note = (
                        'This turn ran out of time before finishing everything.'
                    )
                    logger.warning(
                        'Agent turn hit its deadline',
                        extra={'session_id': session_id, 'iterations': iterations,
                               'elapsed_s': round(elapsed, 1)},
                    )
                    break

                log.status_('thinking', 'Thinking')

                text, calls = self._one_round(ctx, contents, instruction)

                if text:
                    reply_parts.append(text)

                if not calls:
                    # Prose and no tool calls: the model has answered.
                    break

                # The model's own turn, recorded verbatim so the next round trip
                # sees the calls it made. Both the text and the calls, in that
                # order, because dropping the text would make the model repeat it.
                model_parts = []
                if text:
                    model_parts.append({'text': text})
                for call in calls:
                    model_parts.append({'function_call': {
                        'name': call['name'], 'args': call['args'],
                    }})
                contents.append({'role': 'model', 'parts': model_parts})

                # Every result goes back in one content, in call order. The API
                # requires a response for each call it asked for -- a missing one
                # makes the next call fail with a mismatched-parts error rather
                # than with anything diagnosable.
                response_parts = []
                for call in calls:
                    result = dispatch(ctx, call['name'], call['args'])
                    response_parts.append({'function_response': {
                        'name': call['name'],
                        'response': _serialisable(result),
                    }})
                contents.append({'role': 'user', 'parts': response_parts})

            else:
                # The while loop exhausted its iterations without breaking.
                stop_note = (
                    'This turn reached its step limit, so it may be unfinished.'
                )
                logger.warning(
                    'Agent turn hit the iteration ceiling',
                    extra={'session_id': session_id,
                           'max_iterations': self.max_iterations,
                           'tool_usage': ctx.usage()},
                )

        except GeminiError as exc:
            status = 'failed'
            error = getattr(exc, 'message', None) or str(exc)
            logger.warning('Agent turn failed on the model: %s', exc,
                           extra={'session_id': session_id})
        except Exception as exc:
            status = 'failed'
            error = 'Something went wrong on our side. Nothing was lost.'
            logger.exception('Agent turn crashed',
                             extra={'session_id': session_id, 'detail': str(exc)})

        reply = '\n\n'.join(part.strip() for part in reply_parts if part.strip())

        if status == 'failed':
            # A failure still gets a reply in the thread. An error banner that
            # disappears on reload leaves a conversation whose last turn is the
            # user talking to nobody.
            reply = self._failure_text(error, ctx)
        elif len(reply.strip()) < MIN_REPLY_CHARS:
            reply = self._fallback_text(ctx)
        elif stop_note:
            reply = f'{reply}\n\n{stop_note}'

        result = self._persist(
            ctx, session_id, user_id, reply,
            status=status, error=error, iterations=iterations,
            turn_id=log.turn_id,
        )
        result.duration_s = round(time.time() - started, 1)

        logger.info(
            'Agent turn %s', status,
            extra={'session_id': session_id, 'user_id': user_id,
                   'iterations': iterations, 'tool_calls': len(ctx.tool_calls),
                   'tool_usage': ctx.usage(),
                   'blogs_created': len(ctx.created_blog_ids),
                   'duration_ms': round((time.time() - started) * 1000, 1)},
        )

        return result

    # --- One model round trip --------------------------------------------

    def _one_round(self, ctx, contents, instruction):
        """One model call. Returns ``(text, calls)``.

        Text is streamed into the log as it arrives, so prose the model produces
        before deciding to call a tool is on screen before the tool runs -- which
        is what makes a multi-step turn feel like something happening rather than
        like a stall.
        """
        chunks = []
        calls = []

        for kind, payload in self.client.stream_with_tools(
            contents,
            declarations(),
            model=self.model,
            system_instruction=instruction,
            timeout=MODEL_TIMEOUT_SECONDS,
            label='agent_turn',
        ):
            if kind == 'text':
                chunks.append(payload)
                ctx.log.token(payload)
            else:
                calls.append(payload)

        return ''.join(chunks), calls

    # --- Endings ----------------------------------------------------------

    def _fallback_text(self, ctx):
        """A reply for a turn where the model produced no prose.

        It happens: a model that calls a tool as its last act sometimes stops
        without commenting. Rather than showing an empty bubble, the turn is
        described from what actually occurred -- which the context knows exactly,
        having recorded every call.
        """
        if ctx.created_blog_ids:
            return (
                f'Done — the post is written and saved to your drafts as '
                f'"{ctx.focus_blog_title or "a new draft"}".'
            )

        last_outline = next(
            (card for card in reversed(ctx.cards) if card['kind'] == 'outline'),
            None,
        )
        if last_outline:
            return (
                'Here is the outline. Does this work, or should I adjust '
                'anything before writing it?'
            )

        if any(card['kind'] == 'confirm_delete' for card in ctx.cards):
            return 'Confirm above and I will delete it. This cannot be undone.'

        if ctx.tool_calls:
            names = ', '.join(
                sorted({call['name'] for call in ctx.tool_calls})
            ).replace('_', ' ')
            return f'I ran {names} but did not have anything to add. What next?'

        return "I did not manage to produce an answer there. Could you rephrase?"

    def _failure_text(self, error, ctx):
        """A reply for a failed turn, mentioning anything that did land.

        Work completed before the failure is real work: a post that was written
        and saved before the model fell over is in the user's drafts whatever the
        banner says, and not mentioning it is how someone ends up with a
        duplicate.
        """
        message = error or 'Something went wrong.'
        if ctx.created_blog_ids:
            return (
                f'{message}\n\nThe post itself was written and saved to your '
                'drafts before that happened, so it is not lost.'
            )
        return message

    # --- Persistence ------------------------------------------------------

    def _persist(self, ctx, session_id, user_id, reply, *, status, error,
                 iterations, turn_id):
        """Write the reply, update the session, close the log.

        Ordered so the durable write happens before the log is closed. The
        browser stops attaching once it sees a terminal event, and if it went
        looking for the persisted message before the write landed it would find
        the conversation one message short.
        """
        written = self.db.append_chat_message(
            session_id, user_id, 'agent', reply,
            tool_calls=ctx.tool_calls,
            cards=ctx.cards,
            status=status,
            error=error,
            turn_id=turn_id,
            focus_blog_id=ctx.focus_blog_id,
            focus_blog_title=ctx.focus_blog_title,
            focus_outline_id=ctx.focus_outline_id,
            blogs_created=len(ctx.created_blog_ids),
        )

        message_id = (written or {}).get('id', '')
        if written:
            ctx.log.emit(events.MESSAGE, message_id=message_id,
                         seq=written.get('seq'))
        else:
            # The reply exists on screen but not in the conversation. Logged
            # loudly: it is the one failure here that a user notices later, as a
            # turn that vanishes on reload.
            logger.error('Could not persist the agent reply',
                         extra={'session_id': session_id, 'turn_id': turn_id})

        result = TurnResult(
            text=reply,
            cards=list(ctx.cards),
            tool_calls=list(ctx.tool_calls),
            iterations=iterations,
            status=status,
            error=error,
            message_id=message_id,
            blog_ids=list(ctx.created_blog_ids),
            focus_blog_id=ctx.focus_blog_id,
            focus_blog_title=ctx.focus_blog_title,
            focus_outline_id=ctx.focus_outline_id,
        )

        if status == 'failed':
            ctx.log.fail(error or 'The turn failed.')
        else:
            ctx.log.done(
                message_id=message_id,
                blog_ids=list(ctx.created_blog_ids),
                focus_blog_id=ctx.focus_blog_id,
            )

        return result


def run_turn(db, *, session, user_id, user_name='', user_role='USER',
             message='', history=None, search=None, log=None,
             max_iterations=DEFAULT_MAX_ITERATIONS,
             deadline_seconds=DEFAULT_DEADLINE_SECONDS):
    """Convenience entry point: open a log if needed, run the turn.

    What the background task calls. The log is created by the *route* in the
    normal path, before the task is submitted, so the response can hand the
    browser a turn id to attach to immediately -- if a caller has not done that,
    one is opened here so the function is usable on its own.
    """
    if log is None:
        log = turns.open(session.get('id') if session else '', user_id)

    loop = AgentLoop(db, search=search, max_iterations=max_iterations,
                     deadline_seconds=deadline_seconds)
    return loop.run(
        log=log, session=session, user_id=user_id, user_name=user_name,
        user_role=user_role, message=message, history=history,
    )


def _serialisable(result):
    """A tool result the SDK can put in a ``function_response``.

    The proto conversion accepts nested dicts, lists and scalars and nothing
    else, so anything exotic is stringified rather than allowed to raise from
    inside the marshalling layer -- where the traceback names a proto field and
    not the tool that produced the value.
    """
    if isinstance(result, dict):
        return {str(k): _serialisable(v) for k, v in result.items()}
    if isinstance(result, (list, tuple)):
        return [_serialisable(v) for v in result]
    if isinstance(result, (str, int, float, bool)) or result is None:
        return result
    return str(result)
