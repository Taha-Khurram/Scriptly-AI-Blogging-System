import google.generativeai as genai
from flask import current_app
import re
from app.core.logging import get_logger

logger = get_logger(__name__)


class EmbeddingService:
    """Service for generating text embeddings using Google's embedding model."""

    def __init__(self):
        genai.configure(api_key=current_app.config['GEMINI_API_KEY'])
        self.model = "models/gemini-embedding-001"

    def _clean_text(self, text):
        """Clean and normalize text for embedding."""
        if not text:
            return ""
        # Remove HTML tags
        text = re.sub(r'<[^>]+>', '', str(text))
        # Remove extra whitespace
        text = ' '.join(text.split())
        # Limit length to avoid token limits
        return text[:8000]

    def generate_embedding(self, text):
        """
        Generate embedding vector for a text string.
        Returns a list of floats (768 dimensions).
        """
        try:
            cleaned_text = self._clean_text(text)
            if not cleaned_text:
                return None

            result = genai.embed_content(
                model=self.model,
                content=cleaned_text,
                task_type="retrieval_document"
            )
            return result['embedding']
        except Exception:
            logger.exception("Error generating embedding")
            return None

    def generate_query_embedding(self, query):
        """
        Generate embedding for a search query.
        Uses retrieval_query task type for better search results.
        """
        try:
            cleaned_query = self._clean_text(query)
            if not cleaned_query:
                return None

            result = genai.embed_content(
                model=self.model,
                content=cleaned_query,
                task_type="retrieval_query"
            )
            return result['embedding']
        except Exception:
            logger.exception("Error generating query embedding")
            return None

    def generate_blog_embedding(self, blog):
        """
        Generate embedding for a blog post.
        Combines title, category, and content for richer representation.
        """
        title = blog.get('title', '')
        category = blog.get('category', '')

        # Extract content text
        content = blog.get('content', '')
        if isinstance(content, dict):
            content_text = content.get('body', '') or content.get('markdown', '') or content.get('text', '')
        else:
            content_text = str(content) if content else ''

        # Combine for embedding (title weighted more by repetition)
        combined_text = f"{title}. {title}. Category: {category}. {content_text}"

        return self.generate_embedding(combined_text)
