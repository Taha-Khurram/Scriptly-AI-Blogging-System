import json
import google.generativeai as genai
from config import Config


class NewsletterAgent:
    """
    AI-powered newsletter content generator.
    Uses Gemini to create engaging newsletter content from published blogs.
    """

    def __init__(self):
        genai.configure(api_key=Config.GEMINI_API_KEY)
        self.model = genai.GenerativeModel('gemini-flash-lite-latest')

    def generate_newsletter(self, blogs: list, site_name: str = "My Blog",
                            custom_intro: str = None, topic: str = None):
        """
        Generate newsletter content from recent published blogs.

        Args:
            blogs: List of blog dicts with 'title', 'content', 'id', 'category'
            site_name: Name of the blog/site
            custom_intro: Optional custom introduction text
            topic: Optional topic/theme for the newsletter

        Returns:
            Dict with subject, intro, posts summaries, and CTA
        """
        if not blogs:
            return {
                "success": False,
                "error": "No blogs provided for newsletter"
            }

        # Prepare blog summaries for the prompt
        blog_data = []
        for blog in blogs[:5]:  # Limit to 5 most recent
            content = blog.get('content', {})
            if isinstance(content, dict):
                body = content.get('body', content.get('html', ''))
            else:
                body = str(content)

            # Clean HTML and limit length
            clean_body = self._strip_html(body)[:500]

            blog_data.append({
                "title": blog.get('title', 'Untitled'),
                "excerpt": clean_body,
                "category": blog.get('category', 'General'),
                "id": blog.get('id', '')
            })

        prompt = self._build_prompt(blog_data, site_name, custom_intro, topic)

        try:
            response = self.model.generate_content(prompt)
            result = self._parse_response(response.text)
            result["success"] = True
            return result
        except Exception as e:
            print(f"Newsletter generation error: {e}")
            return {
                "success": False,
                "error": str(e),
                "fallback": self._generate_fallback(blog_data, site_name)
            }

    def _build_prompt(self, blog_data: list, site_name: str,
                      custom_intro: str = None, topic: str = None):
        """Build the AI prompt for newsletter generation."""

        topic_instruction = ""
        if topic:
            topic_instruction = f"Focus the newsletter around the theme: {topic}"

        custom_intro_instruction = ""
        if custom_intro:
            custom_intro_instruction = f"Use this as the introduction: {custom_intro}"

        blogs_json = json.dumps(blog_data, indent=2)

        return f"""
You are a professional newsletter writer for "{site_name}".
Create an engaging newsletter digest based on these recent blog posts:

{blogs_json}

{topic_instruction}
{custom_intro_instruction}

Requirements:
1. Write a catchy, attention-grabbing subject line (max 60 characters)
2. Write a warm, engaging introduction (2-3 sentences)
3. For each blog post, write a compelling summary (2-3 sentences) that makes readers want to click
4. End with a clear call-to-action

Respond ONLY with valid JSON in this exact format:
{{
    "subject": "Your catchy subject line here",
    "intro": "Your engaging introduction paragraph here",
    "posts": [
        {{
            "title": "Original post title",
            "summary": "Your compelling 2-3 sentence summary",
            "id": "original_id"
        }}
    ],
    "cta_text": "Read More on Our Blog",
    "closing": "Brief friendly closing message"
}}
"""

    def _parse_response(self, text: str):
        """Parse the AI response to extract JSON."""
        try:
            # Find JSON in response
            start = text.find('{')
            end = text.rfind('}') + 1

            if start == -1 or end == 0:
                raise ValueError("No JSON found in response")

            json_str = text[start:end]
            return json.loads(json_str)

        except json.JSONDecodeError as e:
            print(f"JSON parse error: {e}")
            # Try to extract key fields manually
            return {
                "subject": "Weekly Newsletter Update",
                "intro": text[:300] if text else "Check out our latest posts!",
                "posts": [],
                "cta_text": "Visit Our Blog",
                "closing": "Thanks for reading!"
            }

    def _generate_fallback(self, blog_data: list, site_name: str):
        """Generate fallback content if AI fails."""
        posts = []
        for blog in blog_data:
            posts.append({
                "title": blog.get("title", "Untitled"),
                "summary": blog.get("excerpt", "")[:150] + "...",
                "id": blog.get("id", "")
            })

        return {
            "subject": f"Latest from {site_name}",
            "intro": f"Here's what's new on {site_name}! Check out our latest articles below.",
            "posts": posts,
            "cta_text": "Read More",
            "closing": "Thanks for being a subscriber!"
        }

    def _strip_html(self, html: str):
        """Remove HTML tags from content."""
        import re
        clean = re.sub(r'<[^>]+>', '', html)
        clean = re.sub(r'\s+', ' ', clean).strip()
        return clean

    def generate_subject_variations(self, main_subject: str, count: int = 3):
        """Generate alternative subject line variations."""
        prompt = f"""
Generate {count} alternative email subject lines based on this original:
"{main_subject}"

Requirements:
- Each under 60 characters
- Engaging and clickable
- Different styles (question, statement, curiosity gap)

Respond with JSON array only:
["Subject 1", "Subject 2", "Subject 3"]
"""
        try:
            response = self.model.generate_content(prompt)
            text = response.text
            start = text.find('[')
            end = text.rfind(']') + 1
            return json.loads(text[start:end])
        except Exception as e:
            print(f"Subject variation error: {e}")
            return [main_subject]

    def improve_content(self, content: str, instruction: str = "Make it more engaging"):
        """Improve newsletter content based on instruction."""
        prompt = f"""
Improve this newsletter content:
---
{content}
---

Instruction: {instruction}

Return only the improved content, no explanations.
"""
        try:
            response = self.model.generate_content(prompt)
            return {"success": True, "content": response.text.strip()}
        except Exception as e:
            return {"success": False, "error": str(e)}
