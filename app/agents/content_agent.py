"""Content agent - writes the blog post.

Two entry points over one prompt:

* :meth:`generate_blog` waits for the whole post and returns it. Correct for
  anything that only uses the finished text.
* :meth:`stream_blog` reports the post as it is written, chunk by chunk, and
  asks the model to plan out loud first. The create screen shows the plan as
  the agent's reasoning and the draft as it lands, so a 40-second wait reads as
  work happening rather than as a spinner.

The plan is not decoration bolted on for the UI. It is emitted by the same call
that writes the post, ahead of it, and the post is written against it -- so what
appears in the reasoning panel is the reasoning that actually shaped the draft,
not a summary invented afterwards.
"""
import re
import time

from app.core.logging import get_logger
from app.services.gemini_client import gemini, GeminiResponseError

logger = get_logger(__name__)

# A ~1200-word generation routinely takes longer than the SDK's 60s default,
# which used to surface as "504 Deadline Exceeded".
GENERATE_TIMEOUT_SECONDS = 180

# Below this the response is not a blog post, however the stream ended.
MIN_USABLE_CHARS = 400

# The marker that separates the planning block from the post. Distinctive
# enough that it cannot occur in ordinary prose or markdown, and matched
# loosely (see _BLOG_MARKER_RE) because a model will sometimes bold it or
# change the run of equals signs.
BLOG_MARKER = '=== BLOG ==='
_PLAN_MARKER_RE = re.compile(r'^\W*=+\s*PLAN\s*=+\W*$', re.I)
_BLOG_MARKER_RE = re.compile(r'^\W*=+\s*BLOG\s*=+\W*$', re.I)
_FENCE_RE = re.compile(r'^```[a-z]*$', re.I)

# How much text may accumulate in the planning block before the splitter
# decides the model ignored the format. Past this it stops treating the output
# as a plan and hands everything it has to the content sink: a run that
# produced an unusually chatty preamble still yields a whole blog post, which
# matters far more than a tidy reasoning panel.
PLAN_CHAR_LIMIT = 1500

# Plan lines are one thought each. A model that decides to write an essay in
# the planning block would otherwise flood the panel.
MAX_PLAN_LINES = 8


class StreamSplitter:
    """Routes a streaming response into plan lines and post text.

    Fed arbitrary chunk boundaries -- a chunk can end mid-word or mid-marker --
    so nothing is dispatched from the plan side until a line is known to be
    complete. Content, by contrast, is passed straight through: it is being
    typed onto a screen, and holding it back to line boundaries would make a
    smooth stream arrive in paragraph-sized jumps.
    """

    def __init__(self, on_thought=None, on_content=None):
        self._on_thought = on_thought
        self._on_content = on_content
        self.in_body = False
        self.plan_lines = []
        self.content = []
        self._pending = ''      # incomplete trailing line, plan side only
        self._raw = ''          # everything seen, for the give-up path
        self._plan_chars = 0
        self._skip_fence = False
        # A line before the marker is only reasoning if the model actually
        # opened a planning block. Until the PLAN marker is seen, lines are
        # held: a model that ignored the format entirely is writing the post,
        # and its first heading must not be shown as a thought and then
        # repeated at the top of the draft.
        self._plan_seen = False
        self._held = []

    def feed(self, chunk):
        if not chunk:
            return
        self._raw += chunk

        if self.in_body:
            self._emit_content(chunk)
            return

        self._pending += chunk
        self._plan_chars += len(chunk)

        while '\n' in self._pending:
            line, self._pending = self._pending.split('\n', 1)
            if self._consume_plan_line(line):
                # Marker found: whatever followed it in this chunk is the
                # opening of the post and must not be held back.
                self.in_body = True
                rest, self._pending = self._pending, ''
                self._emit_content(rest)
                return

        if self._plan_chars > PLAN_CHAR_LIMIT:
            self._give_up_on_plan()

    def close(self):
        """Finish the stream and return the post text."""
        if not self.in_body:
            # No marker anywhere. The model wrote a post without the format, so
            # the "plan" was the post all along.
            self._give_up_on_plan()
        elif self._pending:
            rest, self._pending = self._pending, ''
            self._emit_content(rest)

        return self.markdown

    @property
    def markdown(self):
        return _tidy(''.join(self.content))

    # --- internals --------------------------------------------------------

    def _consume_plan_line(self, line):
        """Handle one complete pre-marker line. True when it was the marker."""
        text = line.strip()
        if _BLOG_MARKER_RE.match(text):
            # The plan block ended without ever being opened: the lines held
            # back were real plan lines after all, so they go out now rather
            # than being lost.
            for held in self._held:
                self._dispatch_thought(held)
            self._held = []
            self._skip_fence = True
            return True

        if _PLAN_MARKER_RE.match(text):
            self._plan_seen = True
            return False
        if not text:
            return False

        # Strip the bullet or numbering the prompt asks for, plus the bold a
        # model likes to add on its own.
        text = re.sub(r'^[-*•]\s*|^\d+[.)]\s*', '', text)
        text = text.strip('*_ ').strip()
        if not text:
            return False

        if self._plan_seen:
            self._dispatch_thought(text)
        elif len(self._held) < MAX_PLAN_LINES:
            self._held.append(text)
        return False

    def _dispatch_thought(self, text):
        if len(self.plan_lines) >= MAX_PLAN_LINES:
            return
        self.plan_lines.append(text)
        if self._on_thought:
            self._on_thought(text)

    def _emit_content(self, text):
        if not text:
            return

        # A leading ```markdown fence, which the prompt forbids and a model
        # produces anyway. Dropped before it reaches the screen rather than
        # cleaned out of the saved text afterwards.
        if self._skip_fence:
            stripped = text.lstrip('\n')
            if not stripped:
                return
            first, sep, rest = stripped.partition('\n')
            if _FENCE_RE.match(first.strip()):
                self._skip_fence = False
                if not sep:
                    return
                text = rest
                if not text:
                    return
            elif first.strip():
                self._skip_fence = False

        self.content.append(text)
        if self._on_content:
            self._on_content(text)

    def _give_up_on_plan(self):
        """Treat everything seen as post text after all.

        Any plan lines already dispatched stay on screen -- they were the
        model's own words about the piece, and pulling them back mid-run would
        be stranger than leaving them.
        """
        self.in_body = True
        self._skip_fence = False
        self._pending = ''
        self._held = []
        body = _PLAN_MARKER_RE.sub('', self._raw, count=1) if self._raw else ''
        self.content = []
        self._emit_content(body.lstrip('\n'))
        logger.warning(
            'Blog stream had no %s marker; treating the whole response as '
            'content (%s chars)', BLOG_MARKER, len(body),
        )


