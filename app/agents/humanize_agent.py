import re
import random
import inspect
from google import generativeai as genai
from flask import current_app

from app.utils.parallel import run_parallel_simple


# Raise the client deadline well above the default ~60s gRPC timeout.
# Humanization rewrites each chunk in one model call; a slow call was
# surfacing as "504 Deadline Exceeded" on the first attempt.
HUMANIZE_TIMEOUT_SECONDS = 120

# Transient 504/503s from the model backend are common under load. Retry a
# few times with exponential backoff so a slow first try recovers instead of
# silently falling back to the un-humanized original.
HUMANIZE_MAX_RETRIES = 3

# `request_options` is only accepted by google-generativeai >= 0.4. Older
# builds forward unknown kwargs into the request proto and raise, so we
# feature-detect before passing it (mirrors ContentAgent).
_SUPPORTS_REQUEST_OPTIONS = (
    'request_options'
    in inspect.signature(genai.GenerativeModel.generate_content).parameters
)


# ── AI word → human word replacement map ──────────────────────────
# These are the exact tokens AI detectors flag as high-probability AI output.
# Replacing them with lower-probability synonyms directly raises perplexity.

AI_WORD_MAP = {
    r'\butilize\b': 'use',
    r'\butilizing\b': 'using',
    r'\butilized\b': 'used',
    r'\bleverage\b': 'use',
    r'\bleveraging\b': 'using',
    r'\bleveraged\b': 'used',
    r'\bstreamline\b': 'simplify',
    r'\bstreamlined\b': 'simplified',
    r'\bstreamlining\b': 'simplifying',
    r'\brobust\b': 'solid',
    r'\bcutting-edge\b': 'latest',
    r'\bgroundbreaking\b': 'new',
    r'\bdelve\b': 'dig into',
    r'\bdelving\b': 'digging into',
    r'\bdelved\b': 'dug into',
    r'\bcrucial\b': 'key',
    r'\bpivotal\b': 'important',
    r'\bfacilitate\b': 'help with',
    r'\bfacilitating\b': 'helping with',
    r'\bcomprehensive\b': 'thorough',
    r'\bimplement\b': 'set up',
    r'\bimplementing\b': 'setting up',
    r'\bimplemented\b': 'set up',
    r'\bimplementation\b': 'setup',
    r'\boptimize\b': 'improve',
    r'\boptimizing\b': 'improving',
    r'\boptimized\b': 'improved',
    r'\bparadigm\b': 'approach',
    r'\bfoster\b': 'build',
    r'\bfostering\b': 'building',
    r'\bharness\b': 'use',
    r'\bharnessing\b': 'using',
    r'\bempower\b': 'help',
    r'\bempowering\b': 'helping',
    r'\bseamless\b': 'smooth',
    r'\bseamlessly\b': 'smoothly',
    r'\bplethora\b': 'plenty of',
    r'\bmyriad\b': 'many',
    r'\bencompass\b': 'cover',
    r'\bencompassing\b': 'covering',
    r'\bencompasses\b': 'covers',
    r'\bmultifaceted\b': 'complex',
    r'\btapestry\b': 'mix',
    r'\bunderscores\b': 'shows',
    r'\bunderscore\b': 'show',
    r'\bbolster\b': 'strengthen',
    r'\bbolstering\b': 'strengthening',
    r'\bFurthermore\b': 'Also',
    r'\bMoreover\b': 'Plus',
    r'\bAdditionally\b': 'On top of that',
    r'\bIn conclusion\b': 'So overall',
    r'\bIt is important to note\b': 'Worth noting',
    r'\bIt\'s important to note\b': 'Worth noting',
    r'\bIn today\'s\b': "In today's",
    r'\bIn the realm of\b': 'In the world of',
    r'\bthe realm of\b': 'the world of',
    r'\bthe landscape of\b': 'the space of',
    r'\bnavigate the\b': 'deal with the',
    r'\bnavigating the\b': 'dealing with the',
}

# ── Shared preamble for all prompt variants ───────────────────────
# E-E-A-T, information gain, and anti-AI rules baked into every call.

