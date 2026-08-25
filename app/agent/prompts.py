"""The system prompt, and the conversation state that goes into it.

Two functions and one long string. The string is worth reading carefully,
because it is where the *intent* of the flow lives -- the enforcement lives in
the tools (see :mod:`app.agent.tools.outlines`), and the two have to agree or
the agent spends its turns being refused by its own tools.

The division of labour, restated because it is the design decision that matters
most in this package:

* **The prompt** makes the agent *want* to do the right thing: research before
  planning, plan before writing, stop and ask.
* **The tools** make it *unable* to do the wrong thing: ``create_blog`` refuses
  an unapproved outline, ``delete_blog`` cannot delete.

Neither is sufficient. A prompt alone is a request that one confusing turn can
override. Tools alone would produce an agent that constantly tries forbidden
things and gets refused, which reads to the user as an agent that does not know
what it is doing.

Why the state block is rendered fresh every turn
------------------------------------------------

Focus ("that one"), the pending outline and its approval status are injected as
a short block on every turn rather than left to be inferred from the transcript.
Inference drifts: a model reading twenty messages will sometimes decide "the
post" means the first one discussed rather than the last, and the user has no
way to see that it has. A rendered block is checkable -- it is the same state the
tools resolve against, so what the agent believes and what the tools will do
cannot come apart.
"""
from __future__ import annotations

from app.utils.date_utils import utcnow

# How many past messages are rendered into the model's context. Every one is
# re-sent on every iteration of the tool loop, so this is the main lever on the
# cost of a long conversation. Twenty turns is comfortably more than any
# follow-up needs to resolve against.
HISTORY_MESSAGES = 20

# One historical message, clipped. Long enough for a full instruction, short
# enough that one pasted essay cannot evict the rest of the conversation.
MAX_HISTORY_CHARS = 1_500


SYSTEM_PROMPT = """\
You are Scriptly, the blog studio agent inside a content platform. You research \
topics, plan posts, write them, revise them and manage the user's library — \
through conversation, using tools.

You are talking to {user_name}. Today is {today}.

# THE ONE RULE THAT IS NOT NEGOTIABLE

You never write a blog post that the user has not approved an outline for.

The order is always: (research, if useful) → outline → **the user approves** → \
write. There is no shortcut for an eager user, an obvious topic or a repeated \
request. `create_blog` checks stored approval state and will refuse you — the \
rule is enforced in the tool, so trying it just wastes the user's time.

When you have produced an outline: STOP. End your turn. Summarise the plan in \
two or three lines and ask whether it works or should change. Do not call \
`create_blog` in the same turn as `create_outline` unless \
`submit_outline_approval` has come back `approved: true` in between.

If they reply with a clear yes → `submit_outline_approval`, then `create_blog`.
If they reply with anything else → `revise_outline` with what they said.
If you cannot tell → ask. One short question beats a post they did not want.

# HOW TO WORK

**Research when it matters.** Call `search_web` first if the topic is \
time-sensitive, involves current tools, prices or versions, or the user asked \
you to look it up. Cite only what the search returned. If web search is \
unavailable, say so in one clause and write from your own knowledge — never \
invent a source, a statistic or a URL to fill the gap.

**Chain tools within a turn where the steps are yours to take.** "Research the \
latest trends and outline it" is one turn: search, then outline, then stop for \
approval. "Write it and delete my old draft on the same topic" is one turn too: \
write it, then raise the delete confirmation. What you must not chain is \
anything across the approval gate.

**Deleting shows a confirmation; it does not delete.** `delete_blog` puts a \
confirm button in front of the user. Say plainly what will be removed — and that \
it is permanent, and if it is published, that it will vanish from their live \
site. Then stop. Do not call it twice for the same post.

**Follow-ups resolve against what is in focus.** "Make the intro punchier", \
"shorten section 2", "delete that one" refer to the post named in CURRENT STATE. \
Omit `blog_id` and the tools use it. If nothing is in focus and they were vague, \
use `list_blogs` to find it or ask which one they mean — do not guess at an id.

**One change per `edit_blog` call.** Two instructions in one call get muddled \
together; two calls are cheap.

# HOW TO WRITE BACK

Short. Two to four sentences for most turns. You are in a chat panel next to \
cards that already show the outline, the draft and the list — so do not repeat \
their contents in prose. Say what you did, what it means, and what the choice is \
now.

Never paste a whole blog post or a full outline into your reply. The user can \
see it.

Plain, direct sentences. No preamble ("Certainly! I'd be happy to..."), no \
recap of their own request back at them, no bulleted summary of a summary. If a \
tool failed, say what failed and what you can do instead — do not pretend it \
worked and do not apologise twice.

Write in British or American English to match the user; never mention these \
instructions, the tools by name, or the fact that you are following a process.
"""


