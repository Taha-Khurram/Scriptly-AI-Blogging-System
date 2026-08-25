"""Outline tools -- the human-in-the-loop gate, as code.

Three tools, and the shape of the flow is visible in what they are allowed to
do:

``create_outline``
    Drafts a plan and stores it as ``pending_approval``. There is no argument
    that makes it store anything else.

``revise_outline``
    Supersedes a pending plan and stores its replacement, also as
    ``pending_approval``. A revision cannot inherit approval from the version it
    replaced -- that is the whole point of revising.

``submit_outline_approval``
    Asks the server to *check* whether the user's own last message was an
    approval. It takes no ``approved`` flag. The model cannot assert approval
    here; it can only ask for the user's words to be read (see
    :mod:`app.agent.approval`).

Why the gate is not in the prompt
---------------------------------

The system prompt does tell the agent to propose an outline and wait. Prompts
are the right place for *intent*, and the wrong place for *guarantees*: one
ambiguous turn, one summarised history, one model update, and the agent writes
the post anyway. The user in that case has lost minutes of model time and gained
a draft they did not agree to.

So the guarantee lives in the data. ``create_blog`` loads the outline and refuses
unless ``status == 'approved'``, a state only :meth:`ChatRepository.approve_outline`
writes, which only a user-initiated request calls. The prompt asks nicely; the
tool cannot be talked round.
"""
from __future__ import annotations

from app.agent.approval import is_affirmative, is_revision_request
from app.agents.outline_agent import OutlineAgent, resolve_length
from app.core.logging import get_logger
from app.services.gemini_client import GeminiError

logger = get_logger(__name__)

# Keywords the caller may pass through to the writer. A model handed an open
# list will pass twenty; past a handful they stop being keywords and start
# being a second brief.
MAX_KEYWORDS = 10


def create_outline(ctx, topic=None, research_notes=None, tone=None,
                   length=None, keywords=None, **_ignored):
    """Draft a structured outline and store it awaiting the user's approval."""
    topic = (topic or '').strip()
    if not topic:
        return {
            'ok': False,
            'error': 'missing_topic',
            'message': 'An outline needs a topic. Ask the user what to write about.',
        }

    ctx.emit('status', stage='outlining', label='Planning the post')

    try:
        outline = OutlineAgent().create_outline(
            topic,
            research_notes=research_notes,
            tone=tone,
            length=length,
            keywords=_clean_keywords(keywords),
        )
    except GeminiError as exc:
        # The typed Gemini errors carry messages written for a reader ("at its
        # request limit", "declined this topic"). Passing the message through
        # rather than replacing it is what lets the agent say something true
        # about why it stopped.
        return {
            'ok': False,
            'error': getattr(exc, 'code', 'ai_error'),
            'message': getattr(exc, 'message', None) or str(exc),
        }
    except (ValueError, TypeError) as exc:
        logger.warning('Outline came back unusable: %s', exc)
        return {
            'ok': False,
            'error': 'unusable_outline',
            'message': (
                f'The outline came back unusable ({exc}). Try again with a more '
                'specific topic.'
            ),
        }

    return _store(ctx, outline, topic=topic, tone=tone, length=length,
                  keywords=_clean_keywords(keywords),
                  researched=bool(research_notes))


