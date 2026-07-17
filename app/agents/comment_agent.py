import json
import re
from google import generativeai as genai
from flask import current_app


class CommentAgent:
    """
    AI comment moderation agent.
    Analyzes comments in a single API call and returns a moderation decision:
    approve (clean), edit (grammar/formatting fixes), or remove (spam/toxic).
    """

    def __init__(self):
        genai.configure(api_key=current_app.config['GEMINI_API_KEY'])
        self.model = genai.GenerativeModel('gemini-flash-lite-latest')
        self.generation_config = genai.types.GenerationConfig(
            temperature=0.3,
            top_p=0.9,
            max_output_tokens=1024,
        )

    def moderate_comment(self, comment_text, blog_title=""):
        """
        Moderate a single comment using one Gemini API call.

        Args:
            comment_text: The raw comment text submitted by the user
            blog_title: Title of the blog post (for context)

        Returns:
            dict with keys:
                action: "approve" | "edit" | "remove"
                moderated_text: cleaned text (same as original if approved)
                reason: explanation of the moderation decision
        """
        try:
            result = self._analyze(comment_text, blog_title)
            # Validate the result
            if result.get('action') not in ('approve', 'edit', 'remove'):
                result['action'] = 'approve'
            if 'moderated_text' not in result or not result['moderated_text']:
                result['moderated_text'] = comment_text
            if 'reason' not in result:
                result['reason'] = None
            return result

        except Exception as e:
            print(f"CommentAgent Error: {e}")
            # Fail-open: approve as-is if AI fails
            return {
                "action": "approve",
                "moderated_text": comment_text,
                "reason": "AI moderation unavailable - auto-approved"
            }

    def _analyze(self, comment_text, blog_title):
        """Send comment to Gemini for analysis and return parsed JSON result."""
        prompt = f"""You are an intelligent comment moderation system for a blog. Analyze the following comment and decide what action to take.

Blog post title: "{blog_title}"

Comment to analyze:
---
{comment_text}
---

Evaluate the comment for:
1. SPAM: Promotional links, advertisements, SEO spam, irrelevant product mentions, crypto/gambling promotion
2. TOXICITY: Hate speech, slurs, personal attacks, threats, harassment, extreme profanity
3. IRRELEVANCE: Completely unrelated to the blog topic, random gibberish, bot-generated text
4. QUALITY: Grammar issues, excessive caps, poor formatting, minor profanity that can be cleaned

Based on your analysis, choose ONE action:

- "approve" — Comment is clean, relevant, and well-written. Keep as-is.
- "edit" — Comment has value but needs cleanup (grammar fixes, formatting, mild profanity replacement, excessive caps normalization). Provide the cleaned version.
- "remove" — Comment is spam, toxic, hateful, or completely irrelevant. Must be hidden from the public site.

IMPORTANT RULES:
- Be lenient with opinions, even negative ones about the blog topic. Disagreement is NOT toxicity.
- Only "remove" for clear spam, hate speech, or genuinely harmful content.
- Prefer "edit" over "remove" when the comment has some value but needs cleanup.
- For "approve", set moderated_text to the exact original comment unchanged.
- For "edit", set moderated_text to the cleaned-up version.
- For "remove", set moderated_text to the original (kept for admin logs).

Respond with ONLY valid JSON, no other text:
{{"action": "approve|edit|remove", "moderated_text": "the text", "reason": "brief explanation"}}"""

        response = self.model.generate_content(
            prompt, generation_config=self.generation_config
        )

        return self._parse_response(response.text, comment_text)

    def _parse_response(self, response_text, original_text):
        """Parse Gemini's response, handling code fences and malformed JSON."""
        text = response_text.strip()

        # Strip markdown code fences
        text = re.sub(r'^```(?:json)?\s*\n?', '', text)
        text = re.sub(r'\n?```\s*$', '', text)
        text = text.strip()

        # Try direct JSON parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try extracting JSON object from surrounding text
        match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        # If all parsing fails, approve as-is
        print(f"CommentAgent: Failed to parse response: {text[:200]}")
        return {
            "action": "approve",
            "moderated_text": original_text,
            "reason": "AI response could not be parsed - auto-approved"
        }
