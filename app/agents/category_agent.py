import google.generativeai as genai
import os
from app.firebase.firestore_service import FirestoreService

class CategoryAgent:
    def __init__(self):
        self.db_service = FirestoreService()
        genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
        self.model = genai.GenerativeModel('gemini-flash-lite-latest')

    def categorize_blog(self, title, content_body, categories=None):
        """
        Analyzes context and returns a single category name.
        If 'categories' is provided, it uses them instead of fetching all categories from Firestore.
        """
        # 1. Fetch current categories if none provided
        if categories is None:
            existing_cats = [cat['name'] for cat in self.db_service.get_all_categories(limit=50)]
        else:
            existing_cats = [cat['name'] for cat in categories]

        prompt = f"""
        Role: Senior Content Taxonomist.
        Task: Categorize the following blog post.
        
        Existing Categories: {', '.join(existing_cats) if existing_cats else 'None'}

        Blog Title: {title}
        Blog Content: {content_body[:1500]}

        Instructions:
        1. If a category in 'Existing Categories' fits perfectly, use it.
        2. If none fit, create a new, professional 1-2 word category.
        3. Return ONLY the category name. No quotes, no explanation.
        """

        try:
            response = self.model.generate_content(prompt)
            return response.text.strip()
        except Exception as e:
            print(f"❌ CategoryAgent Error: {e}")
            return "General"