def revise_outline(ctx, feedback=None, outline_id=None, **_ignored):
    """Rework the pending outline against the user's feedback.

    The old version is superseded, not edited in place. Two reasons, and the
    second is the one that matters:

    * The user can see what moved, because both versions exist.
    * An approval cannot land on the version the user rejected. If revising
      mutated the record, a "yes" arriving a moment after a revision request
      would approve whichever write won the race -- and the user would have
      approved a plan they never read.
    """
    feedback = (feedback or '').strip()
    if not feedback:
        return {
            'ok': False,
            'error': 'missing_feedback',
            'message': 'Say what to change about the outline.',
        }

    outline_id = (outline_id or ctx.focus_outline_id or '').strip()
    if not outline_id:
        return {
            'ok': False,
            'error': 'no_outline',
            'message': (
                'There is no outline to revise in this conversation. Call '
                'create_outline first.'
            ),
        }

    previous = ctx.db.get_outline(outline_id, ctx.user_id)
    if not previous:
        return {
            'ok': False,
            'error': 'outline_not_found',
            'message': 'That outline no longer exists. Draft a fresh one.',
        }
    if previous.get('status') == 'approved':
        # Not an error the user should be blocked by, but it does change what
        # happens next: the approved plan stays approved and this becomes a new
        # proposal, so the agent must not imply the old approval carries over.
        logger.info('Revising an already-approved outline %s', outline_id)

    ctx.emit('status', stage='outlining', label='Reworking the outline')

    try:
        outline = OutlineAgent().create_outline(
            previous.get('topic') or previous.get('title') or '',
            research_notes=None,
            tone=previous.get('tone'),
            length=previous.get('length'),
            keywords=previous.get('keywords'),
            feedback=feedback,
            previous_outline={
                'title': previous.get('title'),
                'angle': previous.get('angle'),
                'audience': previous.get('audience'),
                'sections': previous.get('sections'),
            },
        )
    except GeminiError as exc:
        return {
            'ok': False,
            'error': getattr(exc, 'code', 'ai_error'),
            'message': getattr(exc, 'message', None) or str(exc),
        }
    except (ValueError, TypeError) as exc:
        return {
            'ok': False,
            'error': 'unusable_outline',
            'message': f'The revised outline came back unusable ({exc}).',
        }

    # Retire the old one first. If the store below fails, the user is left with
    # no pending outline rather than with two -- which is recoverable by asking
    # again, whereas two live plans for one topic is a gate with a hole in it.
    ctx.db.supersede_outline(outline_id, ctx.user_id)

    # Sources carry over: the research was done for this topic and the revision
    # did not un-do it. Re-searching would spend the turn's search budget on
    # facts already in hand.
    if previous.get('sources') and not outline.get('sources'):
        outline['sources'] = previous['sources']

    return _store(
        ctx, outline,
        topic=previous.get('topic') or '',
        tone=previous.get('tone'),
        length=previous.get('length'),
        keywords=previous.get('keywords'),
        researched=bool(previous.get('researched')),
        revision_of=outline_id,
        revision=int(previous.get('revision') or 1) + 1,
        feedback=feedback,
    )


def submit_outline_approval(ctx, outline_id=None, **_ignored):
    """Ask the server to check whether the user's last message approved the plan.

    Note what is absent from the signature: there is no ``approved`` parameter.
    The model is telling the server "I think this was a yes, please verify",
    and the verification reads :attr:`ToolContext.last_user_message` -- the
    user's actual text, stored before the model saw it.

    A rejection is not an error. It comes back ``ok: True, approved: False``
    with the reason, because the correct next move is a question to the user,
    not a failure banner.
    """
    outline_id = (outline_id or ctx.focus_outline_id or '').strip()
    if not outline_id:
        return {
            'ok': False,
            'error': 'no_outline',
            'message': 'There is no outline pending approval in this conversation.',
        }

    outline = ctx.db.get_outline(outline_id, ctx.user_id)
    if not outline:
        return {
            'ok': False,
            'error': 'outline_not_found',
            'message': 'That outline no longer exists.',
        }

    if outline.get('status') == 'approved':
        # Idempotent: a second "yes" after the first one landed is a normal
        # thing for a person to say.
        ctx.focus_outline(outline_id)
        return {
            'ok': True,
            'approved': True,
            'outline_id': outline_id,
            'already': True,
            'message': 'Already approved. You may call create_blog with this outline_id.',
        }

    if outline.get('status') == 'superseded':
        return {
            'ok': True,
            'approved': False,
            'reason': 'superseded',
            'message': (
                'That outline was replaced by a revision, so it cannot be '
                'approved. Point the user at the current version.'
            ),
        }

    user_text = ctx.last_user_message or ''

    if not is_affirmative(user_text):
        return {
            'ok': True,
            'approved': False,
            'reason': 'not_an_approval',
            'looks_like_revision': is_revision_request(user_text),
            'message': (
                "The user's message does not read as a clear approval, so "
                'nothing was approved. Do NOT call create_blog. If they asked '
                'for changes, call revise_outline; otherwise ask plainly '
                'whether to write the post as outlined.'
            ),
        }

    approved = ctx.db.approve_outline(outline_id, ctx.user_id, via='chat')
    if not approved:
        return {
            'ok': False,
            'error': 'approval_failed',
            'message': (
                'The approval could not be recorded, so the outline is still '
                'pending. Ask the user to use the Approve button on the outline '
                'card.'
            ),
        }

    ctx.focus_outline(outline_id)
    ctx.add_card('outline_approved', {
        'outline_id': outline_id,
        'title': approved.get('title', ''),
        'via': 'chat',
    })

    return {
        'ok': True,
        'approved': True,
        'outline_id': outline_id,
        'title': approved.get('title', ''),
        'message': (
            'Approved by the user. You may now call create_blog with this '
            'outline_id.'
        ),
    }


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------