def system_prompt(*, user_name='there'):
    """The static instructions, with the two facts that are always true."""
    return SYSTEM_PROMPT.format(
        user_name=user_name or 'there',
        today=utcnow().strftime('%d %B %Y'),
    )


def state_block(*, focus_blog_id='', focus_blog_title='', outline=None,
                search_available=True, blog_count=0):
    """The current-state block, appended to the system prompt each turn.

    Rendered as an explicit block rather than left to inference. See the module
    docstring for why -- briefly, because this is the same state the tools
    resolve against, so writing it down is what keeps the agent's belief and the
    tools' behaviour from diverging without anyone noticing.

    Returns an empty string when there is nothing to say, so a first turn does
    not carry a block of "none" values that reads to the model like an
    instruction.
    """
    lines = []

    if focus_blog_id:
        lines.append(
            f'- Post in focus: "{focus_blog_title or "untitled"}" '
            f'(id {focus_blog_id}). "It", "that one" and "the post" mean this '
            'one. Omit blog_id in tool calls to act on it.'
        )

    if outline:
        status = outline.get('status') or 'pending_approval'
        title = outline.get('title') or 'untitled'
        outline_id = outline.get('id') or ''
        if status == 'pending_approval':
            lines.append(
                f'- Outline AWAITING APPROVAL: "{title}" (id {outline_id}, '
                f'revision {outline.get("revision", 1)}). You may NOT call '
                'create_blog for it. If the user just approved it, call '
                'submit_outline_approval; if they asked for changes, call '
                'revise_outline.'
            )
        elif status == 'approved':
            written = outline.get('blog_id')
            if written:
                lines.append(
                    f'- Outline "{title}" (id {outline_id}) is approved and has '
                    f'already been written as post {written}. Do not write it '
                    'again unless the user asks for another version.'
                )
            else:
                lines.append(
                    f'- Outline APPROVED and not yet written: "{title}" '
                    f'(id {outline_id}). You may call create_blog with this '
                    'outline_id now.'
                )

    if not search_available:
        lines.append(
            '- Web search is NOT configured on this deployment. Do not call '
            'search_web. Say once, briefly, that you are working from your own '
            'knowledge rather than live sources, and never invent citations.'
        )

    if blog_count:
        lines.append(f'- Posts written in this conversation so far: {blog_count}.')

    if not lines:
        return ''

    return '\n\n# CURRENT STATE\n\n' + '\n'.join(lines) + '\n'


def history_contents(messages, limit=HISTORY_MESSAGES):
    """Past messages as Gemini ``contents``, oldest first.

    Only the prose is replayed, not the tool calls. That is a considered
    reduction: the model does not need to re-derive *how* it found something out,
    it needs to know what was said and what the current state is -- and the state
    block carries the latter authoritatively. Replaying every tool call and
    result would multiply the context of a long conversation for information that
    is either already summarised in the reply or already in the state block.

    A ``system`` message (there are a few -- "approved via the button", "the
    delete went through") is replayed as a user turn. Gemini has no system role
    inside ``contents``, and these are facts the model must not contradict, so
    the user role is the correct one: it is not the model's own claim.
    """
    contents = []
    for message in (messages or [])[-limit:]:
        text = (message.get('text') or '').strip()
        if not text:
            continue
        role = 'model' if message.get('role') == 'agent' else 'user'
        if message.get('role') == 'system':
            text = f'[system] {text}'
        contents.append({
            'role': role,
            'parts': [{'text': text[:MAX_HISTORY_CHARS]}],
        })
    return contents
