"""What a tool is allowed to know, and what it is allowed to spend.

Every tool in :mod:`app.agent.tools` takes a :class:`ToolContext` as its first
argument and nothing else that is not a model-supplied parameter. That is the
whole point of the type: a tool never reads ``flask.session``, never calls
``current_user()``, and never touches ``request``. It gets a context.

Three things follow from that, all of which the requirements ask for:

* **The tools are unit testable.** A test constructs a context with a fake
  repository and a throwaway log, calls the tool, and asserts on the returned
  dict. No app context, no request, no model. See ``tests/test_agent_tools.py``.
* **Authority is explicit.** ``user_id`` on the context is the only identity a
  tool has. A model that hallucinates ``user_id`` as an argument cannot pass it
  -- there is no such parameter on any tool -- so cross-user access is not
  something the prompt has to be trusted about.
* **Spending is bounded per turn, not per call.** A runaway agent is not one
  bad call; it is thirty reasonable-looking ones. The budget lives here because
  the context is the only object that spans a whole turn.

Focus, and why it is on the context
-----------------------------------

"Make the intro punchier" has to resolve to a blog id. The context carries the
current focus, tools update it whenever they touch something specific, and the
loop writes it back to the session at the end of the turn. So resolution is a
lookup, not an inference -- which is what stops "delete that last one" from
meaning something different depending on how the model happened to read the
transcript that turn.
"""
from __future__ import annotations

import threading

from app.core.logging import get_logger

logger = get_logger(__name__)


class ToolBudgetError(Exception):
    """A per-turn spending limit was reached.

    Raised by :meth:`ToolContext.spend` and caught by the registry, which turns
    it into a tool *result* the model can read rather than an exception that
    ends the turn. The distinction matters: the correct behaviour when the
    research budget runs out is for the agent to say "I've searched enough,
    here's the outline", not for the conversation to break.
    """

    def __init__(self, tool, limit, message=None):
        self.tool = tool
        self.limit = limit
        super().__init__(message or (
            f'The limit of {limit} {tool} call(s) for one turn has been reached.'
        ))


#: Per-turn ceilings, by tool name. A tool absent from this map is limited only
#: by the loop's overall iteration cap.
#:
#: The numbers are chosen from what a legitimate turn needs, not from what feels
#: safe. Three searches covers "research this from a few angles"; a fourth is
#: almost always a model that did not like its own results. Two blog writes
#: covers "write it, and also write the variation I asked for" -- a third in one
#: turn has never been a real request and is minutes of model time.
DEFAULT_BUDGETS = {
    'search_web': 3,
    'create_outline': 3,
    'create_blog': 2,
    'edit_blog': 4,
    'delete_blog': 3,
    'list_blogs': 4,
    'get_blog': 6,
}


