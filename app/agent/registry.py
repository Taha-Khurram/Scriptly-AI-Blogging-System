"""The tool registry: declarations the model sees, and the dispatch it gets.

One table, :data:`TOOLS`, is the single source of truth for what the agent can
do. It produces the JSON-schema declarations sent to Gemini *and* the dispatch
map, so a tool cannot be offered to the model without being callable, or be
callable without being described -- the two failure modes of hand-maintained
tool lists.

What dispatch adds on top of calling the function
-------------------------------------------------

Every guard the requirements ask for lives here rather than in each tool, so a
tool added tomorrow inherits all of them:

* **Budget.** One charge per call against the turn's per-tool ceiling.
* **Repeat suppression.** The same call twice in a turn is answered, not run.
* **Total isolation of failures.** Any exception a tool raises becomes a tool
  *result*. A turn must not die because one tool did; the agent should be able
  to say what went wrong and offer something else.
* **The audit trail.** Name, arguments, outcome and duration, structured-logged
  and recorded on the context for persistence with the reply.

Arguments are logged, and that is a deliberate privacy call: they are the user's
own topics and instructions, they go to the model anyway, and a tool log without
arguments cannot answer the only question anyone ever asks it -- "what did it
actually do?". Nothing else about the user is in them, because no tool takes an
identity argument.
"""
from __future__ import annotations

import time

from app.agent.context import ToolBudgetError
from app.agent.tools import blogs as blog_tools
from app.agent.tools import outlines as outline_tools
from app.agent.tools import research as research_tools
from app.core.logging import get_logger

logger = get_logger(__name__)


class ToolSpec:
    """One tool: how it is described, how it is called, how it is labelled."""

    __slots__ = ('name', 'fn', 'description', 'parameters', 'label', 'destructive')

    def __init__(self, name, fn, description, parameters, label,
                 destructive=False):
        self.name = name
        self.fn = fn
        self.description = description
        self.parameters = parameters
        #: ``fn(args) -> str`` for the line shown in the UI while it runs. The
        #: user should be able to read what the agent is doing without knowing
        #: that tools exist.
        self.label = label
        self.destructive = destructive

    def declaration(self):
        return {
            'name': self.name,
            'description': self.description,
            'parameters': self.parameters,
        }


def _string(description):
    return {'type': 'string', 'description': description}


