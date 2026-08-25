"""Outline agent -- drafts the plan the user approves before anything is written.

The step the single-shot pipeline deliberately skipped. ``BlogAgent.run_pipeline``
derives an "outline" *after the fact*, by regexing ``##`` headings out of a post
that has already been written; its docstring says why -- one model call is the
fastest possible turnaround, and a separate outline round trip was pure latency
when nobody was going to read the outline anyway.

That trade-off inverts the moment a human is in the loop. The outline is now the
thing the user reads, argues with and approves, and the post is written against
it afterwards. So this agent produces a *real* plan -- an angle, a named
audience, sections with the points each will actually make -- and it produces it
before a single word of prose exists.

Two properties are load-bearing:

* **It returns structured data, not prose.** An outline the user approves has to
  be storable, revisable and diffable, and a paragraph of markdown is none of
  those. ``generate_json`` with a strict shape is what lets the approval gate be
  a field on a document (see :mod:`app.repositories.chat`) rather than a guess
  about what the model meant.
* **It never invents sources.** Research notes are passed in from
  ``search_web`` results or not at all. A model asked to "include sources" with
  nothing to cite will produce plausible URLs that do not exist, and an outline
  the user approved because of its sources is the worst place for that to
  happen.
"""
from __future__ import annotations

import json

from app.core.logging import get_logger
from app.services.gemini_client import gemini

logger = get_logger(__name__)

# An outline is a page of structure, not a draft. Well inside any model's
# comfortable output, so the deadline is short: this call sits in the middle of
# a conversation the user is watching.
OUTLINE_TIMEOUT_SECONDS = 60

# Bounds the prompt, not the answer. Research notes come from search snippets,
# which are already clipped per result -- this is the ceiling on all of them
# together, so a wide search cannot spend the whole context window.
MAX_RESEARCH_CHARS = 4_000

TONE_GUIDE = {
    'professional': 'measured and authoritative, written for a working professional',
    'conversational': 'direct and personable, like explaining it to a smart friend',
    'technical': 'precise and detail-first, assuming a technically literate reader',
    'casual': 'relaxed and plain-spoken, contractions welcome',
    'persuasive': 'opinionated and argument-led, taking a clear position',
}

LENGTH_GUIDE = {
    'short': ('600-800 words', 3, 4),
    'medium': ('900-1100 words', 4, 6),
    'long': ('1400-1800 words', 6, 8),
}


def resolve_length(length):
    """``(words, min_sections, max_sections)`` for a length label.

    Unknown labels fall through to medium rather than raising. The value is
    model-supplied, so an unrecognised one is a routine event and the right
    response is the sensible default -- not an error the user has to decode.
    """
    return LENGTH_GUIDE.get((length or '').strip().lower(), LENGTH_GUIDE['medium'])


def resolve_tone(tone):
    key = (tone or '').strip().lower()
    return TONE_GUIDE.get(key, TONE_GUIDE['professional'])


