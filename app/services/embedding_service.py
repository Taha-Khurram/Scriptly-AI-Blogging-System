"""Text embeddings for semantic search over published posts."""
import re

from app.core.logging import get_logger
from app.services.gemini_client import GeminiError, gemini

logger = get_logger(__name__)

# Ceiling on the text sent for one embedding. The model has its own token
# limit; truncating here keeps a very long post from being rejected outright
# and makes the cost per call predictable.
MAX_EMBEDDING_CHARS = 8000


class EmbeddingService:
    """Generates embedding vectors through the shared Gemini client.

    Routed through that client rather than calling the SDK directly so
    embeddings get the same timeout and retry-with-jitter policy as every other
    model call -- a 429 here silently returned None before, which showed up
    later as a post that never appears in search results.
    """

    def _clean_text(self, text):
        """Clean and normalize text for embedding."""
        if not text:
            return ""
        # Remove HTML tags
        text = re.sub(r'<[^>]+>', '', str(text))
        # Remove extra whitespace
        text = ' '.join(text.split())
        # Limit length to avoid token limits
        return text[:MAX_EMBEDDING_CHARS]

    def generate_embedding(self, text):
        """
        Generate embedding vector for a text string.
        Returns a list of floats (768 dimensions).
        """
        try:
            cleaned_text = self._clean_text(text)
            if not cleaned_text:
                return None

            return gemini.embed(cleaned_text, task_type='retrieval_document')
        except GeminiError:
            logger.warning('Embedding unavailable for document', exc_info=True)
            return None
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

            # retrieval_query rather than retrieval_document: the model
            # embeds a short question differently from a long passage, and
            # using the wrong task type measurably degrades match quality.
            return gemini.embed(cleaned_query, task_type='retrieval_query')
        except GeminiError:
            logger.warning('Embedding unavailable for query', exc_info=True)
            return None
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