# The tool table. Descriptions are written *at the model* -- they are the only
# documentation it gets, and where the approval rules are restated so they are
# in front of it at the moment of choosing, not only in a system prompt several
# thousand tokens up.
TOOLS = (
    ToolSpec(
        'search_web',
        research_tools.search_web,
        'Search the live web for current facts, statistics, trends or examples '
        'on a topic. Use this BEFORE create_outline whenever the topic is '
        'time-sensitive, involves current tools/prices/versions, or the user '
        'asked you to research it. The results are the only sources you may '
        'cite. Limited to a few calls per turn.',
        {
            'type': 'object',
            'properties': {
                'query': _string('What to search for. A focused search-engine '
                                 'query, not a sentence.'),
                'max_results': {
                    'type': 'integer',
                    'description': 'How many results to return (1-8, default 5).',
                },
            },
            'required': ['query'],
        },
        lambda args: f'Searching for "{_short(args.get("query"), 60)}"',
    ),

    ToolSpec(
        'create_outline',
        outline_tools.create_outline,
        'Draft a structured outline (title, angle, audience, sections with key '
        'points, sources) and show it to the user for approval. THIS IS ALWAYS '
        'THE FIRST STEP toward a new post — never write a post without one. '
        'After calling this, STOP and ask the user to approve or change it.',
        {
            'type': 'object',
            'properties': {
                'topic': _string('The topic or brief for the post, in the '
                                 "user's own terms plus anything they specified."),
                'research_notes': _string('Findings from search_web to plan '
                                          'against and cite. Omit if no search '
                                          'was done.'),
                'tone': _string('professional | conversational | technical | '
                                'casual | persuasive. Default professional.'),
                'length': _string('short (600-800 words) | medium (900-1100) | '
                                  'long (1400-1800). Default medium.'),
                'keywords': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': 'SEO keywords to work in naturally.',
                },
            },
            'required': ['topic'],
        },
        lambda args: f'Planning "{_short(args.get("topic"), 50)}"',
    ),

    ToolSpec(
        'revise_outline',
        outline_tools.revise_outline,
        'Rework the pending outline according to the changes the user asked '
        'for. Use this whenever they respond to an outline with anything other '
        'than clear approval. The revised outline again needs approval.',
        {
            'type': 'object',
            'properties': {
                'feedback': _string("What to change, in the user's own terms."),
                'outline_id': _string('The outline to revise. Omit to use the '
                                      'one in this conversation.'),
            },
            'required': ['feedback'],
        },
        lambda args: 'Reworking the outline',
    ),

    ToolSpec(
        'submit_outline_approval',
        outline_tools.submit_outline_approval,
        "Ask the server to check whether the user's most recent message "
        'approved the pending outline. Call this when they reply to an outline '
        'with something that reads like a yes. You cannot approve on their '
        "behalf: the server re-reads their actual words. If it answers "
        'approved: false, do NOT write the post.',
        {
            'type': 'object',
            'properties': {
                'outline_id': _string('The outline in question. Omit to use the '
                                      'one in this conversation.'),
            },
            'required': [],
        },
        lambda args: 'Checking the approval',
    ),

    ToolSpec(
        'create_blog',
        blog_tools.create_blog,
        'Write the full blog post from an APPROVED outline and save it as a '
        'draft. This refuses outright unless the outline has been approved by '
        'the user — it is not a suggestion, the tool checks stored approval '
        'state. Takes up to two minutes.',
        {
            'type': 'object',
            'properties': {
                'outline_id': _string('The approved outline to write from. Omit '
                                      'to use the one in this conversation.'),
                'tone': _string('Override the tone agreed in the outline.'),
                'length': _string('Override the length agreed in the outline.'),
                'keywords': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': 'Override the outline\'s keywords.',
                },
            },
            'required': [],
        },
        lambda args: 'Writing the post',
    ),

    ToolSpec(
        'edit_blog',
        blog_tools.edit_blog,
        'Apply one natural-language change to an existing post: rewrite a '
        'section, change the tone, add or cut a paragraph, fix the intro, '
        'tighten it up. Returns the whole post with only that change made. Omit '
        'blog_id to edit the post currently being discussed.',
        {
            'type': 'object',
            'properties': {
                'instructions': _string('The change to make, specifically. '
                                        'One change per call.'),
                'blog_id': _string('The post to edit. Omit for the one in focus.'),
                'apply_title': {
                    'type': 'boolean',
                    'description': 'Apply a suggested new title too. Only pass '
                                   'true if the user asked for a retitle — it '
                                   'changes the post URL.',
                },
            },
            'required': ['instructions'],
        },
        lambda args: f'Editing: {_short(args.get("instructions"), 60)}',
    ),

    ToolSpec(
        'delete_blog',
        blog_tools.delete_blog,
        'Ask the user to confirm deleting a post. THIS DELETES NOTHING — it '
        'shows them a confirmation they must click. Say what will be removed '
        'and that it is permanent, then stop. Omit blog_id for the post in '
        'focus.',
        {
            'type': 'object',
            'properties': {
                'blog_id': _string('The post to delete. Omit for the one in focus.'),
            },
            'required': [],
        },
        lambda args: 'Preparing a delete confirmation',
        destructive=True,
    ),

    ToolSpec(
        'list_blogs',
        blog_tools.list_blogs,
        "List the user's posts. Use this to find a post they referred to by "
        'title or topic, or to answer "what have I got in drafts". The result '
        'is shown to them as a list, so do not repeat it in prose.',
        {
            'type': 'object',
            'properties': {
                'status': _string('DRAFT | UNDER_REVIEW | PUBLISHED | '
                                  'SCHEDULED. Omit for all.'),
                'search': _string('Free text matched against title, category '
                                  'and author.'),
                'category': _string('Restrict to one category by name.'),
                'limit': {
                    'type': 'integer',
                    'description': 'How many to return (1-25, default 10).',
                },
            },
            'required': [],
        },
        lambda args: 'Looking through the posts',
    ),

    ToolSpec(
        'get_blog',
        blog_tools.get_blog,
        'Read one post in full — before editing it, or to answer a question '
        'about what it says. Puts that post in focus so later edit/delete calls '
        'need no id.',
        {
            'type': 'object',
            'properties': {
                'blog_id': _string('The post to read. Omit for the one in focus.'),
            },
            'required': [],
        },
        lambda args: 'Reading the post',
    ),
)

BY_NAME = {spec.name: spec for spec in TOOLS}

#: Names that must never run without a separate user-initiated confirmation.
DESTRUCTIVE = frozenset(spec.name for spec in TOOLS if spec.destructive)


def declarations():
    """The ``tools=`` payload for a Gemini call.

    Plain dicts rather than ``genai.protos`` types: the SDK converts them and
    the dict form is what makes this table readable and diffable. Verified
    against ``google-generativeai`` 0.8.6's ``to_function_library``.
    """
    return [{'function_declarations': [spec.declaration() for spec in TOOLS]}]


def label_for(name, args):
    """The human-readable line shown while a tool runs.

    Falls back to the tool name if a label function raises -- a cosmetic string
    must not be able to break a turn.
    """
    spec = BY_NAME.get(name)
    if spec is None:
        return name
    try:
        return spec.label(args or {})
    except Exception:
        return spec.name


