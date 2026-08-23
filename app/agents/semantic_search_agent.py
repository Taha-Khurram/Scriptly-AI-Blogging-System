"""
Semantic Search Agent - Industry Standard Implementation

Architecture:
    Query → Understand → Plan → Execute Tools → Evaluate → Refine → Explain → Return

Patterns Implemented:
    - Query Understanding (intent classification, normalization, expansion)
    - Agentic Loop (plan, execute, evaluate, refine)
    - Tool Selection (keyword, vector, category tools)
    - Self-Evaluation (quality thresholds, refinement)
    - Observability (structured logging of decisions)

Cost Optimization:
    - All query understanding is rule-based (no LLM)
    - Intent classification uses pattern matching
    - Query expansion uses synonym dictionary
    - LLM used only for final explanation if needed
"""

import numpy as np
from app.services.embedding_service import EmbeddingService
from app.firebase.firestore_service import FirestoreService
import re
import logging
from dataclasses import dataclass, field
from typing import List, Dict
from enum import Enum

# Configure agent logger
logger = logging.getLogger('semantic_search_agent')


class QueryIntent(Enum):
    """Query intent types for search strategy selection."""
    INFORMATIONAL = "informational"  # "what is", "how to", "why"
    NAVIGATIONAL = "navigational"    # Looking for specific content
    EXPLORATORY = "exploratory"      # Browsing, discovering


class SearchTool(Enum):
    """Available search tools."""
    KEYWORD = "keyword"
    VECTOR = "vector"
    CATEGORY = "category"


@dataclass
class AgentState:
    """Tracks agent's reasoning and decisions."""
    query: str = ""
    normalized_query: str = ""
    intent: QueryIntent = QueryIntent.EXPLORATORY
    expanded_terms: List[str] = field(default_factory=list)
    plan: List[str] = field(default_factory=list)
    tools_used: List[str] = field(default_factory=list)
    iterations: int = 0
    quality_score: float = 0.0
    refinements: List[str] = field(default_factory=list)

    def to_log(self) -> Dict:
        """Convert state to loggable dict."""
        return {
            "query": self.query,
            "intent": self.intent.value,
            "expanded_terms": self.expanded_terms,
            "tools_used": self.tools_used,
            "iterations": self.iterations,
            "quality_score": self.quality_score,
            "refinements": self.refinements
        }


