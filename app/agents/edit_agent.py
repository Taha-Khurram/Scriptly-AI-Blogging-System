"""Edit agent -- applies a targeted change to a post that already exists.

The capability the single-shot generator had no room for. Before this, changing
a published post meant opening TinyMCE and doing it by hand, or regenerating the
whole thing from the topic and losing every edit anyone had made since.

The one rule that matters
-------------------------

**Return the whole post, change only what was asked.** A model asked to "make
the intro punchier" will, given the chance, also retitle the piece, reorder the
sections, drop the FAQ and rewrite the conclusion -- all of it defensible, none
of it requested. So the prompt is built around the untouched-by-default
principle, and :func:`describe_change` reports what actually moved so the user
can see whether it obeyed.

Returning the full document rather than a patch is deliberate. A diff-style
edit ("replace lines 40-48 with...") is cheaper in tokens and much worse in
practice: markdown has no stable line identity, the model miscounts, and a
mis-applied patch corrupts a post silently. A full return is verifiable -- the
word count, the heading list and the section count are all checkable against
what went in, which is what :func:`describe_change` does.

Tone changes are the exception to "change only what was asked", and the prompt
says so: "make it more casual" is a licence to touch every sentence. The
distinction is carried by the instruction the user gave, not by a mode flag,
because a mode flag would be one more thing for the calling agent to get wrong.
"""
from __future__ import annotations

import re

from app.agents.content_agent import WRITING_RULES, _tidy
from app.core.logging import get_logger
from app.services.gemini_client import gemini, GeminiResponseError

logger = get_logger(__name__)

# An edit rewrites a whole post, so it is the same order of work as writing one.
EDIT_TIMEOUT_SECONDS = 180

# Below this the model did not return a post; it returned a comment about one.
MIN_USABLE_CHARS = 300

# The post is sent in full and comes back in full, so this is the ceiling on
# both. Well above a long post (~12 KB); the bound is for a document that has
# been edited into something pathological.
MAX_CONTENT_CHARS = 60_000

# One instruction. Bounded because it goes into a prompt, and because an
# "instruction" longer than this is a new blog post rather than an edit.
MAX_INSTRUCTION_CHARS = 1_000

_HEADING_RE = re.compile(r'^##\s+(.+?)\s*$', re.MULTILINE)