_SHARED_RULES = """You are an expert Editor and Content Strategist. Transform this AI draft into human-first content that meets Google's E-E-A-T standards.

CORE RULES (apply to EVERY rewrite):
- LENGTH (most important): Follow the TARGET LENGTH given at the end of this prompt. If the original is shorter than the target, EXPAND it with specific, relevant detail — real examples, hypothetical scenarios, insider observations, deeper explanation — until you reach the target. If it is longer, tighten it. Never pad with empty filler. Preserve EVERY fact, example, and nuance from the original — do NOT summarize, condense, or drop any point.
- PERPLEXITY & BURSTINESS: Vary sentence length wildly. Mix short punchy lines (3-8 words) with medium explanatory ones (10-16 words). Never 3+ sentences of similar length in a row.
- INFORMATION GAIN: Don't just rephrase. Add a specific example, a hypothetical scenario, or a nuanced "insider" observation where it fits naturally. Make the reader learn something extra.
- REMOVE AI-ISMS: Eliminate "In conclusion," "It is important to note," "Furthermore," "In the rapidly evolving world of," "It's worth mentioning," and all similar filler.
- GROUNDED TONE: Conversational yet authoritative. Use "we", "you", or "I" naturally to build connection. Active voice always — never passive.
- CONTRACTIONS ALWAYS: don't, it's, can't, won't, they're, we're.
- KEEP all markdown formatting (headings, bold, italic, lists, links, code blocks).
- KEEP all facts exactly the same. Only change how they're expressed.
- Transitions must feel logical and earned, not mechanical pattern-following.
- Return ONLY the rewritten section. No commentary. No code fences. No preamble."""

# ── Prompt variants (rotated per chunk) ───────────────────────────
# Each adds a unique style layer on top of the shared rules.

PROMPT_VARIANTS = [
    # Variant 0: Direct simplicity
    _SHARED_RULES + """

STYLE FOR THIS SECTION:
- Average sentence: 8-15 words. Some can be 3-5 words. None over 20.
- Every sentence in a paragraph must start with a different word.
- Plain vocabulary. Write like a real person, not a textbook.

Rewrite this blog section about {topic}:
{section}""",

    # Variant 1: Conversational
    _SHARED_RULES + """

STYLE FOR THIS SECTION:
- Explain it like you're talking to a smart friend.
- Paragraphs: 1-4 sentences max.
- Throw in 1-2 natural questions like "Right?" or "Make sense?"
- Simple words only. If a 12-year-old wouldn't say it, don't write it.

Rewrite this blog section about {topic}:
{section}""",

    # Variant 2: Punchy
    _SHARED_RULES + """

STYLE FOR THIS SECTION:
- Short and punchy. Fragments are fine. "Works great." is a valid sentence.
- 1-2 rhetorical questions per paragraph.
- Simplest word possible every time.
- Sentence length: some 3 words, some 15. Never uniform.

Rewrite this blog section about {topic}:
{section}""",

    # Variant 3: Relaxed explainer
    _SHARED_RULES + """

STYLE FOR THIS SECTION:
- Alternate short sentences (5-8 words) with medium ones (10-16 words).
- Start some sentences with "And", "But", "So".
- Sentence fragments occasionally for emphasis.
- No filler phrases whatsoever.

Rewrite this blog section about {topic}:
{section}""",
]

# ── Contraction expansion pairs ───────────────────────────────────
CONTRACTION_PAIRS = [
    (r"\bdon't\b", "do not"),
    (r"\bcan't\b", "cannot"),
    (r"\bwon't\b", "will not"),
    (r"\bit's\b", "it is"),
    (r"\bthey're\b", "they are"),
    (r"\bdoesn't\b", "does not"),
    (r"\bwouldn't\b", "would not"),
]

# ── Filler words and parenthetical asides ─────────────────────────
FILLER_STARTERS = ["Honestly, ", "Look, ", "So, ", "I mean, ", "Basically, "]
PARENTHETICAL_ASIDES = [
    " (at least from what I've seen)",
    " (which is kind of wild)",
    " (your mileage may vary)",
    " (not always, but often)",
    " (just my take)",
    " (seriously)",
]


