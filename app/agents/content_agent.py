import time
import inspect
import google.generativeai as genai
from flask import current_app

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
        genai.configure(api_key=current_app.config['GEMINI_API_KEY'])
        # Using the stable 2026 identifier for speed and quality
        self.model = genai.GenerativeModel('gemini-3-flash-preview')

    def generate_blog(self, topic):
        """
        Generate a complete, structured blog post directly from a topic in a
        single model call. This avoids a separate outline round-trip, roughly
        halving end-to-end generation latency.
        """
        prompt = (
            "You are an expert copywriter and SEO strategist. Write a complete, "
            "engaging, well-structured blog post (approx 1000 words) on the topic below.\n\n"
            "Requirements:\n"
            "- Open with a short, compelling introduction (do NOT add an H1 '# ' title).\n"
            "- Organize the body with 4-6 clear '## ' section headings.\n"
            "- Use bold for emphasis and bullet points where they aid readability.\n"
            "- Finish with a concise conclusion.\n"
            "- Return ONLY the blog post in Markdown. No preamble, no code fences.\n\n"
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
                    print("⚠️ Content generation timed out, retrying once...")
                    time.sleep(2)
                    continue
                raise
        raise last_error
