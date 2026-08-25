"""Reading approval out of what the user actually typed.

The human-in-the-loop gate has two doors. One is a button, and it needs no
interpretation: a POST arrived from a signed-in user's own browser, so the
approval is exactly as real as the session. The other is the user typing "yeah
that works, go for it", and that one needs a decision about what their words
mean.

The decision must not be the model's
------------------------------------

An agent that can decide "the user approved" is an agent with no approval step.
Not because a model is untrustworthy in general, but because *this* judgement is
precisely the one it is motivated to get wrong: it has an outline in hand, a
clear next action, and every incentive to read "hmm, not sure about section 3"
as permission to proceed.

So the approval tool does not accept an ``approved=True`` argument. It re-reads
the user's own last message from the turn context and applies the rules here.
The model's role is reduced to *noticing* that an approval may have happened and
asking for it to be checked -- which is a routing decision, and the right kind of
work for it.

Why patterns and not a classifier call
--------------------------------------

A second model call to classify "is this a yes" would be slower, cost money per
turn, and -- being a model -- could be talked into the same wrong answer by the
same conversation. It would also fail closed in the worst way: an outage in the
classifier would block every approval.

These rules are deliberately narrow. Anything they do not recognise is *not* an
approval, and the agent then asks the plain question ("shall I write it?"). One
extra exchange is a small cost. Writing a post the user was still thinking about
is not: it spends minutes of model time and puts something in their drafts they
did not ask for.
"""
from __future__ import annotations

import re

# Bare affirmatives. Anchored so "yes" matches and "yesterday" does not, and so
# "no, wait" cannot match on its trailing words.
_AFFIRMATIVE = re.compile(
    r'^\s*(?:'
    r'y|ya|yea|yeah|yep|yup|yes|sure|ok|okay|k|'
    r'go|go ahead|go for it|proceed|continue|'
    r'do it|write it|write|send it|ship it|'
    r'approve|approved|approval|accept|accepted|'
    r'sounds good|looks good|lgtm|perfect|great|nice|love it|'
    r'that works|works for me|good to go|all good|fine|'
    r'confirm|confirmed'
    r')\b',
    re.I,
)

# Phrases that mean yes but do not start the message. Kept separate because
# matching them anywhere is a weaker signal, so they are only trusted when
# nothing in :data:`_NEGATION` is present.
_AFFIRMATIVE_ANYWHERE = re.compile(
    r'\b(?:'
    r'go ahead|go for it|write it|write the (?:post|blog|article)|'
    r'looks? good|sounds? good|lgtm|approved?|ship it|'
    r'happy with (?:it|that|this)|no changes|nothing to change|'
    r"that'?s (?:it|good|great|perfect|fine)"
    r')\b',
    re.I,
)

# Anything here disqualifies the message, wherever it appears. These are the
# words that turn an approval into a condition, and a conditional approval is a
# revision request -- "yes but make section 2 shorter" is not permission to
# write the outline as it stands.
#
# Four groups, and why each is here:
#
# * Refusals and pauses -- the obvious no.
# * Contrastives and orderings ("but", "except", "after", "once", "first").
#   These are the dangerous ones, because the message still *opens* with a yes:
#   "approve it after you fix the heading" reads as approval to any rule that
#   only looks at the first word, and it plainly is not one.
# * Verbs of change, including the bare imperative "make". "Make the intro
#   punchier" is the most common follow-up in this product and is never an
#   approval -- which is why "make it" was dropped from the affirmatives above
#   rather than special-cased here.
# * Hedges. Someone who is unsure has not approved.
_NEGATION = re.compile(
    r'\b(?:'
    r'no|not|nope|nah|don\'?t|do not|stop|wait|hold on|hold off|'
    r'but|however|except|instead|before|first|after|once|when|unless|'
    r'change|revise|revision|edit|adjust|tweak|swap|replace|rework|'
    r'rewrite|redo|fix|make|move|split|merge|trim|'
    r'add|remove|drop|cut|shorten|shorter|lengthen|longer|expand|'
    r'reorder|rename|punchier|tighter|better|'
    r'shouldn\'?t|can we|could we|what about|how about|'
    r'unsure|not sure|hmm|maybe|perhaps|actually'
    r')\b',
    re.I,
)

# A message longer than this is not a bare approval however it starts. "Yes, and
# while you're there, ..." is a brief, and treating it as a green light on the
# unmodified outline drops the rest of what the user said.
MAX_APPROVAL_CHARS = 120


def is_affirmative(text):
    """True when ``text`` is an unconditional approval and nothing else.

    Conservative by construction: three separate ways to fail (too long, a
    disqualifying word, no affirmative at all) and only one narrow way to pass.
    A false negative costs one clarifying question; a false positive spends
    minutes of model time writing something nobody asked for.
    """
    message = (text or '').strip()
    if not message:
        return False
    if len(message) > MAX_APPROVAL_CHARS:
        return False
    if _NEGATION.search(message):
        return False
    return bool(_AFFIRMATIVE.match(message) or _AFFIRMATIVE_ANYWHERE.search(message))


def is_revision_request(text):
    """True when ``text`` reads as "change the plan" rather than "go".

    Used only to phrase the agent's next move -- there is no gate behind it, so
    it can afford to be loose where :func:`is_affirmative` cannot.
    """
    message = (text or '').strip()
    if not message:
        return False
    return bool(_NEGATION.search(message))