class EditAgent:
    """Applies one natural-language instruction to one post."""

    def edit(self, content, instructions, *, title=''):
        """Return ``{'markdown', 'title_suggestion', 'change'}`` for an edit.

        Raises rather than returning an error dict, for the same reason
        :class:`~app.agents.outline_agent.OutlineAgent` does: the caller is a
        tool inside a conversation and needs the typed Gemini exception to
        choose its sentence. A quota error and a safety block are different
        problems for the user.
        """
        content = (content or '')[:MAX_CONTENT_CHARS]
        instructions = (instructions or '').strip()[:MAX_INSTRUCTION_CHARS]

        if not content.strip():
            raise ValueError('There is no content to edit.')
        if not instructions:
            raise ValueError('No edit instruction was given.')

        edited = gemini.generate_text(
            self._build_prompt(content, instructions, title),
            timeout=EDIT_TIMEOUT_SECONDS,
            label='blog_edit',
            generation_config={'temperature': 0.6},
        )
        edited = _tidy(edited)

        if len(edited) < MIN_USABLE_CHARS:
            raise GeminiResponseError(
                'The edit came back too short to be the post. Nothing was '
                'changed — try describing the change differently.'
            )

        return {
            'markdown': edited,
            # A retitle is proposed, never applied here. The title is also the
            # slug, and a slug change breaks inbound links -- so the decision
            # belongs to the tool layer, which knows whether the post is
            # published, and ultimately to the user.
            'title_suggestion': self._suggest_title(edited, title),
            'change': describe_change(content, edited),
        }

    # --- Prompt -----------------------------------------------------------

    @staticmethod
    def _build_prompt(content, instructions, title):
        return (
            'You are a senior editor revising a blog post that is already '
            'written. You are not rewriting it from scratch.\n\n'

            '=== THE EDIT (this, and nothing else) ===\n'
            f'{instructions}\n\n'

            '=== RULES ===\n'
            '- Return the COMPLETE post, from its first line to its last. Not '
            'a diff, not an excerpt, not a summary of what you changed.\n'
            '- Change ONLY what the instruction asks for. Everything the '
            'instruction did not mention must come back byte-identical: same '
            'headings, same order, same examples, same FAQ, same conclusion.\n'
            '- The exception is a tone or voice instruction ("more casual", '
            '"less salesy"), which is licence to touch every sentence. A '
            'structural or sectional instruction is not.\n'
            '- Do NOT add an H1 "# " title. The title is stored separately.\n'
            '- Keep the existing markdown conventions: "## " for sections, '
            'bold for genuinely important terms, lists only where a list is '
            'the clearest format.\n'
            '- If the instruction is impossible or refers to something that is '
            'not in the post, return the post UNCHANGED rather than inventing '
            'the thing being referred to.\n'
            '- Return ONLY the markdown. No preamble, no "here is the edited '
            'version", no code fences.\n\n'

            # The same standards the post was written to. Without them an edit
            # is where the banned AI tells creep back in one paragraph at a
            # time -- each edit individually plausible, the post steadily worse.
            '=== THE STANDARDS THIS POST WAS WRITTEN TO (keep meeting them) ===\n'
            + WRITING_RULES +

            f'=== CURRENT TITLE ===\n{title or "(untitled)"}\n\n'
            f'=== CURRENT POST ===\n{content}'
        )

    @staticmethod
    def _suggest_title(edited, current_title):
        """A new title only when the edit clearly outgrew the old one.

        Cheap and local: no second model call for something the user did not
        ask for. If the post now opens with an H1 the model added despite being
        told not to, that is taken as its title suggestion and stripped from
        the body by the caller.
        """
        match = re.match(r'^#\s+(.+?)\s*$', edited, re.MULTILINE)
        if match:
            candidate = match.group(1).strip()
            if candidate and candidate.lower() != (current_title or '').lower():
                return candidate[:300]
        return ''


def describe_change(before, after):
    """What actually moved between two versions of a post.

    Reported back to the user through the chat, and the reason is trust. "I made
    the intro punchier" is a claim; "the intro is 40 words shorter, the six
    section headings are unchanged" is evidence. When a model over-edits -- and
    it does -- this is what surfaces it in the same turn rather than a week
    later when someone rereads the post.
    """
    before_words = len((before or '').split())
    after_words = len((after or '').split())
    before_headings = _HEADING_RE.findall(before or '')
    after_headings = _HEADING_RE.findall(after or '')

    added = [h for h in after_headings if h not in before_headings]
    removed = [h for h in before_headings if h not in after_headings]

    return {
        'word_count_before': before_words,
        'word_count_after': after_words,
        'word_delta': after_words - before_words,
        'sections_before': len(before_headings),
        'sections_after': len(after_headings),
        'sections_added': added[:6],
        'sections_removed': removed[:6],
        # True when the structure survived, which is the usual expectation for
        # a targeted edit and the thing worth flagging when it did not.
        'structure_unchanged': before_headings == after_headings,
        'unchanged': (before or '').strip() == (after or '').strip(),
    }


def summarise_change(change):
    """One line of prose describing a :func:`describe_change` result."""
    if change.get('unchanged'):
        return 'Nothing changed — the post came back identical.'

    delta = change.get('word_delta') or 0
    if delta > 0:
        length = f'{delta} words longer'
    elif delta < 0:
        length = f'{abs(delta)} words shorter'
    else:
        length = 'the same length'

    if change.get('structure_unchanged'):
        structure = f"all {change.get('sections_after', 0)} sections intact"
    else:
        moves = []
        if change.get('sections_added'):
            moves.append(f"added {len(change['sections_added'])}")
        if change.get('sections_removed'):
            moves.append(f"removed {len(change['sections_removed'])}")
        structure = 'sections ' + (' and '.join(moves) or 'reordered')

    return f'{length}, {structure}.'
