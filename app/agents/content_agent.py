import time
import inspect
import google.generativeai as genai
from app.core.logging import get_logger
from app.services.gemini_client import gemini

logger = get_logger(__name__)

# Raise the client deadline well above the default 60s gRPC timeout.
# A ~1200-word generation routinely takes longer than 60s and was
# surfacing as "504 Deadline Exceeded".
GENERATE_TIMEOUT_SECONDS = 180

# `request_options` is only accepted by google-generativeai >= 0.4.
# Older builds forward unknown kwargs into the request proto and raise
# "Unknown field for GenerateContentRequest: request_options", so we
# feature-detect before passing it.
_SUPPORTS_REQUEST_OPTIONS = (
    'request_options'
    in inspect.signature(genai.GenerativeModel.generate_content).parameters
)


class ContentAgent:
    def __init__(self):
        # Using the stable 2026 identifier for speed and quality
        self.model = gemini.get_model()

    def generate_blog(self, topic):
        """
        Generate a complete, structured blog post directly from a topic in a
        single model call. This avoids a separate outline round-trip, roughly
        halving end-to-end generation latency.
        """
        prompt = (
            "You are a senior human content writer and subject-matter expert with "
            "10+ years of hands-on experience in this field. You write for a "
            "publication that Google and AI search engines (Google AI Overviews, "
            "ChatGPT, Perplexity) trust and cite. Write a complete, original, "
            "deeply useful blog post (900-1100 words) on the topic below.\n\n"

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

        response = self._generate_with_retry(prompt)

        return {
            "markdown": response.text,
            "html": "<article>{}</article>".format(
                response.text.replace("\n", "<br>")
            )
        }

    def _generate_with_retry(self, prompt):
        """Call the model with an extended deadline and one retry on timeout."""
        kwargs = {}
        if _SUPPORTS_REQUEST_OPTIONS:
            kwargs["request_options"] = {"timeout": GENERATE_TIMEOUT_SECONDS}

        last_error = None
        for attempt in range(2):
            try:
                return self.model.generate_content(prompt, **kwargs)
            except Exception as e:
                last_error = e
                error_str = str(e).lower()
                is_timeout = (
                    'deadline' in error_str
                    or 'timeout' in error_str
                    or '504' in error_str
                )
                if attempt == 0 and is_timeout:
                    logger.warning("Content generation timed out, retrying once...")
                    time.sleep(2)
                    continue
                raise
        raise last_error