def _tidy(text):
    """Trim the wrapper artefacts a model leaves at either end."""
    out = (text or '').strip()
    if out.startswith('```'):
        out = re.sub(r'^```[a-z]*\s*\n?', '', out, flags=re.I)
    if out.endswith('```'):
        out = out[:-3].rstrip()
    return out


class ContentAgent:
    def __init__(self):
        self.model = gemini.get_model()

    # --- Prompt -----------------------------------------------------------

    def _build_prompt(self, topic, with_plan=False):
        """The writing brief, optionally preceded by a planning block.

        One prompt for both paths: the streamed and the waited-for post must not
        quietly become different products because they were requested
        differently.
        """
        plan_block = ''
        if with_plan:
            plan_block = (
                "=== OUTPUT FORMAT (follow exactly) ===\n"
                "Before writing, think on the page. Output this, and nothing else:\n\n"
                "=== PLAN ===\n"
                "- The specific angle you are taking on this topic, and why that "
                "angle is more useful than the obvious one.\n"
                "- Who this reader is and what they already know.\n"
                "- The 4-6 sections you will write, named.\n"
                "- The concrete example, number or scenario you will use to prove "
                "the point.\n"
                "- One thing you will deliberately leave out or avoid.\n"
                f"{BLOG_MARKER}\n"
                "Then the finished blog post in Markdown, written against the plan "
                "above.\n\n"
                "Rules for the format: each plan line is ONE line starting with "
                "'- ' (no line breaks inside a line), at most 6 lines. Emit the "
                f"'{BLOG_MARKER}' line exactly, on its own line. Do not repeat or "
                "mention the plan inside the post itself.\n\n"
            )

        return (
            "You are a senior human content writer and subject-matter expert with "
            "10+ years of hands-on experience in this field. You write for a "
            "publication that Google and AI search engines (Google AI Overviews, "
            "ChatGPT, Perplexity) trust and cite. Write a complete, original, "
            "deeply useful blog post (900-1100 words) on the topic below.\n\n"

            + plan_block +

            "=== SEARCH & AEO OPTIMIZATION (2026 ranking factors) ===\n"
            "- Answer the core search intent in the FIRST 2-3 sentences, directly and "
            "specifically — this is what gets pulled into AI Overviews and featured "
            "snippets. No throat-clearing before the answer.\n"
            "- Naturally cover related sub-questions, synonyms, and semantic variations "
            "of the topic (the way a true expert would talk about it), instead of "
            "repeating the exact keyword.\n"
            "- Include specific, concrete details: real numbers, timeframes, named tools, "
            "named methods, comparisons — vague generalities do not rank in 2026.\n"
            "- Add a short FAQ-style section near the end (2-3 real questions people "
            "search, each answered in 2-4 direct sentences) to capture People-Also-Ask "
            "and voice search queries.\n"
            "- Every section should give the reader something actionable or a concrete "
            "takeaway, not just an explanation.\n\n"

            "=== E-E-A-T (Experience, Expertise, Authority, Trust) ===\n"
            "- Write with a confident, first-hand point of view, as someone who has "
            "actually done this — include a specific example, mini case, scenario, "
            "or 'here's what actually happens' detail somewhere in the post.\n"
            "- State clear opinions/recommendations where relevant instead of hedging "
            "everything ('it depends', 'there are many factors') — take a position and "
            "back it briefly.\n"
            "- Be precise. If you're unsure of an exact statistic, describe the "
            "trend/magnitude honestly instead of inventing a fake-precise number.\n\n"

            "=== SOUND HUMAN, NOT AI-GENERATED ===\n"
            "- Vary sentence length aggressively — mix short punchy sentences with "
            "longer ones. Do NOT write uniform 15-20 word sentences back to back.\n"
            "- Do NOT use these overused AI words/phrases anywhere: 'delve', 'tapestry', "
            "'boast', 'unlock', 'unleash', 'elevate', 'game-changer', 'landscape', "
            "'realm', 'in today's fast-paced world', 'it's important to note', "
            "'navigate', 'seamless', 'robust', 'in conclusion', 'moreover', "
            "'furthermore', 'additionally' (as a sentence opener).\n"
            "- Do NOT start consecutive paragraphs or sections with the same "
            "transition pattern. Vary how sections open.\n"
            "- Avoid generic listicle filler ('there are many benefits to X'). Every "
            "bullet must contain a specific, non-obvious point.\n"
            "- Write like you're explaining this to a smart friend — direct, a little "
            "opinionated, occasionally conversational ('here's the thing', 'honestly'), "
            "not like a corporate brochure.\n"
            "- Avoid perfectly symmetrical structure (e.g. every section exactly 3 "
            "bullets, every paragraph exactly 4 sentences) — real writing is uneven.\n\n"

            "=== FORMATTING REQUIREMENTS ===\n"
            "- Open with a short, compelling introduction (do NOT add an H1 '# ' title).\n"
            "- Organize the body with 4-6 clear '## ' section headings that describe "
            "real sub-topics (not generic labels like 'Introduction' or 'Overview').\n"
            "- Use bold for genuinely important terms/takeaways (not decoratively), and "
            "bullet points only where a list is actually the clearest format.\n"
            "- Finish with a short, non-generic conclusion — a real takeaway or next "
            "step, not a summary restating the headings.\n"
            "- Return ONLY the blog post in Markdown. No preamble, no meta-commentary "
            "about the writing itself, no code fences.\n\n"

            f"TOPIC: {topic}"
        )

    # --- Generation -------------------------------------------------------

    def generate_blog(self, topic):
        """Write the whole post and return it. No progress reporting."""
        text = gemini.generate_text(
            self._build_prompt(topic),
            timeout=GENERATE_TIMEOUT_SECONDS,
            label='blog_content',
        )
        return self._package(_tidy(text), None, time.perf_counter())

    def stream_blog(self, topic, on_thought=None, on_content=None):
        """Write the post, reporting the plan and the text as they arrive.

        ``on_thought(text)`` fires once per complete plan line;
        ``on_content(chunk)`` fires for every piece of the post. Both are called
        on this thread, so a slow callback slows the read -- they are expected
        to do nothing heavier than appending to a buffer.

        A stream that breaks late keeps what it wrote: throwing away 900 words
        because the connection dropped on the last paragraph would be worse
        than saving a draft the author can finish. A stream that breaks early
        raises, because a two-paragraph stub filed as a finished blog post is
        worse than a visible failure.
        """
        splitter = StreamSplitter(on_thought=on_thought, on_content=on_content)
        started = time.perf_counter()

        try:
            for chunk in gemini.stream_text(
                self._build_prompt(topic, with_plan=True),
                timeout=GENERATE_TIMEOUT_SECONDS,
                label='blog_content_stream',
            ):
                splitter.feed(chunk)
        except Exception:
            partial = splitter.markdown
            if len(partial) < MIN_USABLE_CHARS:
                raise
            logger.warning(
                'Blog stream broke after %s chars; keeping the partial draft',
                len(partial),
            )
            return self._package(partial, splitter, started, partial=True)

        markdown_text = splitter.close()
        if len(markdown_text) < MIN_USABLE_CHARS:
            raise GeminiResponseError(
                'The AI returned too little text to make a blog post. '
                'Try again, or give the prompt more detail.'
            )
        return self._package(markdown_text, splitter, started)

    @staticmethod
    def _package(markdown_text, splitter, started, partial=False):
        return {
            "markdown": markdown_text,
            # Kept for callers that read `html` straight off this agent. The
            # real HTML comes from the formatting agent downstream.
            "html": "<article>{}</article>".format(
                markdown_text.replace("\n", "<br>")
            ),
            "plan": list(splitter.plan_lines) if splitter else [],
            "streamed": splitter is not None,
            "partial": partial,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        }