class OutlineAgent:
    """Turns a topic (plus optional research) into a structured, reviewable plan."""

    def create_outline(self, topic, *, research_notes=None, tone=None,
                       length=None, keywords=None, feedback=None,
                       previous_outline=None):
        """Draft an outline. Returns a dict, or raises a :class:`GeminiError`.

        ``feedback`` and ``previous_outline`` together are the revision path:
        given both, the model is asked to change the plan it is shown rather
        than to invent a new one. That is the difference between "make section 2
        about pricing" producing an edited outline and producing an unrelated
        second outline that happens to mention pricing.

        Raising rather than returning an error dict is deliberate here. The
        caller is a tool (:mod:`app.agent.tools.outlines`) whose job includes
        turning a failure into something the *conversation* can continue from,
        and it needs the typed Gemini exception to do that -- a quota error and
        a safety block need different sentences.
        """
        words, min_sections, max_sections = resolve_length(length)

        prompt = self._build_prompt(
            topic, research_notes=research_notes, tone=tone, keywords=keywords,
            words=words, min_sections=min_sections, max_sections=max_sections,
            feedback=feedback, previous_outline=previous_outline,
        )

        raw = gemini.generate_json(
            prompt,
            timeout=OUTLINE_TIMEOUT_SECONDS,
            label='blog_outline',
            generation_config={'temperature': 0.7},
        )

        return self._normalise(raw, topic, min_sections)

    # --- Prompt -----------------------------------------------------------

    def _build_prompt(self, topic, *, research_notes, tone, keywords, words,
                      min_sections, max_sections, feedback, previous_outline):
        parts = [
            'You are a senior content strategist planning a blog post that has '
            'to earn its place: specific, opinionated, and useful to someone '
            'who already knows the basics.\n',
            f'TOPIC: {topic}\n',
            f'TARGET LENGTH: {words} '
            f'({min_sections}-{max_sections} body sections).\n',
            f'TONE: {resolve_tone(tone)}.\n',
        ]

        if keywords:
            parts.append(
                'KEYWORDS to work in naturally (do not stuff them): '
                + ', '.join(str(k) for k in keywords[:10]) + '\n'
            )

        if research_notes:
            parts.append(
                '\n=== RESEARCH (from a live web search — the only facts you may '
                'cite) ===\n'
                + _format_research(research_notes)
                + '\nUse these for specifics and cite them in `sources`. Do NOT '
                'add any source that is not listed above, and do not invent '
                'URLs, statistics or dates. If the research does not support a '
                'point, make the point without a citation.\n'
            )
        else:
            parts.append(
                '\nNo web research was performed for this outline. Leave '
                '`sources` as an empty list — do NOT invent citations, URLs or '
                'fake-precise statistics. Plan the piece from general knowledge '
                'and lean on reasoning and concrete scenarios rather than '
                'numbers you cannot verify.\n'
            )

        if previous_outline and feedback:
            parts.append(
                '\n=== REVISION ===\n'
                'This is a revision, not a new plan. Here is the outline the '
                'user has already seen:\n'
                + json.dumps(previous_outline, indent=2, default=str)[:3000]
                + f'\n\nThe user asked for this change:\n"{feedback}"\n\n'
                'Apply exactly that change. Keep everything the user did not '
                'ask you to change — including section order and wording — so '
                'they can see what moved.\n'
            )

        parts.append(
            '\n=== OUTPUT ===\n'
            'Return ONLY a JSON object with this exact shape:\n'
            '{\n'
            '  "title": "the working headline — specific, not a category label",\n'
            '  "angle": "one sentence on the specific take, and why it beats '
            'the obvious one",\n'
            '  "audience": "who this is for and what they already know",\n'
            '  "sections": [\n'
            '    {"heading": "a real sub-topic, not \'Introduction\'", '
            '"points": ["the concrete claim or example this section makes"]}\n'
            '  ],\n'
            '  "sources": [{"title": "...", "url": "..."}]\n'
            '}\n\n'
            'Rules: 2-4 points per section, each a specific claim or example '
            'rather than a topic label. No "Introduction" or "Conclusion" '
            'sections — those are written, not planned. No markdown, no '
            'commentary outside the JSON.'
        )

        return ''.join(parts)

    # --- Shaping ----------------------------------------------------------

    @staticmethod
    def _normalise(raw, topic, min_sections):
        """Coerce the model's JSON into the shape the rest of the app expects.

        Lenient on purpose. ``generate_json`` already repairs fences and
        trailing commas, but a model still returns ``sections`` as a list of
        strings often enough to matter. An outline that arrives slightly
        off-schema should reach the user for approval, not become an error they
        cannot act on -- so a shape that can be read is read, and only an
        unusable one raises.
        """
        if not isinstance(raw, dict):
            raise ValueError('The outline response was not a JSON object.')

        sections = []
        for entry in (raw.get('sections') or []):
            if isinstance(entry, str):
                sections.append({'heading': entry.strip(), 'points': []})
            elif isinstance(entry, dict):
                heading = (entry.get('heading') or entry.get('title') or '').strip()
                points = entry.get('points') or entry.get('key_points') or []
                if isinstance(points, str):
                    points = [points]
                if heading:
                    sections.append({
                        'heading': heading,
                        'points': [str(p).strip() for p in points if str(p).strip()],
                    })

        if len(sections) < min(2, min_sections):
            # Two sections is not an outline; it is a shrug. Better to fail and
            # let the tool retry or explain than to send the user a plan they
            # cannot meaningfully approve.
            raise ValueError(
                f'The outline came back with {len(sections)} sections, which is '
                'too few to review.'
            )

        sources = []
        for source in (raw.get('sources') or []):
            if isinstance(source, dict):
                title = (source.get('title') or source.get('name') or '').strip()
                url = (source.get('url') or source.get('link') or '').strip()
                if title or url:
                    sources.append({'title': title or url, 'url': url})
            elif isinstance(source, str) and source.strip():
                sources.append({'title': source.strip(), 'url': ''})

        return {
            'title': (raw.get('title') or topic).strip()[:300],
            'angle': (raw.get('angle') or '').strip()[:400],
            'audience': (raw.get('audience') or '').strip()[:400],
            'sections': sections,
            'sources': sources,
        }


def _format_research(notes):
    """Render search results (or free text) as a compact research block.

    Accepts what ``search_web`` returns *and* a plain string, because the model
    is allowed to pass ``research_notes`` as prose it wrote itself -- summarising
    what it found is a reasonable thing for it to do, and refusing the string
    form would make the tool call fail for a benign reason.
    """
    if isinstance(notes, str):
        return notes[:MAX_RESEARCH_CHARS]

    lines = []
    budget = MAX_RESEARCH_CHARS
    for index, item in enumerate(notes or [], start=1):
        if isinstance(item, dict):
            line = '[{}] {}\n    {}\n    {}\n'.format(
                index,
                (item.get('title') or 'Untitled').strip(),
                (item.get('url') or '').strip(),
                (item.get('snippet') or '').strip(),
            )
        else:
            line = f'[{index}] {item}\n'
        if len(line) > budget:
            break
        lines.append(line)
        budget -= len(line)
    return ''.join(lines) or '(no usable research results)'