class HumanizeAgent:
    """
    Section-based content humanization agent.
    Rewrites AI-generated content to bypass AI detection tools
    using rotating prompt variants + 5-pass deterministic post-processing.
    """

    def __init__(self):
        genai.configure(api_key=current_app.config['GEMINI_API_KEY'])
        self.model = genai.GenerativeModel('gemini-flash-lite-latest')
        # gemini-flash-lite-latest is a THINKING model: its internal reasoning tokens
        # are drawn from max_output_tokens BEFORE the visible answer. Measured
        # ~2,500-3,500 thinking tokens per chunk, so a low cap (the old 3072)
        # gets fully consumed by thinking and truncates the rewrite to a
        # handful of words (finish_reason=MAX_TOKENS). This SDK (0.8.6) predates
        # ThinkingConfig, so thinking can't be disabled — instead give a ceiling
        # generous enough for thinking + a full-length rewrite of a half-blog
        # chunk. 8192 completes (finish=STOP) on chunks past 1,100 words with
        # headroom; going higher just makes the model think more, not better.
        self.generation_config = genai.types.GenerationConfig(
            temperature=0.9,
            top_p=0.88,
            max_output_tokens=8192,
        )

    def humanize_content(self, markdown, topic="", target_words=1200):
        """
        Main entry point. Humanize AI-generated markdown content.
        Uses max 2 API calls (blog split in half) + heavy post-processing.

        Args:
            markdown: Raw AI-generated markdown text
            topic: Blog topic for context-aware rewriting
            target_words: Desired length of the humanized blog (default 1200).
                The target is split proportionally across chunks so the final
                rewrite lands close to this word count.

        Returns:
            dict with 'markdown', 'original_markdown', 'humanization_applied'
        """
        original_markdown = markdown
        orig_words = len(markdown.split())
        print(f"\n{'='*60}")
        print(f"🔄 HumanizeAgent — Starting humanization")
        print(f"   Topic: {topic or '(none)'}")
        print(f"   Input: {orig_words} words")
        print(f"{'='*60}")

        try:
            # Step 1: Split blog into 2 halves at a ## boundary
            chunks = self._split_into_halves(markdown)
            print(f"\n📂 Step 1: Split into {len(chunks)} chunk(s)")
            for i, chunk in enumerate(chunks):
                chunk_words = len(chunk.split())
                chunk_headings = len(re.findall(r'^#{1,3}\s', chunk, re.MULTILINE))
                print(f"   Chunk {i+1}: {chunk_words} words, {chunk_headings} headings")

            # Step 2: Rewrite each half with a different prompt variant.
            # Chunks are independent, so run them concurrently — this halves
            # wall-clock time versus the old sequential loop.
            # Split the overall target proportionally to each chunk's size so
            # the reassembled blog lands near `target_words`.
            total_chunk_words = sum(len(c.split()) for c in chunks) or 1
            chunk_targets = [
                max(1, round(target_words * len(c.split()) / total_chunk_words))
                for c in chunks
            ]

            print(f"\n🤖 Step 2: Rewriting {len(chunks)} chunk(s) via Gemini API (parallel)")
            print(f"   Target: {target_words} words total → per-chunk {chunk_targets}")
            if len(chunks) == 1:
                rewritten = [self._rewrite_chunk(chunks[0], topic, 0, chunk_targets[0])]
            else:
                tasks = [
                    (self._rewrite_chunk, (chunk, topic, i, chunk_targets[i]))
                    for i, chunk in enumerate(chunks)
                ]
                rewritten = run_parallel_simple(tasks, max_workers=len(tasks))
                # A failed parallel task returns None — fall back to the
                # original chunk so we never drop content.
                rewritten = [
                    r if r is not None else chunks[i]
                    for i, r in enumerate(rewritten)
                ]

            # Step 3: Reassemble
            humanized = '\n\n'.join(c.strip() for c in rewritten if c.strip())
            reassembled_words = len(humanized.split())
            print(f"\n🔗 Step 3: Reassembled — {reassembled_words} words")

            # Step 4: 5-pass deterministic post-processing
            print(f"\n⚙️  Step 4: Post-processing (5 passes)")
            humanized = self._post_process(humanized)
            post_words = len(humanized.split())
            print(f"   Post-processing complete — {post_words} words")

            # Step 5: Validate structure preserved
            print(f"\n✅ Step 5: Validating structure")
            humanized = self._validate(original_markdown, humanized)
            final_words = len(humanized.split())
            ratio = final_words / orig_words if orig_words > 0 else 1.0

            print(f"\n{'='*60}")
            print(f"✅ Humanization complete!")
            print(f"   {orig_words} → {final_words} words (ratio: {ratio:.2f})")
            print(f"{'='*60}\n")

            return {
                "markdown": humanized,
                "original_markdown": original_markdown,
                "humanization_applied": True
            }

        except Exception as e:
            print(f"\n❌ HumanizeAgent Error: {e}")
            return {
                "markdown": original_markdown,
                "original_markdown": original_markdown,
                "humanization_applied": False
            }

    # ── Splitting & rewriting ─────────────────────────────────────

    def _split_into_halves(self, markdown):
        """Split markdown into 2 roughly equal halves at a ## heading boundary."""
        sections = re.split(r'(?=^## )', markdown, flags=re.MULTILINE)
        sections = [s for s in sections if s.strip()]
        print(f"   Found {len(sections)} sections (## headings)")

        if len(sections) <= 2:
            print(f"   → Too few sections, using 1 chunk (single API call)")
            return [markdown]

        mid = len(sections) // 2
        first_half = '\n\n'.join(sections[:mid])
        second_half = '\n\n'.join(sections[mid:])
        print(f"   → Split at section {mid}: chunk 1 = sections 1-{mid}, chunk 2 = sections {mid+1}-{len(sections)}")
        return [first_half, second_half]

    def _rewrite_chunk(self, chunk, topic, index, target_words=None):
        """Rewrite a chunk using a prompt variant. Streams the response and
        retries transient backend errors with exponential backoff.

        `target_words` is the desired length of this chunk's rewrite; the model
        is told to expand or tighten toward it.
        """
        import time

        chunk_words = len(chunk.split())
        variant_num = index % len(PROMPT_VARIANTS)

        if chunk_words < 30:
            print(f"   Chunk {index+1}: ⏭️  Skipped (only {chunk_words} words)")
            return chunk

        print(f"   Chunk {index+1}: 🔄 Rewriting with variant {variant_num} ({chunk_words} words → target {target_words})...", end=" ", flush=True)

        variant = PROMPT_VARIANTS[variant_num]
        prompt = variant.format(topic=topic or "this topic", section=chunk)
        if target_words:
            prompt += (
                f"\n\nTARGET LENGTH: Aim for approximately {target_words} words in "
                f"this rewritten section. If the source above is shorter, expand it "
                f"with specific, relevant detail and examples to reach that length; "
                f"if longer, tighten it. Keep every fact and point intact."
            )

        kwargs = {"generation_config": self.generation_config}
        if _SUPPORTS_REQUEST_OPTIONS:
            kwargs["request_options"] = {"timeout": HUMANIZE_TIMEOUT_SECONDS}

        for attempt in range(HUMANIZE_MAX_RETRIES):
            try:
                # Stream the response. Non-streaming generate_content must wait
                # for the WHOLE generation to finish before returning, so a slow
                # preview-model call trips the backend's single-operation
                # deadline and 504s on the first try. Streaming returns tokens
                # incrementally, so the request completes without ever hitting
                # that all-or-nothing deadline.
                raw = self._stream_text(prompt, kwargs)
                result = self._clean_response(raw)
                result_words = len(result.split())

                orig_headings = re.findall(r'^#{1,3}\s', chunk, re.MULTILINE)
                new_headings = re.findall(r'^#{1,3}\s', result, re.MULTILINE)
                if len(orig_headings) > 0 and len(new_headings) == 0:
                    print(f"⚠️ Lost all headings, keeping original")
                    return chunk

                # Truncation guard: if the rewrite came back far shorter than
                # what we asked for, the model almost certainly ran out of
                # output budget mid-generation (finish_reason=MAX_TOKENS). Keep
                # THIS chunk's original only in that broken case so we never
                # save a half-cut section. Compare against the target (falling
                # back to the source length when no target is set).
                floor = (target_words or chunk_words) * 0.4
                if result_words < floor:
                    print(f"⚠️ Rewrite truncated (target {target_words or chunk_words} → {result_words} words), keeping original")
                    return chunk

                print(f"✅ Done ({chunk_words} → {result_words} words)")
                return result

            except Exception as e:
                error_str = str(e).lower()
                is_transient = (
                    'deadline' in error_str
                    or 'timeout' in error_str
                    or '504' in error_str
                    or '503' in error_str
                    or 'unavailable' in error_str
                )
                if attempt < HUMANIZE_MAX_RETRIES - 1 and is_transient:
                    backoff = 2 * (attempt + 1)  # 2s, 4s
                    print(
                        f"⚠️ Transient error, retrying "
                        f"({attempt + 1}/{HUMANIZE_MAX_RETRIES - 1}) in {backoff}s...",
                        end=" ", flush=True,
                    )
                    time.sleep(backoff)
                    continue
                print(f"❌ Failed: {e}")
                return chunk

    def _stream_text(self, prompt, kwargs):
        """
        Generate with stream=True and accumulate the text.

        Streaming sidesteps the "504 Deadline expired before operation could
        complete" error: the backend emits tokens as they're produced instead
        of holding the whole response until a single completion deadline.
        """
        response = self.model.generate_content(prompt, stream=True, **kwargs)
        parts = []
        for event in response:
            # A streamed event may carry no text (e.g. a safety/finish-only
            # chunk); guard so one empty part can't abort the whole stream.
            try:
                if event.text:
                    parts.append(event.text)
            except (ValueError, AttributeError):
                continue
        return "".join(parts)

    def _clean_response(self, text):
        """Strip code fences and preamble from LLM output."""
        text = text.strip()
        text = re.sub(r'^```(?:markdown)?\s*\n?', '', text)
        text = re.sub(r'\n?```\s*$', '', text)
        # Remove preamble before first heading if present
        if re.search(r'^#', text, re.MULTILINE):
            stripped = re.sub(r'^.*?(?=^#)', '', text, count=1, flags=re.DOTALL | re.MULTILINE)
            if stripped.strip():
                text = stripped
        return text.strip()

    # ── Post-processing (5 passes) ────────────────────────────────

    def _post_process(self, text):
        """
        5-pass deterministic post-processing to disrupt AI detection patterns.
        Each pass targets a specific signal that detectors measure.
        """
        before = len(text.split())

        text = self._replace_ai_words(text)
        print(f"   Pass 1/5: AI word replacement — {len(text.split())} words")

        text = self._split_long_sentences(text)
        print(f"   Pass 2/5: Long sentence splitting — {len(text.split())} words")

        text = self._mix_contractions(text)
        print(f"   Pass 3/5: Contraction mixing — {len(text.split())} words")

        text = self._vary_paragraph_lengths(text)
        print(f"   Pass 4/5: Paragraph length variation — {len(text.split())} words")

        text = self._inject_imperfections(text)
        print(f"   Pass 5/5: Imperfection injection — {len(text.split())} words")

        return text

    def _replace_ai_words(self, text):
        """Pass 1: Replace high-probability AI words with human alternatives."""
        for pattern, replacement in AI_WORD_MAP.items():
            # Preserve sentence-start capitalization
            def _replace_match(m):
                original = m.group(0)
                if original[0].isupper() and replacement[0].islower():
                    return replacement[0].upper() + replacement[1:]
                return replacement
            text = re.sub(pattern, _replace_match, text, flags=re.IGNORECASE)
        return text

    def _split_long_sentences(self, text):
        """Pass 2: Break sentences over 20 words to increase burstiness."""
        lines = text.split('\n')
        result = []

        in_code_block = False
        for line in lines:
            if line.strip().startswith('```'):
                in_code_block = not in_code_block
                result.append(line)
                continue

            # Skip headings, code blocks, list items
            if (in_code_block
                    or re.match(r'^#{1,6}\s', line)
                    or re.match(r'^\s*[-*]\s', line)
                    or re.match(r'^\s*\d+\.\s', line)
                    or not line.strip()):
                result.append(line)
                continue

            # Split line into sentences and process each
            sentences = re.split(r'(?<=[.!?])\s+', line)
            new_sentences = []

            for sentence in sentences:
                words = sentence.split()
                if len(words) > 20:
                    split_sent = self._try_split_sentence(sentence)
                    new_sentences.extend(split_sent)
                else:
                    new_sentences.append(sentence)

            result.append(' '.join(new_sentences))

        return '\n'.join(result)

    def _try_split_sentence(self, sentence):
        """Try to split a long sentence at a conjunction or comma near the middle."""
        words = sentence.split()
        mid = len(words) // 2
        search_range = range(max(mid - 3, 2), min(mid + 4, len(words) - 1))

        split_words = {'and', 'but', 'which', 'that', 'because', 'while', 'so', 'yet', 'although', 'however'}

        # Try to find a conjunction near the middle
        for i in search_range:
            if words[i].lower().rstrip(',') in split_words:
                first_half = ' '.join(words[:i])
                second_half = ' '.join(words[i:])

                # Clean up first half — add period if needed
                if not first_half.endswith(('.', '!', '?')):
                    first_half = first_half.rstrip(',') + '.'

                # Capitalize second half
                if second_half and second_half[0].islower():
                    second_half = second_half[0].upper() + second_half[1:]

                return [first_half, second_half]

        # Try comma near the middle
        for i in search_range:
            if words[i].endswith(','):
                first_half = ' '.join(words[:i + 1]).rstrip(',') + '.'
                second_half = ' '.join(words[i + 1:])
                if second_half and second_half[0].islower():
                    second_half = second_half[0].upper() + second_half[1:]
                return [first_half, second_half]

        # No good split point — return as-is
        return [sentence]

    def _mix_contractions(self, text):
        """Pass 3: Expand one contraction in ~15% of paragraphs for inconsistency."""
        paragraphs = text.split('\n\n')
        result = []

        for para in paragraphs:
            # Skip headings and code
            if para.strip().startswith('#') or para.strip().startswith('```'):
                result.append(para)
                continue

            if random.random() < 0.15:
                pair = random.choice(CONTRACTION_PAIRS)
                para = re.sub(pair[0], pair[1], para, count=1)

            result.append(para)

        return '\n\n'.join(result)

    def _vary_paragraph_lengths(self, text):
        """Pass 4: Merge consecutive short paragraphs or split long ones."""
        paragraphs = text.split('\n\n')
        result = []
        i = 0

        while i < len(paragraphs):
            para = paragraphs[i]

            # Skip headings, code blocks, lists
            if (para.strip().startswith('#')
                    or para.strip().startswith('```')
                    or re.match(r'^\s*[-*]\s', para.strip())
                    or re.match(r'^\s*\d+\.\s', para.strip())):
                result.append(para)
                i += 1
                continue

            words = para.split()

            # Merge two consecutive short paragraphs (both under 30 words)
            if (len(words) < 30
                    and i + 1 < len(paragraphs)
                    and not paragraphs[i + 1].strip().startswith('#')
                    and not paragraphs[i + 1].strip().startswith('```')
                    and len(paragraphs[i + 1].split()) < 30
                    and random.random() < 0.30):
                merged = para.rstrip() + ' ' + paragraphs[i + 1].lstrip()
                result.append(merged)
                i += 2
                continue

            # Split long paragraphs (over 80 words with 4+ sentences)
            if len(words) > 80 and random.random() < 0.40:
                sentences = re.split(r'(?<=[.!?])\s+', para)
                if len(sentences) >= 4:
                    mid = len(sentences) // 2
                    first_half = ' '.join(sentences[:mid])
                    second_half = ' '.join(sentences[mid:])
                    result.append(first_half)
                    result.append(second_half)
                    i += 1
                    continue

            result.append(para)
            i += 1

        return '\n\n'.join(result)

    def _inject_imperfections(self, text):
        """Pass 5: Add filler words and parenthetical asides."""
        lines = text.split('\n')
        result = []

        in_code_block = False
        for line in lines:
            if line.strip().startswith('```'):
                in_code_block = not in_code_block
                result.append(line)
                continue

            # Skip headings, code, lists, empty lines
            if (in_code_block
                    or re.match(r'^#{1,6}\s', line)
                    or re.match(r'^\s*[-*]\s', line)
                    or re.match(r'^\s*\d+\.\s', line)
                    or not line.strip()):
                result.append(line)
                continue

            # 5% chance: prepend a filler word
            if random.random() < 0.05 and len(line) > 20:
                filler = random.choice(FILLER_STARTERS)
                # Lowercase the original first character
                if line[0].isupper():
                    line = filler + line[0].lower() + line[1:]
                else:
                    line = filler + line

            # 4% chance: add parenthetical aside before last period
            if random.random() < 0.04 and line.rstrip().endswith('.') and len(line) > 30:
                aside = random.choice(PARENTHETICAL_ASIDES)
                line = line.rstrip()
                line = line[:-1] + aside + '.'

            result.append(line)

        return '\n'.join(result)

    # ── Validation ────────────────────────────────────────────────

    def _validate(self, original, humanized):
        """Validate the humanized output.

        We intentionally do NOT revert to the AI original on length changes —
        the humanizer targets a fixed word count (~1200), so the length is
        expected to move. The only case that falls back is a genuinely empty
        rewrite, where there is simply nothing to save.
        """
        if not humanized or not humanized.strip():
            print("⚠️ Humanization returned empty — using original")
            return original

        return humanized