class SemanticSearchAgent:
    """
    Industry-standard Semantic Search Agent.

    Implements agentic patterns with minimal LLM usage:
    - Rule-based query understanding
    - Multi-tool search execution
    - Self-evaluation and refinement
    - Structured observability
    """

    # Stop words to filter from queries
    STOP_WORDS = {
        'a', 'an', 'the', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
        'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
        'should', 'may', 'might', 'must', 'shall', 'can', 'need', 'dare',
        'to', 'of', 'in', 'for', 'on', 'with', 'at', 'by', 'from', 'as',
        'into', 'through', 'during', 'before', 'after', 'above', 'below',
        'between', 'under', 'again', 'further', 'then', 'once', 'here',
        'there', 'when', 'where', 'why', 'how', 'all', 'each', 'few', 'more',
        'most', 'other', 'some', 'such', 'no', 'nor', 'not', 'only', 'own',
        'same', 'so', 'than', 'too', 'very', 'just', 'and', 'but', 'if', 'or',
        'because', 'until', 'while', 'about', 'against', 'this', 'that', 'these',
        'those', 'i', 'me', 'my', 'myself', 'we', 'our', 'you', 'your', 'he',
        'him', 'his', 'she', 'her', 'it', 'its', 'they', 'them', 'their', 'what',
        'which', 'who', 'whom', 'find', 'show', 'get', 'give', 'tell', 'want'
    }

    # Synonym dictionary for query expansion (no LLM needed)
    SYNONYMS = {
        'guide': ['tutorial', 'howto', 'walkthrough', 'introduction'],
        'tutorial': ['guide', 'howto', 'lesson', 'course'],
        'tips': ['advice', 'tricks', 'hacks', 'suggestions'],
        'best': ['top', 'recommended', 'optimal', 'ideal'],
        'learn': ['study', 'understand', 'master', 'discover'],
        'create': ['build', 'make', 'develop', 'design'],
        'fix': ['solve', 'repair', 'resolve', 'debug'],
        'improve': ['enhance', 'optimize', 'boost', 'upgrade'],
        'start': ['begin', 'getting started', 'introduction', 'basics'],
        'advanced': ['expert', 'professional', 'deep dive', 'complex'],
        'simple': ['easy', 'basic', 'beginner', 'straightforward'],
        'fast': ['quick', 'rapid', 'speedy', 'efficient'],
        'code': ['programming', 'coding', 'development', 'software'],
        'web': ['website', 'internet', 'online', 'frontend'],
        'api': ['endpoint', 'interface', 'service', 'backend'],
        'data': ['information', 'database', 'analytics', 'dataset'],
        'ai': ['artificial intelligence', 'machine learning', 'ml', 'neural'],
        'app': ['application', 'software', 'program', 'tool'],
    }

    # Navigational keywords (words that suggest looking for specific content)
    NAVIGATIONAL_KEYWORDS = {
        'guide', 'tutorial', 'howto', 'walkthrough', 'introduction', 'intro',
        'documentation', 'docs', 'example', 'examples', 'sample', 'demo',
        'template', 'starter', 'boilerplate', 'cheatsheet', 'reference',
        'basics', 'fundamentals', 'beginner', 'advanced', 'tips', 'tricks'
    }

    # Quality thresholds
    MIN_QUALITY_SCORE = 0.15
    HIGH_QUALITY_SCORE = 0.4
    MAX_ITERATIONS = 2

    def __init__(self):
        self.embedding_service = EmbeddingService()
        self.db_service = FirestoreService()

    # =========================================================================
    # PHASE 1: QUERY UNDERSTANDING (Rule-based, No LLM)
    # =========================================================================

    def _normalize_query(self, query: str) -> str:
        """Normalize query: lowercase, remove punctuation, clean whitespace."""
        normalized = query.lower().strip()
        normalized = re.sub(r'[^\w\s]', ' ', normalized)
        normalized = ' '.join(normalized.split())
        return normalized

    def _extract_terms(self, query: str) -> List[str]:
        """Extract meaningful terms, filtering stop words."""
        words = query.lower().split()
        terms = [w for w in words if w not in self.STOP_WORDS and len(w) > 2]
        return terms

    def _classify_intent(self, query: str) -> QueryIntent:
        """
        Classify query intent using pattern matching and heuristics (no LLM).

        Intent types:
        - INFORMATIONAL: User wants to learn/understand something (questions)
        - NAVIGATIONAL: User looking for specific content (guide, tutorial, docs)
        - EXPLORATORY: User browsing/discovering content on a topic
        """
        query_lower = query.lower().strip()
        words = query_lower.split()

        # 1. Check for question patterns → INFORMATIONAL
        question_patterns = [
            r'^what\s+', r'^how\s+', r'^why\s+', r'^when\s+', r'^where\s+',
            r'^who\s+', r'^which\s+', r'^can\s+', r'^could\s+', r'^should\s+',
            r'^is\s+', r'^are\s+', r'^does\s+', r'^do\s+', r'^will\s+',
            r'\?$', r'vs\.?$', r'versus', r'compared\s+to', r'difference'
        ]
        for pattern in question_patterns:
            if re.search(pattern, query_lower):
                return QueryIntent.INFORMATIONAL

        # 2. Check for navigational keywords → NAVIGATIONAL
        for word in words:
            if word in self.NAVIGATIONAL_KEYWORDS:
                return QueryIntent.NAVIGATIONAL

        # 3. Check navigational phrase patterns
        nav_patterns = [
            r'guide', r'tutorial', r'documentation', r'docs$',
            r'^find\s+', r'^show\s+', r'^looking\s+for', r'^search\s+for'
        ]
        for pattern in nav_patterns:
            if re.search(pattern, query_lower):
                return QueryIntent.NAVIGATIONAL

        # 4. Default: topic-based queries are EXPLORATORY (browsing/discovering)
        return QueryIntent.EXPLORATORY

    def _expand_query(self, terms: List[str]) -> List[str]:
        """Expand query with synonyms (no LLM)."""
        expanded = set(terms)

        for term in terms:
            if term in self.SYNONYMS:
                # Add first 2 synonyms to avoid over-expansion
                expanded.update(self.SYNONYMS[term][:2])

        return list(expanded)

    def _understand_query(self, query: str, state: AgentState) -> AgentState:
        """
        Phase 1: Understand the query.
        - Normalize
        - Extract terms
        - Classify intent
        - Expand with synonyms
        """
        state.query = query
        state.normalized_query = self._normalize_query(query)
        state.intent = self._classify_intent(query)

        base_terms = self._extract_terms(state.normalized_query)
        state.expanded_terms = self._expand_query(base_terms)

        logger.debug(f"Query understood: intent={state.intent.value}, terms={state.expanded_terms}")
        return state

    # =========================================================================
    # PHASE 2: PLANNING (Rule-based, No LLM)
    # =========================================================================

    def _create_plan(self, state: AgentState) -> AgentState:
        """
        Phase 2: Create search plan based on intent.
        Decides which tools to use and in what order.
        """
        plan = []

        if state.intent == QueryIntent.INFORMATIONAL:
            # Informational queries benefit from semantic understanding
            plan = ["vector_search", "keyword_boost", "explain_results"]
        elif state.intent == QueryIntent.NAVIGATIONAL:
            # Navigational queries need exact matching
            plan = ["keyword_search", "category_filter", "vector_boost"]
        else:
            # Exploratory: balanced approach
            plan = ["hybrid_search", "category_context"]

        state.plan = plan
        logger.debug(f"Plan created: {plan}")
        return state

    # =========================================================================
    # PHASE 3: TOOL EXECUTION
    # =========================================================================

    def _cosine_similarity(self, vec1, vec2) -> float:
        """Calculate cosine similarity between vectors."""
        vec1 = np.array(vec1, dtype=np.float32)
        vec2 = np.array(vec2, dtype=np.float32)
        dot = np.dot(vec1, vec2)
        norm = np.linalg.norm(vec1) * np.linalg.norm(vec2)
        return float(dot / norm) if norm > 0 else 0.0

    def _get_text_content(self, blog: Dict) -> str:
        """Extract searchable text from blog content."""
        content = blog.get('content', '')
        if isinstance(content, dict):
            text = content.get('body', '') or content.get('markdown', '') or ''
        else:
            text = str(content) if content else ''
        return re.sub(r'<[^>]+>', '', text).lower()

    def _get_excerpt(self, blog: Dict, max_length: int = 120) -> str:
        """Extract clean excerpt from blog."""
        content = blog.get('content', '')
        if isinstance(content, dict):
            text = content.get('body', '') or content.get('markdown', '') or ''
        else:
            text = str(content) if content else ''

        # Clean markdown and HTML
        text = re.sub(r'<[^>]+>', '', text)
        text = re.sub(r'^#+\s*', '', text, flags=re.MULTILINE)
        text = re.sub(r'\*\*?([^*]+)\*\*?', r'\1', text)
        text = re.sub(r'`[^`]+`', '', text)
        text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
        text = ' '.join(text.split())

        if len(text) > max_length:
            return text[:max_length].rsplit(' ', 1)[0] + '...'
        return text

    def _tool_keyword_search(self, blog: Dict, terms: List[str]) -> float:
        """
        TOOL: Keyword Search
        Scores based on term presence in title, category, content.
        """
        title = blog.get('title', '').lower()
        category = blog.get('category', '').lower()
        content = self._get_text_content(blog)

        score = 0.0
        matches = {'title': [], 'category': [], 'content': []}

        for term in terms:
            if term in title:
                score += 0.35
                matches['title'].append(term)
            if term in category:
                score += 0.25
                matches['category'].append(term)
            if term in content:
                score += 0.1
                matches['content'].append(term)

        return min(score, 1.0), matches

    def _tool_vector_search(self, blog: Dict, query_embedding: List[float]) -> float:
        """
        TOOL: Vector Search
        Calculates semantic similarity using embeddings.
        """
        blog_embedding = blog.get('embedding')
        if not query_embedding or not blog_embedding:
            return 0.0
        return self._cosine_similarity(query_embedding, blog_embedding)

    def _tool_category_filter(self, blog: Dict, terms: List[str]) -> float:
        """
        TOOL: Category Filter
        Boosts score if category matches any term.
        """
        category = blog.get('category', '').lower()
        for term in terms:
            if term in category or category in term:
                return 0.2
        return 0.0

    def _execute_tools(self, blogs: List[Dict], state: AgentState) -> List[Dict]:
        """
        Phase 3: Execute search tools based on plan.
        """
        # Get query embedding (may fail, that's OK)
        query_embedding = None
        try:
            query_embedding = self.embedding_service.generate_query_embedding(state.query)
            state.tools_used.append("vector_embedding")
        except Exception as e:
            logger.warning(f"Embedding failed, using keywords only: {e}")

        candidates = []

        for blog in blogs:
            # Always run keyword search
            kw_score, matches = self._tool_keyword_search(blog, state.expanded_terms)
            state.tools_used.append("keyword") if "keyword" not in state.tools_used else None

            # Vector search if available
            vec_score = 0.0
            if query_embedding:
                vec_score = self._tool_vector_search(blog, query_embedding)
                state.tools_used.append("vector") if "vector" not in state.tools_used else None

            # Category boost
            cat_boost = self._tool_category_filter(blog, state.expanded_terms)
            state.tools_used.append("category") if "category" not in state.tools_used else None

            # Combine scores based on intent
            if state.intent == QueryIntent.INFORMATIONAL:
                # Semantic understanding matters more
                score = (vec_score * 0.5) + (kw_score * 0.35) + (cat_boost * 0.15)
            elif state.intent == QueryIntent.NAVIGATIONAL:
                # Exact matches matter more
                score = (kw_score * 0.6) + (vec_score * 0.25) + (cat_boost * 0.15)
            else:
                # Balanced
                score = (vec_score * 0.4) + (kw_score * 0.4) + (cat_boost * 0.2)

            if score > 0.05:  # Low threshold, we'll filter later
                candidates.append({
                    'id': blog.get('id'),
                    'title': blog.get('title', ''),
                    'category': blog.get('category', ''),
                    'excerpt': self._get_excerpt(blog),
                    'cover_image': blog.get('cover_image_url', ''),
                    'score': round(score, 3),
                    'matches': matches,
                    'vec_score': round(vec_score, 3),
                    'kw_score': round(kw_score, 3),
                    'updated_at': blog.get('updated_at')
                })

        # Sort by score
        candidates.sort(key=lambda x: x['score'], reverse=True)
        return candidates

    # =========================================================================
    # PHASE 4: EVALUATION & REFINEMENT (Rule-based, No LLM)
    # =========================================================================

    def _evaluate_results(self, candidates: List[Dict], state: AgentState) -> float:
        """
        Phase 4a: Evaluate result quality.
        Returns quality score 0-1.
        """
        if not candidates:
            return 0.0

        # Quality factors
        top_score = candidates[0]['score'] if candidates else 0
        result_count = len([c for c in candidates if c['score'] > self.MIN_QUALITY_SCORE])
        score_spread = top_score - (candidates[-1]['score'] if len(candidates) > 1 else 0)

        # Quality score combines multiple factors
        quality = (top_score * 0.5) + (min(result_count / 5, 1) * 0.3) + (score_spread * 0.2)

        state.quality_score = round(quality, 3)
        return quality

    def _refine_search(self, candidates: List[Dict], state: AgentState, blogs: List[Dict]) -> List[Dict]:
        """
        Phase 4b: Refine if quality is low.
        Tries alternative strategies without LLM.
        """
        if state.quality_score >= self.MIN_QUALITY_SCORE:
            return candidates

        if state.iterations >= self.MAX_ITERATIONS:
            state.refinements.append("max_iterations_reached")
            return candidates

        state.iterations += 1

        # Refinement strategy 1: Broaden terms (use original query words)
        if not state.refinements:
            state.refinements.append("broadened_terms")
            original_terms = [w for w in state.query.lower().split() if len(w) > 1]
            state.expanded_terms = list(set(state.expanded_terms + original_terms))
            return self._execute_tools(blogs, state)

        # Refinement strategy 2: Lower threshold
        state.refinements.append("lowered_threshold")
        return candidates

    # =========================================================================
    # PHASE 5: EXPLANATION (Rule-based, No LLM)
    # =========================================================================

    def _generate_explanation(self, candidate: Dict, state: AgentState) -> str:
        """
        Phase 5: Generate human-readable explanation.
        Uses match data, no LLM needed.
        """
        matches = candidate.get('matches', {})
        title_matches = matches.get('title', [])
        category_matches = matches.get('category', [])

        # Build explanation based on what matched
        if title_matches:
            if len(title_matches) > 1:
                return f"Title matches '{title_matches[0]}' and {len(title_matches)-1} more terms"
            return f"Title contains '{title_matches[0]}'"

        if category_matches:
            category = candidate.get('category', '')
            return f"Found in '{category}' category"

        # Check if semantic match (high vector score, low keyword)
        if candidate.get('vec_score', 0) > 0.5 and candidate.get('kw_score', 0) < 0.2:
            return "Semantically related to your search"

        if candidate.get('vec_score', 0) > 0.3:
            return "Related to your topic"

        return "Matches your search criteria"

    def _add_explanations(self, candidates: List[Dict], state: AgentState) -> List[Dict]:
        """Add explanations to all candidates."""
        for candidate in candidates:
            candidate['match_reason'] = self._generate_explanation(candidate, state)
            # Clean up internal fields
            candidate.pop('matches', None)
            candidate.pop('vec_score', None)
            candidate.pop('kw_score', None)
        return candidates

    # =========================================================================
    # MAIN SEARCH (Agentic Loop)
    # =========================================================================

    def search(self, user_id: str, query: str, top_k: int = 6, include_insights: bool = False):
        """
        Main search method implementing agentic loop:

        1. Understand → 2. Plan → 3. Execute → 4. Evaluate → 5. Refine → 6. Explain

        Args:
            user_id: The user's ID
            query: Search query string
            top_k: Number of results to return
            include_insights: If True, returns (results, insights) tuple

        Returns:
            List of results, or (results, insights) tuple if include_insights=True
        """
        try:
            # Validation
            if not query or len(query.strip()) < 2:
                return ([], None) if include_insights else []

            # Initialize agent state
            state = AgentState()

            # PHASE 1: Understand
            state = self._understand_query(query, state)

            # PHASE 2: Plan
            state = self._create_plan(state)

            # Get all published blogs
            blogs = self.db_service.get_published_blogs(user_id, limit=50)
            if not blogs:
                logger.info("No blogs found for user")
                return ([], self._build_insights(state, 0)) if include_insights else []

            # PHASE 3: Execute
            candidates = self._execute_tools(blogs, state)

            # PHASE 4: Evaluate & Refine
            self._evaluate_results(candidates, state)
            candidates = self._refine_search(candidates, state, blogs)

            # Filter by minimum quality
            candidates = [c for c in candidates if c['score'] >= self.MIN_QUALITY_SCORE]

            if not candidates:
                logger.info(f"No results above threshold for query: {query}")
                insights = self._build_insights(state, 0) if include_insights else None
                return ([], insights) if include_insights else []

            # PHASE 5: Explain
            candidates = self._add_explanations(candidates[:top_k], state)

            # Log agent trace
            logger.info(f"Search completed: {state.to_log()}")

            if include_insights:
                return candidates, self._build_insights(state, len(candidates))
            return candidates

        except Exception as e:
            logger.error(f"Search error: {e}")
            return ([], None) if include_insights else []

    def _build_insights(self, state: AgentState, result_count: int) -> Dict:
        """Build frontend-friendly insights from agent state."""
        return {
            'intent': {
                'type': state.intent.value,
                'label': self._get_intent_label(state.intent)
            },
            'query_analysis': {
                'original': state.query,
                'normalized': state.normalized_query,
                'terms_used': state.expanded_terms[:8],  # Limit for display
                'expansion_applied': len(state.expanded_terms) > len(state.query.split())
            },
            'strategy': {
                'plan': state.plan,
                'tools_used': list(set(state.tools_used)),
                'iterations': state.iterations,
                'refinements': state.refinements
            },
            'quality': {
                'score': state.quality_score,
                'label': self._get_quality_label(state.quality_score),
                'result_count': result_count
            }
        }

    def _get_intent_label(self, intent: QueryIntent) -> str:
        """Get human-readable intent label."""
        labels = {
            QueryIntent.INFORMATIONAL: "Looking for information",
            QueryIntent.NAVIGATIONAL: "Finding specific content",
            QueryIntent.EXPLORATORY: "Exploring topics"
        }
        return labels.get(intent, "General search")

    def _get_quality_label(self, score: float) -> str:
        """Get human-readable quality label."""
        if score >= 0.6:
            return "Excellent match"
        elif score >= 0.4:
            return "Good match"
        elif score >= 0.2:
            return "Partial match"
        else:
            return "Best effort"

    # =========================================================================
    # EMBEDDING MANAGEMENT
    # =========================================================================

    def generate_and_store_embedding(self, blog_id: str) -> bool:
        """Generate and store embedding for a blog."""
        try:
            blog = self.db_service.get_blog_by_id(blog_id)
            if not blog:
                return False

            embedding = self.embedding_service.generate_blog_embedding(blog)
            if not embedding:
                return False

            return self.db_service.update_blog_embedding(blog_id, embedding)
        except Exception as e:
            logger.error(f"Embedding error for blog {blog_id}: {e}")
            return False