class ToolContext:
    """Everything one turn's tools may read, plus that turn's budget.

    Instances are per-turn and single-threaded in practice (one worker runs the
    loop), but the counters are lock-guarded anyway: the SSE and polling
    endpoints read :meth:`usage` from request threads while the worker writes,
    and a dict mutated during iteration raises.
    """

    def __init__(self, *, db, user_id, user_name='', user_role='USER',
                 session_id='', log=None, budgets=None,
                 focus_blog_id='', focus_blog_title='', focus_outline_id='',
                 last_user_message='', search=None):
        self.db = db
        self.user_id = user_id
        self.user_name = user_name or 'there'
        self.user_role = user_role or 'USER'
        self.session_id = session_id
        self.log = log

        # The web search service, injected rather than imported so a test can
        # pass a stub without monkeypatching a module singleton.
        self.search = search

        # What "that one" means right now. Seeded from the session, updated by
        # tools, written back by the loop.
        self.focus_blog_id = focus_blog_id or ''
        self.focus_blog_title = focus_blog_title or ''
        self.focus_outline_id = focus_outline_id or ''

        # The user's message this turn, verbatim. Used by the approval path to
        # verify a claimed "yes" against what the user actually typed -- see
        # app/agent/tools/outlines.py. Never shown; never sent anywhere.
        self.last_user_message = last_user_message or ''

        self._budgets = dict(DEFAULT_BUDGETS)
        if budgets:
            self._budgets.update(budgets)
        self._spent = {}
        # Identical calls seen this turn, keyed by (name, args). The other half
        # of the runaway guard: a budget stops thirty *different* calls, and this
        # stops the same call thirty times -- which is the more common failure,
        # because a model that dislikes a tool result will re-issue the exact
        # call rather than change its approach.
        self._seen_calls = {}
        self._lock = threading.RLock()

        # Accumulated across the turn, read by the loop when it finishes: the
        # audit trail, the cards to persist with the reply, and the ids the
        # session's focus and counters need.
        self.tool_calls = []
        self.cards = []
        self.created_blog_ids = []
        self.deleted_blog_ids = []

    # --- Budget -----------------------------------------------------------

    def spend(self, tool):
        """Charge one call of ``tool`` against this turn. Raises when exhausted."""
        with self._lock:
            limit = self._budgets.get(tool)
            used = self._spent.get(tool, 0)
            if limit is not None and used >= limit:
                raise ToolBudgetError(tool, limit)
            self._spent[tool] = used + 1
            return used + 1

    def usage(self):
        with self._lock:
            return dict(self._spent)

    def note_call(self, name, args):
        """Count this exact call. Returns how many times it has now been seen.

        A return of 1 is a first call. Anything higher is a repeat, and the
        registry answers a repeat with a result telling the model to stop rather
        than by running the tool again -- re-running is at best wasted work and
        at worst a second write.
        """
        import json

        try:
            key = (name, json.dumps(args or {}, sort_keys=True, default=str))
        except (TypeError, ValueError):
            key = (name, repr(args))
        with self._lock:
            count = self._seen_calls.get(key, 0) + 1
            self._seen_calls[key] = count
            return count

    # --- Focus ------------------------------------------------------------

    def focus_blog(self, blog_id, title=''):
        """Point "that one" at a blog.

        Called by every tool that reads or writes a specific post, including
        ``get_blog`` -- looking something up is exactly how a user signals what
        they are about to talk about.
        """
        if blog_id:
            self.focus_blog_id = str(blog_id)
            if title:
                self.focus_blog_title = str(title)[:300]

    def focus_outline(self, outline_id):
        if outline_id:
            self.focus_outline_id = str(outline_id)

    def resolve_blog_id(self, blog_id=None):
        """The blog a call refers to: the one named, else the one in focus.

        Returning ``None`` rather than raising, so each tool can phrase its own
        "which post do you mean?" -- the sentence that gets the conversation
        unstuck differs between editing and deleting, and the model needs the
        specific one.
        """
        candidate = (blog_id or '').strip() if isinstance(blog_id, str) else blog_id
        return str(candidate) if candidate else (self.focus_blog_id or None)

    # --- Recording --------------------------------------------------------

    def record_call(self, name, args, ok, summary='', duration_ms=0):
        """Add one call to the turn's audit trail.

        Kept on the context and written once with the reply, rather than
        appended to Firestore per call: a turn is one unit of "what happened",
        and eight extra writes per turn is eight extra round trips on the
        critical path of a conversation.
        """
        with self._lock:
            self.tool_calls.append({
                'name': name,
                'args': args,
                'ok': bool(ok),
                'summary': summary,
                'duration_ms': duration_ms,
            })

    def add_card(self, kind, data):
        """Attach a structured card to the reply, and show it live.

        Both, from one call, because a card that appears during the turn and
        then vanishes on reload is worse than one that never appeared: the
        reader learns not to trust the pane.
        """
        card = {'kind': kind, 'data': data or {}}
        with self._lock:
            self.cards.append(card)
        if self.log is not None:
            self.log.card(kind, data or {})
        return card

    def emit(self, event_type, **data):
        """Write to the turn log, if there is one.

        Tools call this for progress that is not a card -- a status line, a
        thought. Guarded because a unit test builds a context with no log, and
        a tool must not need one to be callable.
        """
        if self.log is not None:
            return self.log.emit(event_type, **data)
        return -1