def dispatch(ctx, name, args):
    """Run one tool call under every guard. Always returns a result dict.

    Never raises. Whatever happens -- unknown tool, exhausted budget, a
    repository that threw, a bug in a tool -- the model gets a dict explaining
    it and the turn continues. A conversational agent that dies on a tool error
    has turned a recoverable problem into a lost turn.
    """
    args = dict(args or {})
    spec = BY_NAME.get(name)

    if spec is None:
        # Models do invent tool names, usually plausible ones ('publish_blog').
        # Naming what does exist turns a dead end into a correction.
        logger.warning('Agent called unknown tool %r', name)
        ctx.record_call(name, args, ok=False, summary='unknown tool')
        return {
            'ok': False,
            'error': 'unknown_tool',
            'message': (
                f'There is no tool called {name}. Available tools: '
                + ', '.join(sorted(BY_NAME)) + '.'
            ),
        }

    repeats = ctx.note_call(name, args)
    if repeats > 1:
        logger.warning('Agent repeated an identical %s call (%s times)',
                       name, repeats)
        ctx.record_call(name, args, ok=False, summary='duplicate call suppressed')
        return {
            'ok': False,
            'error': 'duplicate_call',
            'message': (
                f'You already made this exact {name} call in this turn and the '
                'result has not changed. Do not repeat it. Either change the '
                'arguments, use a different tool, or answer the user with what '
                'you already have.'
            ),
        }

    try:
        ctx.spend(name)
    except ToolBudgetError as exc:
        ctx.record_call(name, args, ok=False, summary='budget exhausted')
        return {
            'ok': False,
            'error': 'budget_exhausted',
            'message': (
                f'{exc} Work with what you have and reply to the user; do not '
                'call this tool again this turn.'
            ),
        }

    label = label_for(name, args)
    ctx.emit('tool_start', name=name, label=label, args=_public_args(args))

    started = time.perf_counter()
    try:
        result = spec.fn(ctx, **args)
        if not isinstance(result, dict):
            # A tool returning something else is a bug in the tool, but it must
            # not be a bug in the turn.
            logger.error('Tool %s returned %s, not a dict', name, type(result))
            result = {'ok': True, 'result': str(result)[:500]}
    except TypeError as exc:
        # A required argument the model did not supply. Every tool accepts
        # **_ignored, so an *extra* argument cannot land here -- only a missing
        # required one, which is worth telling the model precisely.
        logger.warning('Tool %s called with bad arguments: %s', name, exc)
        result = {
            'ok': False,
            'error': 'bad_arguments',
            'message': f'That call was missing a required argument: {exc}',
        }
    except Exception as exc:
        logger.exception('Tool %s raised', name, extra={'tool': name})
        result = {
            'ok': False,
            'error': 'tool_failed',
            'message': (
                f'{name} failed unexpectedly ({type(exc).__name__}). Tell the '
                'user something went wrong with that step and offer an '
                'alternative. Do not retry it.'
            ),
        }

    duration_ms = round((time.perf_counter() - started) * 1000, 1)
    ok = bool(result.get('ok'))
    summary = _summarise(result)

    # The audit line the requirements ask for: name, args, result, timing --
    # structured, so it is queryable in the JSON logs rather than only readable.
    logger.info(
        'Agent tool %s %s', name, 'ok' if ok else 'failed',
        extra={
            'tool': name,
            'tool_args': _public_args(args),
            'tool_ok': ok,
            'tool_error': result.get('error'),
            'tool_summary': summary,
            'duration_ms': duration_ms,
            'user_id': ctx.user_id,
            'session_id': ctx.session_id,
        },
    )

    ctx.record_call(name, args, ok=ok, summary=summary, duration_ms=duration_ms)
    ctx.emit('tool_end', name=name, ok=ok, summary=summary,
             duration_ms=duration_ms)

    return result


def _summarise(result):
    """A short human line for a tool result, for the log and the UI."""
    if not result.get('ok'):
        return str(result.get('error') or 'failed')[:80]
    for key in ('title', 'query', 'count', 'blog_id', 'outline_id'):
        if result.get(key):
            return f'{key}={str(result[key])[:60]}'
    return 'ok'


def _public_args(args):
    """Arguments, clipped for the log and the UI.

    Long values are truncated rather than dropped: a search query or an edit
    instruction is the most useful part of a tool log, and it is the user's own
    words -- which they typed into this same screen a second earlier.
    """
    out = {}
    for key, value in (args or {}).items():
        if isinstance(value, str):
            out[key] = value[:200]
        elif isinstance(value, (list, tuple)):
            out[key] = [str(v)[:60] for v in list(value)[:8]]
        elif isinstance(value, (int, float, bool)) or value is None:
            out[key] = value
        else:
            out[key] = str(value)[:120]
    return out


def _short(value, limit):
    text = str(value or '').strip()
    return text[:limit] + ('…' if len(text) > limit else '')
