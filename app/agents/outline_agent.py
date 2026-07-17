# import google.generativeai as genai
from google import generativeai as genai
from flask import current_app
import json
import re

class OutlineAgent:
    def __init__(self):
        # Always configure inside the class or factory to ensure app context
        genai.configure(api_key=current_app.config['GEMINI_API_KEY'])
        self.model = genai.GenerativeModel('gemini-flash-lite-latest')

    def generate_outline(self, topic):
        prompt = f"Create a structured SEO blog outline for: {topic}. Return ONLY a JSON list of strings."
        response = self.model.generate_content(prompt)
        text = response.text.strip()

        # Strip markdown code fences if present
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)

        # Try parsing as JSON first
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list) and parsed:
                return [str(item).strip() for item in parsed if str(item).strip()]
        except (json.JSONDecodeError, TypeError):
            pass

        # Fallback: split by newlines
        return [line.strip() for line in text.split('\n') if line.strip()]