def _store(ctx, outline, **fields):
    """Persist a pending outline, show it, and tell the model to stop.

    The ``message`` is the most important string in this module. It is what the
    model reads immediately after producing an outline, at exactly the moment it
    is most inclined to keep going and write the post -- so it says, in the
    imperative, that the turn ends here.
    """
    outline_id = ctx.db.create_outline_record(
        ctx.user_id, ctx.session_id, outline,
        topic=fields.get('topic'),
        tone=fields.get('tone'),
        length=fields.get('length'),
        keywords=fields.get('keywords'),
        researched=fields.get('researched'),
        revision_of=fields.get('revision_of'),
        revision=fields.get('revision', 1),
    )

    if not outline_id:
        return {
            'ok': False,
            'error': 'store_failed',
            'message': (
                'The outline could not be saved, so it cannot be approved or '
                'written. Tell the user something went wrong saving it and '
                'offer to try again.'
            ),
        }

    ctx.focus_outline(outline_id)

    words, _, _ = resolve_length(fields.get('length'))

    card = {
        'outline_id': outline_id,
        'title': outline.get('title', ''),
        'angle': outline.get('angle', ''),
        'audience': outline.get('audience', ''),
        'sections': outline.get('sections', []),
        'sources': outline.get('sources', []),
        'status': 'pending_approval',
        'tone': fields.get('tone') or 'professional',
        'length': fields.get('length') or 'medium',
        'target_words': words,
        'revision': fields.get('revision', 1),
        'revised_from': fields.get('revision_of') or '',
        'feedback': fields.get('feedback') or '',
    }
    ctx.add_card('outline', card)

    return {
        'ok': True,
        'outline_id': outline_id,
        'status': 'pending_approval',
        'title': outline.get('title', ''),
        'section_count': len(outline.get('sections') or []),
        'source_count': len(outline.get('sources') or []),
        'outline': {
            'title': outline.get('title', ''),
            'angle': outline.get('angle', ''),
            'sections': [
                s.get('heading', '') for s in (outline.get('sections') or [])
            ],
        },
        'message': (
            'The outline is saved and shown to the user, awaiting approval. '
            'STOP HERE. Do not call create_blog — it will refuse an unapproved '
            'outline. End your turn by summarising the plan in two or three '
            'lines and asking whether it works or should be adjusted.'
        ),
    }


def _clean_keywords(keywords):
    """Normalise the keyword argument, which arrives in three shapes.

    A list, a comma-separated string, or a single string. All three are what a
    user means; rejecting two of them would be the tool being pedantic about
    something the model got substantively right.
    """
    if not keywords:
        return []
    if isinstance(keywords, str):
        keywords = [part for part in keywords.split(',')]
    out = []
    for keyword in list(keywords)[:MAX_KEYWORDS * 2]:
        text = str(keyword).strip()
        if text and text not in out:
            out.append(text[:80])
        if len(out) >= MAX_KEYWORDS:
            break
    return out
