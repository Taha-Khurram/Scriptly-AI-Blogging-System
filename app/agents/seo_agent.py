"""
SEO Agent - Advanced Keyword Research & Content Optimization
Analyzes content, finds low-competition keywords, and auto-optimizes blogs
with real content analysis and scoring.

Features:
- Real keyword data from Ahrefs Keyword Research API
- Actual content analysis (not AI self-reporting)
- Readability scoring (Flesch-Kincaid)
- Keyword density analysis
- Heading structure validation
- Meta description optimization
- Content length recommendations
- Internal/external link detection
- Response caching for keyword lookups
"""

import google.generativeai as genai
import requests
import os
import re
import math
import inspect
from typing import Dict, List, Optional
from collections import Counter
from app.utils.cache import cache


# Raise the client deadline well above the default ~60s gRPC timeout. Full-blog
# SEO optimization is a large prompt that routinely runs past 60s and was
# surfacing as "504 Deadline Exceeded".
SEO_TIMEOUT_SECONDS = 180

# `request_options` is only accepted by google-generativeai >= 0.4. Older builds
# raise on unknown kwargs, so we feature-detect (mirrors ContentAgent).
_SUPPORTS_REQUEST_OPTIONS = (
    'request_options'
    in inspect.signature(genai.GenerativeModel.generate_content).parameters
)


class SEOAgent:
    def __init__(self):
        genai.configure(api_key=os.getenv('GEMINI_API_KEY'))
        self.model = genai.GenerativeModel('gemini-2.5-flash')

        # Extended per-call deadline, passed to every generate_content call.
        self._gen_kwargs = {}
        if _SUPPORTS_REQUEST_OPTIONS:
            self._gen_kwargs['request_options'] = {"timeout": SEO_TIMEOUT_SECONDS}

        # Ahrefs Keyword Research API on RapidAPI
        self.ahrefs_key = os.getenv('AHREFS_RAPIDAPI_KEY', '')
        self.ahrefs_keyword_host = "ahrefs-keyword-research.p.rapidapi.com"

        # SEO Best Practices Constants
        self.IDEAL_TITLE_LENGTH = (50, 60)
        self.IDEAL_META_LENGTH = (150, 160)
        self.IDEAL_KEYWORD_DENSITY = (1.0, 2.5)  # percentage
        self.MIN_CONTENT_WORDS = 300
        self.IDEAL_CONTENT_WORDS = (1000, 2000)
        self.IDEAL_PARAGRAPH_LENGTH = (100, 200)  # words
        self.IDEAL_SENTENCE_LENGTH = (15, 20)  # words

    # =========================================
    # ADVANCED CONTENT ANALYSIS
    # =========================================
    def analyze_content(self, content: str, title: str = "", target_keyword: str = "") -> Dict:
        """
        Comprehensive SEO analysis of content
        Returns detailed metrics and scores
        """
        # Clean content for analysis
        text_only = self._strip_markdown(content)
        words = text_only.lower().split()
        word_count = len(words)

        # Basic metrics
        sentences = self._count_sentences(text_only)
        paragraphs = [p for p in content.split('\n\n') if p.strip()]

        # Heading analysis
        headings = self._analyze_headings(content)

        # Keyword analysis
        keyword_analysis = self._analyze_keyword_usage(content, text_only, target_keyword) if target_keyword else {}

        # Readability
        readability = self._calculate_readability(text_only, sentences, word_count)

        # Link analysis
        links = self._analyze_links(content)

        # Image analysis
        images = self._analyze_images(content)

        # Title analysis
        title_analysis = self._analyze_title(title, target_keyword) if title else {}

        # Calculate overall SEO score
        seo_score = self._calculate_comprehensive_seo_score(
            word_count=word_count,
            headings=headings,
            keyword_analysis=keyword_analysis,
            readability=readability,
            links=links,
            images=images,
            title_analysis=title_analysis
        )

        return {
            "word_count": word_count,
            "sentence_count": sentences,
            "paragraph_count": len(paragraphs),
            "avg_sentence_length": round(word_count / max(sentences, 1), 1),
            "avg_paragraph_length": round(word_count / max(len(paragraphs), 1), 1),
            "headings": headings,
            "keyword_analysis": keyword_analysis,
            "readability": readability,
            "links": links,
            "images": images,
            "title_analysis": title_analysis,
            "seo_score": seo_score,
            "issues": self._identify_issues(seo_score)
        }

    def _strip_markdown(self, content: str) -> str:
        """Remove markdown formatting to get plain text"""
        # Remove code blocks
        text = re.sub(r'```[\s\S]*?```', '', content)
        text = re.sub(r'`[^`]+`', '', text)
        # Remove images
        text = re.sub(r'!\[.*?\]\(.*?\)', '', text)
        # Remove links but keep text
        text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
        # Remove headers markers
        text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
        # Remove bold/italic
        text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
        text = re.sub(r'\*([^*]+)\*', r'\1', text)
        text = re.sub(r'__([^_]+)__', r'\1', text)
        text = re.sub(r'_([^_]+)_', r'\1', text)
        # Remove list markers
        text = re.sub(r'^\s*[-*+]\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)
        return text.strip()

    def _count_sentences(self, text: str) -> int:
        """Count sentences in text"""
        # Split by sentence-ending punctuation
        sentences = re.split(r'[.!?]+', text)
        return len([s for s in sentences if s.strip()])

    def _analyze_headings(self, content: str) -> Dict:
        """Analyze heading structure"""
        heading_pattern = re.compile(r'^(#{1,6})\s+(.+)$', re.MULTILINE)
        headings = []

        for match in heading_pattern.finditer(content):
            level = len(match.group(1))
            text = match.group(2).strip()
            headings.append({"level": level, "text": text})

        # Check hierarchy
        levels_used = [h['level'] for h in headings]
        has_h1 = 1 in levels_used
        has_h2 = 2 in levels_used
        proper_hierarchy = self._check_heading_hierarchy(levels_used)

        # Count by level
        level_counts = Counter(levels_used)

        return {
            "total": len(headings),
            "has_h1": has_h1,
            "has_h2": has_h2,
            "h1_count": level_counts.get(1, 0),
            "h2_count": level_counts.get(2, 0),
            "h3_count": level_counts.get(3, 0),
            "proper_hierarchy": proper_hierarchy,
            "headings_list": headings,
            "score": self._score_headings(headings, has_h1, has_h2, proper_hierarchy)
        }

    def _check_heading_hierarchy(self, levels: List[int]) -> bool:
        """Check if headings follow proper hierarchy (no skipping levels)"""
        if not levels:
            return True

        for i in range(1, len(levels)):
            # Allow going up any amount, but down only by 1
            if levels[i] > levels[i-1] + 1:
                return False
        return True

    def _score_headings(self, headings: List, has_h1: bool, has_h2: bool, proper_hierarchy: bool) -> int:
        """Score heading structure out of 100"""
        score = 0

        if headings:
            score += 20  # Has headings
        if has_h1:
            score += 25  # Has H1
        if has_h2:
            score += 25  # Has H2
        if proper_hierarchy:
            score += 20  # Proper hierarchy
        if len(headings) >= 3:
            score += 10  # Multiple headings for structure

        return min(100, score)

    def _analyze_keyword_usage(self, content: str, text_only: str, keyword: str) -> Dict:
        """Analyze how a keyword is used in content"""
        keyword_lower = keyword.lower()
        content_lower = content.lower()
        text_lower = text_only.lower()

        # Count occurrences
        keyword_count = text_lower.count(keyword_lower)
        word_count = len(text_lower.split())

        # Calculate density
        keyword_words = len(keyword_lower.split())
        density = (keyword_count * keyword_words / max(word_count, 1)) * 100

        # Check positions
        in_title = keyword_lower in content_lower[:200]  # First 200 chars likely has title

        # Check first paragraph (first 500 chars of text)
        first_para = text_lower[:500]
        in_first_paragraph = keyword_lower in first_para

        # Check in headings
        heading_pattern = re.compile(r'^#{1,6}\s+(.+)$', re.MULTILINE)
        headings_text = ' '.join(m.group(1).lower() for m in heading_pattern.finditer(content))
        in_headings = keyword_lower in headings_text

        # Check variations
        keyword_parts = keyword_lower.split()
        partial_matches = sum(1 for part in keyword_parts if part in text_lower)

        # Score keyword usage
        score = self._score_keyword_usage(
            keyword_count, density, in_title, in_first_paragraph, in_headings, word_count
        )

        return {
            "keyword": keyword,
            "count": keyword_count,
            "density": round(density, 2),
            "density_status": self._get_density_status(density),
            "in_title": in_title,
            "in_first_paragraph": in_first_paragraph,
            "in_headings": in_headings,
            "partial_matches": partial_matches,
            "score": score
        }

    def _get_density_status(self, density: float) -> str:
        """Get status of keyword density"""
        if density < 0.5:
            return "too_low"
        elif density < 1.0:
            return "low"
        elif density <= 2.5:
            return "optimal"
        elif density <= 3.5:
            return "high"
        else:
            return "too_high"

    def _score_keyword_usage(self, count: int, density: float, in_title: bool,
                            in_first_para: bool, in_headings: bool, word_count: int) -> int:
        """Score keyword usage out of 100"""
        score = 0

        # Keyword present
        if count > 0:
            score += 15

        # Density scoring
        if 1.0 <= density <= 2.5:
            score += 25  # Optimal
        elif 0.5 <= density < 1.0 or 2.5 < density <= 3.5:
            score += 15  # Acceptable
        elif density > 0:
            score += 5   # Present but not optimal

        # Position scoring
        if in_title:
            score += 20
        if in_first_para:
            score += 20
        if in_headings:
            score += 15

        # Natural usage (not too few, not too many)
        expected_count = max(2, word_count // 200)  # Roughly 1 per 200 words minimum
        if count >= expected_count:
            score += 5

        return min(100, score)

    def _calculate_readability(self, text: str, sentences: int, word_count: int) -> Dict:
        """Calculate readability metrics including Flesch-Kincaid"""
        if word_count == 0 or sentences == 0:
            return {"score": 0, "grade_level": "N/A", "status": "insufficient_content"}

        # Count syllables (approximate)
        syllables = self._count_syllables(text)

        # Flesch Reading Ease
        # 206.835 - 1.015 * (words/sentences) - 84.6 * (syllables/words)
        avg_sentence_length = word_count / sentences
        avg_syllables_per_word = syllables / word_count

        flesch_score = 206.835 - (1.015 * avg_sentence_length) - (84.6 * avg_syllables_per_word)
        flesch_score = max(0, min(100, flesch_score))

        # Flesch-Kincaid Grade Level
        # 0.39 * (words/sentences) + 11.8 * (syllables/words) - 15.59
        grade_level = (0.39 * avg_sentence_length) + (11.8 * avg_syllables_per_word) - 15.59
        grade_level = max(0, grade_level)

        # Determine readability status
        if flesch_score >= 60:
            status = "easy"
        elif flesch_score >= 40:
            status = "moderate"
        else:
            status = "difficult"

        return {
            "flesch_score": round(flesch_score, 1),
            "grade_level": round(grade_level, 1),
            "avg_sentence_length": round(avg_sentence_length, 1),
            "avg_syllables_per_word": round(avg_syllables_per_word, 2),
            "status": status,
            "score": self._score_readability(flesch_score, avg_sentence_length)
        }

    def _count_syllables(self, text: str) -> int:
        """Approximate syllable count"""
        words = text.lower().split()
        total = 0

        for word in words:
            word = re.sub(r'[^a-z]', '', word)
            if not word:
                continue

            # Count vowel groups
            syllables = len(re.findall(r'[aeiouy]+', word))

            # Adjustments
            if word.endswith('e'):
                syllables -= 1
            if word.endswith('le') and len(word) > 2 and word[-3] not in 'aeiouy':
                syllables += 1
            if syllables == 0:
                syllables = 1

            total += syllables

        return total

    def _score_readability(self, flesch_score: float, avg_sentence_length: float) -> int:
        """Score readability for SEO (targeting general audience)"""
        score = 0

        # Flesch score (target: 60-70 for general web content)
        if flesch_score >= 60:
            score += 60
        elif flesch_score >= 50:
            score += 45
        elif flesch_score >= 40:
            score += 30
        else:
            score += 15

        # Sentence length (target: 15-20 words)
        if 15 <= avg_sentence_length <= 20:
            score += 40
        elif 12 <= avg_sentence_length <= 25:
            score += 25
        else:
            score += 10

        return min(100, score)

    def _analyze_links(self, content: str) -> Dict:
        """Analyze links in content"""
        # Find all markdown links
        link_pattern = re.compile(r'\[([^\]]+)\]\(([^\)]+)\)')
        links = link_pattern.findall(content)

        internal = []
        external = []

        for text, url in links:
            if url.startswith(('http://', 'https://', 'www.')):
                external.append({"text": text, "url": url})
            else:
                internal.append({"text": text, "url": url})

        return {
            "total": len(links),
            "internal_count": len(internal),
            "external_count": len(external),
            "has_internal": len(internal) > 0,
            "has_external": len(external) > 0,
            "score": self._score_links(len(internal), len(external))
        }

    def _score_links(self, internal: int, external: int) -> int:
        """Score link usage"""
        score = 0

        if internal > 0:
            score += 40  # Has internal links
        if internal >= 2:
            score += 20  # Multiple internal links
        if external > 0:
            score += 25  # Has external/reference links
        if internal > 0 and external > 0:
            score += 15  # Good mix

        return min(100, score)

    def _analyze_images(self, content: str) -> Dict:
        """Analyze images in content"""
        # Find markdown images
        image_pattern = re.compile(r'!\[([^\]]*)\]\(([^\)]+)\)')
        images = image_pattern.findall(content)

        with_alt = sum(1 for alt, url in images if alt.strip())
        without_alt = len(images) - with_alt

        return {
            "total": len(images),
            "with_alt_text": with_alt,
            "without_alt_text": without_alt,
            "score": self._score_images(len(images), with_alt)
        }

    def _score_images(self, total: int, with_alt: int) -> int:
        """Score image usage"""
        if total == 0:
            return 50  # No images is neutral, not bad

        score = 30  # Has images

        # Alt text percentage
        alt_percentage = with_alt / total
        if alt_percentage == 1:
            score += 70  # All have alt text
        elif alt_percentage >= 0.8:
            score += 50
        elif alt_percentage >= 0.5:
            score += 30

        return min(100, score)

    def _analyze_title(self, title: str, keyword: str = "") -> Dict:
        """Analyze title for SEO"""
        title_length = len(title)

        # Check length
        if 50 <= title_length <= 60:
            length_status = "optimal"
        elif 40 <= title_length < 50 or 60 < title_length <= 70:
            length_status = "acceptable"
        else:
            length_status = "needs_improvement"

        # Check keyword in title
        has_keyword = keyword.lower() in title.lower() if keyword else False
        keyword_at_start = title.lower().startswith(keyword.lower()[:20]) if keyword else False

        return {
            "length": title_length,
            "length_status": length_status,
            "has_keyword": has_keyword,
            "keyword_at_start": keyword_at_start,
            "score": self._score_title(title_length, has_keyword, keyword_at_start)
        }

    def _score_title(self, length: int, has_keyword: bool, keyword_at_start: bool) -> int:
        """Score title for SEO"""
        score = 0

        # Length scoring
        if 50 <= length <= 60:
            score += 40
        elif 40 <= length <= 70:
            score += 25
        elif length > 0:
            score += 10

        # Keyword scoring
        if has_keyword:
            score += 35
        if keyword_at_start:
            score += 25

        return min(100, score)

    def _calculate_comprehensive_seo_score(self, word_count: int, headings: Dict,
                                          keyword_analysis: Dict, readability: Dict,
                                          links: Dict, images: Dict, title_analysis: Dict) -> Dict:
        """Calculate comprehensive SEO score with breakdown"""

        # Content length score
        if word_count >= 1500:
            content_score = 100
        elif word_count >= 1000:
            content_score = 85
        elif word_count >= 600:
            content_score = 70
        elif word_count >= 300:
            content_score = 50
        else:
            content_score = 25

        # Get individual scores
        heading_score = headings.get('score', 0)
        keyword_score = keyword_analysis.get('score', 50) if keyword_analysis else 50
        readability_score = readability.get('score', 50)
        link_score = links.get('score', 0)
        image_score = images.get('score', 50)
        title_score = title_analysis.get('score', 50) if title_analysis else 50

        # Weighted average
        weights = {
            'content': 0.15,
            'headings': 0.15,
            'keywords': 0.25,
            'readability': 0.15,
            'links': 0.10,
            'images': 0.05,
            'title': 0.15
        }

        total_score = (
            content_score * weights['content'] +
            heading_score * weights['headings'] +
            keyword_score * weights['keywords'] +
            readability_score * weights['readability'] +
            link_score * weights['links'] +
            image_score * weights['images'] +
            title_score * weights['title']
        )

        return {
            "total": round(total_score),
            "breakdown": {
                "content_length": {"score": content_score, "weight": "15%"},
                "headings": {"score": heading_score, "weight": "15%"},
                "keywords": {"score": keyword_score, "weight": "25%"},
                "readability": {"score": readability_score, "weight": "15%"},
                "links": {"score": link_score, "weight": "10%"},
                "images": {"score": image_score, "weight": "5%"},
                "title": {"score": title_score, "weight": "15%"}
            },
            "grade": self._get_grade(total_score)
        }

    def _get_grade(self, score: float) -> str:
        """Convert score to letter grade"""
        if score >= 90:
            return "A+"
        elif score >= 80:
            return "A"
        elif score >= 70:
            return "B"
        elif score >= 60:
            return "C"
        elif score >= 50:
            return "D"
        else:
            return "F"

    def _identify_issues(self, seo_score: Dict) -> List[Dict]:
        """Identify SEO issues based on scores"""
        issues = []
        breakdown = seo_score.get('breakdown', {})

        # Check each category
        if breakdown.get('content_length', {}).get('score', 100) < 70:
            issues.append({
                "type": "content_length",
                "severity": "medium",
                "message": "Content is shorter than recommended. Aim for 1000+ words for comprehensive coverage.",
                "priority": 2
            })

        if breakdown.get('headings', {}).get('score', 100) < 60:
            issues.append({
                "type": "headings",
                "severity": "medium",
                "message": "Improve heading structure. Use H1 for title, H2 for main sections, H3 for subsections.",
                "priority": 3
            })

        if breakdown.get('keywords', {}).get('score', 100) < 50:
            issues.append({
                "type": "keywords",
                "severity": "high",
                "message": "Keyword optimization needs improvement. Include target keyword in title, first paragraph, and headings.",
                "priority": 1
            })

        if breakdown.get('readability', {}).get('score', 100) < 50:
            issues.append({
                "type": "readability",
                "severity": "medium",
                "message": "Content may be difficult to read. Use shorter sentences and simpler words.",
                "priority": 4
            })

        if breakdown.get('links', {}).get('score', 100) < 40:
            issues.append({
                "type": "links",
                "severity": "low",
                "message": "Add internal links to related content and external links to authoritative sources.",
                "priority": 5
            })

        if breakdown.get('title', {}).get('score', 100) < 60:
            issues.append({
                "type": "title",
                "severity": "high",
                "message": "Optimize title: Keep it 50-60 characters and include your target keyword.",
                "priority": 1
            })

        # Sort by priority
        issues.sort(key=lambda x: x['priority'])

        return issues

    # =========================================
    # KEYWORD RESEARCH (Existing methods updated)
    # =========================================
    def _extract_seed_keywords(self, topic: str, content: str = "") -> List[str]:
        """Use AI to extract main keyword concepts from the blog topic/content"""
        prompt = f"""
        Extract 5-8 seed keywords from this blog topic and content.
        Return ONLY a comma-separated list of keywords, nothing else.

        Topic: {topic}
        Content Preview: {content[:500] if content else 'N/A'}

        Focus on:
        - Main subject keywords
        - Related concepts
        - Long-tail variations
        """

        response = self.model.generate_content(prompt, **self._gen_kwargs)
        text = (response.text or "") if response else ""
        if not text.strip():
            return [topic.lower()] if topic else []
        keywords = [kw.strip() for kw in text.split(',')]
        return keywords

    def extract_seed_keywords(self, topic: str, content: str) -> List[str]:
        """Public wrapper for seed keyword extraction"""
        return self._extract_seed_keywords(topic, content)

    def get_keyword_data(self, keywords: List[str], region: str = "US") -> List[Dict]:
        """Fetch keyword metrics from Ahrefs Keyword Research API"""
        cache_key = f"keywords:{region}:{':'.join(sorted(keywords[:5]))}"
        cached_result = cache.get(cache_key)
        if cached_result is not None:
            print("Using cached keyword data")
            return cached_result

        if not self.ahrefs_key:
            print("ERROR: AHREFS_RAPIDAPI_KEY not configured")
            return []

        country = region.lower() if region else "us"
        results = []

        for kw in keywords[:8]:
            data = self._fetch_ahrefs_keyword(kw, country)
            if data:
                results.append(data)

        if results:
            print(f"Using Ahrefs keyword data ({len(results)} keywords)")
            cache.set(cache_key, results, ttl=900)

        return results

    def _fetch_ahrefs_keyword(self, keyword: str, country: str) -> Optional[Dict]:
        """Fetch metrics for a single keyword from Ahrefs API"""
        try:
            resp = requests.get(
                f"https://{self.ahrefs_keyword_host}/keyword-metrics",
                params={"keyword": keyword, "country": country},
                headers={
                    "x-rapidapi-key": self.ahrefs_key,
                    "x-rapidapi-host": self.ahrefs_keyword_host
                },
                timeout=30
            )
            if resp.status_code == 200:
                raw = resp.json()
                data = raw.get("data", raw) if raw.get("success") else raw

                volume = data.get("volume", data.get("search_volume", 0)) or 0
                difficulty = data.get("difficulty", data.get("keyword_difficulty", 50)) or 50
                cpc = data.get("cpc", 0) or 0

                return {
                    "keyword": data.get("keyword", keyword),
                    "search_volume": int(volume),
                    "difficulty_score": int(difficulty),
                    "cpc": float(cpc),
                    "competition": self._map_competition(difficulty),
                    "clicks": data.get("clicks", 0) or 0,
                    "traffic_potential": data.get("trafficPotential", data.get("traffic_potential", 0)) or 0,
                    "source": "ahrefs"
                }
            elif resp.status_code == 429:
                print(f"Ahrefs rate limited for keyword: {keyword}")
            else:
                print(f"Ahrefs API returned {resp.status_code} for: {keyword}")
        except Exception as e:
            print(f"Ahrefs keyword fetch error for '{keyword}': {e}")
        return None

    def _map_competition(self, value) -> str:
        """Map numeric competition to LOW/MEDIUM/HIGH"""
        if isinstance(value, str):
            return value.upper()
        if isinstance(value, float) and value <= 1:
            value = value * 100
        if value <= 33:
            return "LOW"
        elif value <= 66:
            return "MEDIUM"
        else:
            return "HIGH"

    def _get_country_code(self, region: str) -> str:
        """Convert region code to lowercase country code for Ahrefs API"""
        return region.lower() if region else "us"

    # =========================================
    # KEYWORD RESEARCH PIPELINE
    # =========================================
    def find_low_competition_keywords(self, topic: str, content: str, region: str = "US",
                                      max_difficulty: int = 40, min_volume: int = 100) -> Dict:
        """Find low-competition keywords"""
        seed_keywords = self.extract_seed_keywords(topic, content)
        print(f"Seed keywords: {seed_keywords}")

        all_keywords = self.get_keyword_data(seed_keywords, region)

        if not all_keywords:
            return {
                "region": region,
                "primary_keyword": None,
                "secondary_keywords": [],
                "all_opportunities": [],
                "seed_keywords": seed_keywords,
                "error": "No real keyword data available.",
                "data_source": "none"
            }

        low_competition = [
            kw for kw in all_keywords
            if kw['difficulty_score'] <= max_difficulty
            and kw['search_volume'] >= min_volume
        ]

        if not low_competition:
            low_competition = sorted(
                all_keywords,
                key=lambda x: x['search_volume'] / (x['difficulty_score'] + 1),
                reverse=True
            )

        low_competition.sort(
            key=lambda x: x['search_volume'] / (x['difficulty_score'] + 1),
            reverse=True
        )

        primary = low_competition[0] if low_competition else (all_keywords[0] if all_keywords else None)
        secondary = low_competition[1:4] if len(low_competition) > 1 else []
        data_source = all_keywords[0].get('source', 'unknown') if all_keywords else 'none'

        return {
            "region": region,
            "primary_keyword": primary,
            "secondary_keywords": secondary,
            "all_opportunities": low_competition[:10],
            "all_keywords": all_keywords,
            "seed_keywords": seed_keywords,
            "data_source": data_source
        }

    # =========================================
    # CONTENT OPTIMIZATION
    # =========================================
    def _find_first_paragraph(self, content: str):
        """Return the first real body paragraph (not a heading, list, or code)."""
        blocks = re.split(r'\n\s*\n', content)
        for block in blocks:
            stripped = block.strip()
            if not stripped:
                continue
            if stripped.startswith('#') or stripped.startswith('```'):
                continue
            if re.match(r'^\s*[-*+]\s', stripped) or re.match(r'^\s*\d+\.\s', stripped):
                continue
            return block
        return None

    def _apply_seo_to_content(self, content: str, primary_keyword: str,
                              keyword_intro: str, faq_section: list) -> str:
        """
        Apply SEO changes ADDITIVELY to the existing content.

        The original body is preserved verbatim — this never rewrites or
        truncates it. It only:
          1. weaves a keyword-bearing intro sentence into the first paragraph,
             and only when the primary keyword is genuinely missing there, and
          2. appends an FAQ section at the end (if one isn't already present).
        """
        updated = content.rstrip()

        # 1. Ensure the primary keyword appears early in the body.
        if primary_keyword and keyword_intro:
            first_para = self._find_first_paragraph(updated)
            if first_para is not None and primary_keyword.lower() not in first_para.lower():
                intro = keyword_intro.strip()
                if intro and not intro.endswith(('.', '!', '?')):
                    intro += '.'
                if intro:
                    new_para = f"{intro} {first_para.strip()}"
                    updated = updated.replace(first_para, new_para, 1)

        # 2. Append an FAQ section (skip if the content already has one).
        if faq_section and 'frequently asked questions' not in updated.lower():
            faq_parts = ['## Frequently Asked Questions', '']
            for item in faq_section:
                question = (item.get('question') or '').strip()
                answer = (item.get('answer') or '').strip()
                if question and answer:
                    faq_parts.append(f"### {question}")
                    faq_parts.append('')
                    faq_parts.append(answer)
                    faq_parts.append('')
            if len(faq_parts) > 2:
                updated = updated.rstrip() + '\n\n' + '\n'.join(faq_parts).rstrip()

        return updated

    def auto_implement_seo(self, title: str, content: str, keyword_data: Dict) -> Dict:
        """Automatically optimize the blog content for SEO"""
        primary_kw = keyword_data.get('primary_keyword', {})
        primary_keyword = primary_kw.get('keyword', '') if primary_kw else ''
        secondary_kws = [kw['keyword'] for kw in keyword_data.get('secondary_keywords', [])]

        # Send a trimmed, code-free copy of the content to the model for CONTEXT
        # ONLY. We no longer ask the model to rewrite the body, so this is just
        # so it understands the topic when writing the title/meta/FAQ.
        clean_content = re.sub(r'```[\s\S]*?```', '[code]', content[:4000])
        clean_content = re.sub(r'`[^`]+`', '[inline-code]', clean_content)

        prompt = f"""TASK: Generate SEO enhancements for this blog post. Do NOT rewrite or return the article body. Return ONLY a JSON object.

PRIMARY KEYWORD: {primary_keyword}
SECONDARY KEYWORDS: {', '.join(secondary_kws)}

TITLE: {title}
CONTENT (for context only — do not rewrite it):
{clean_content}

Provide these SEO additions that will be applied ON TOP of the existing content (the body itself stays unchanged):
1. optimized_title: rewrite the title to include the primary keyword (50-60 chars)
2. meta_description: 150-160 chars including the primary keyword
3. keyword_intro: ONE natural sentence (15-25 words) featuring the primary keyword that can be woven in as the opening line of the article. It must read naturally and match the article's topic.
4. faq_section: exactly 3 relevant questions, each with a concise 2-3 sentence answer

OUTPUT FORMAT - respond with ONLY this JSON structure, nothing else:
{{"optimized_title": "string", "meta_description": "string", "keyword_intro": "string", "faq_section": [{{"question": "string", "answer": "string"}}]}}"""

        try:
            response = None
            import time as _time
            for attempt in range(3):
                try:
                    response = self.model.generate_content(prompt, **self._gen_kwargs)
                    if response and response.text:
                        break
                except Exception as retry_err:
                    print(f"Gemini attempt {attempt+1} failed: {retry_err}")
                    if attempt < 2:
                        _time.sleep(2)
            if not response or not response.text:
                raise ValueError("Gemini failed to generate content after 3 attempts")
        except Exception as e:
            print(f"Gemini API error in auto_implement_seo: {e}")
            analysis = self.analyze_content(content, title, primary_keyword)
            return {
                "optimized_title": title,
                "meta_description": content[:150],
                "optimized_content": content,
                "seo_analysis": analysis,
                "seo_score": analysis['seo_score']['total'],
                "seo_grade": analysis['seo_score']['grade'],
                "error": str(e)
            }

        try:
            import json
            text = (response.text or "").strip()

            if not text:
                raise ValueError("Gemini returned an empty response")

            # Remove markdown code blocks
            if '```' in text:
                json_match = re.search(r'```(?:json)?\s*\n?([\s\S]*?)\n?```', text)
                if json_match:
                    text = json_match.group(1).strip()
                else:
                    text = re.sub(r'^```json?\n?', '', text)
                    text = re.sub(r'\n?```$', '', text)

            # Find the JSON object - look for opening { and matching closing }
            start_idx = text.find('{')
            if start_idx != -1:
                brace_count = 0
                end_idx = start_idx
                for i, char in enumerate(text[start_idx:], start_idx):
                    if char == '{':
                        brace_count += 1
                    elif char == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            end_idx = i + 1
                            break
                text = text[start_idx:end_idx]
            else:
                raise ValueError(f"No JSON object found in Gemini response: {text[:200]}")

            # Try parsing as-is first
            try:
                result = json.loads(text)
            except json.JSONDecodeError:
                # Clean common issues from AI-generated JSON
                cleaned = text
                # Remove trailing commas before } or ]
                cleaned = re.sub(r',\s*([}\]])', r'\1', cleaned)
                # Replace single quotes with double quotes (but not within already double-quoted strings)
                cleaned = re.sub(r"(?<![\"\\])'([^']*)'(?![\"\\])", r'"\1"', cleaned)
                # Remove JavaScript-style comments
                cleaned = re.sub(r'//[^\n]*', '', cleaned)
                # Fix unescaped newlines inside strings
                cleaned = re.sub(r'(?<=": ")(.*?)(?="[,}\]])', lambda m: m.group(0).replace('\n', '\\n'), cleaned)
                result = json.loads(cleaned)

            # Apply the SEO additions ON TOP of the existing content instead of
            # replacing it. The original body is preserved verbatim — we only
            # weave in a keyword intro (if missing) and append an FAQ section.
            optimized_content = self._apply_seo_to_content(
                content=content,
                primary_keyword=primary_keyword,
                keyword_intro=result.get('keyword_intro', ''),
                faq_section=result.get('faq_section', [])
            )
            result['optimized_content'] = optimized_content

            optimized_title = result.get('optimized_title', title)

            # Run real analysis on the optimized content
            analysis = self.analyze_content(
                content=optimized_content,
                title=optimized_title,
                target_keyword=primary_keyword
            )

            result['seo_analysis'] = analysis
            result['seo_score'] = analysis['seo_score']['total']
            result['seo_grade'] = analysis['seo_score']['grade']
            result['issues'] = analysis['issues']

            # Add keyword_placement for template compatibility
            keyword_data = analysis.get('keyword_analysis', {})
            result['keyword_placement'] = {
                'in_title': keyword_data.get('in_title', False),
                'in_first_paragraph': keyword_data.get('in_first_paragraph', False),
                'in_headings': keyword_data.get('in_headings', False),
                'density': keyword_data.get('density', 0),
                'count': keyword_data.get('count', 0)
            }

            return result

        except Exception as e:
            print(f"Error parsing SEO response: {e}")
            # Return basic analysis of original content
            analysis = self.analyze_content(content, title, primary_keyword)
            return {
                "optimized_title": title,
                "meta_description": content[:150],
                "optimized_content": content,
                "seo_analysis": analysis,
                "seo_score": analysis['seo_score']['total'],
                "seo_grade": analysis['seo_score']['grade'],
                "error": str(e)
            }

    # =========================================
    # MAIN OPTIMIZATION PIPELINE
    # =========================================
    def analyze_only(self, title: str, content: str, target_keyword: str = "") -> Dict:
        """
        Analyze content without optimization - Step 1 of two-step workflow
        Returns detailed analysis of current SEO status
        """
        print(f"Analyzing content SEO (no optimization)...")

        # Run comprehensive analysis
        analysis = self.analyze_content(content, title, target_keyword)

        # Extract keywords for suggestions (without AI call if no target keyword)
        seed_keywords = []
        if not target_keyword:
            # Simple keyword extraction from title
            words = title.lower().split()
            seed_keywords = [w for w in words if len(w) > 3][:5]

        return {
            "title": title,
            "content_preview": content[:500] + "..." if len(content) > 500 else content,
            "analysis": analysis,
            "seo_score": analysis['seo_score'],
            "issues": analysis['issues'],
            "word_count": analysis['word_count'],
            "readability": analysis['readability'],
            "headings": analysis['headings'],
            "links": analysis['links'],
            "images": analysis['images'],
            "suggested_keywords": seed_keywords,
            "recommendations": self._generate_analysis_recommendations(analysis)
        }

    def _generate_analysis_recommendations(self, analysis: Dict) -> List[str]:
        """Generate recommendations from analysis without keyword data"""
        recommendations = []

        # Score info
        seo_score = analysis.get('seo_score', {})
        total = seo_score.get('total', 0)
        grade = seo_score.get('grade', 'N/A')
        recommendations.append(f"Current SEO Score: {total}/100 (Grade: {grade})")

        # Content length
        word_count = analysis.get('word_count', 0)
        if word_count < 300:
            recommendations.append(f"⚠️ Content too short ({word_count} words). Aim for 1000+ words.")
        elif word_count < 600:
            recommendations.append(f"📝 Content is {word_count} words. Consider expanding to 1000+ for better ranking.")
        else:
            recommendations.append(f"✓ Good content length: {word_count} words")

        # Headings
        headings = analysis.get('headings', {})
        if not headings.get('has_h1'):
            recommendations.append("⚠️ Missing H1 heading - add a main title")
        if not headings.get('has_h2'):
            recommendations.append("⚠️ Missing H2 headings - add section headers")
        if headings.get('proper_hierarchy'):
            recommendations.append("✓ Good heading hierarchy")

        # Readability
        readability = analysis.get('readability', {})
        if readability.get('status') == 'difficult':
            recommendations.append("⚠️ Content may be difficult to read. Use shorter sentences.")
        elif readability.get('status') == 'easy':
            recommendations.append("✓ Good readability score")

        # Links
        links = analysis.get('links', {})
        if links.get('total', 0) == 0:
            recommendations.append("💡 Add internal and external links for better SEO")

        # Issues from analysis
        for issue in analysis.get('issues', [])[:3]:
            if issue['severity'] == 'high':
                recommendations.append(f"🔴 {issue['message']}")

        return recommendations

    # =========================================
    # URL-BASED SEO ANALYSIS (RapidAPI SEO Checker)
    # =========================================
    def analyze_url(self, url: str) -> Dict:
        """
        Analyze a live URL's SEO using the Ahrefs URL Research API.
        """
        if not self.ahrefs_key:
            return {"success": False, "error": "Ahrefs API key not configured"}

        cache_key = f"seo_url:{url}"
        cached = cache.get(cache_key)
        if cached:
            return cached

        try:
            resp = requests.get(
                "https://ahrefs-url-research.p.rapidapi.com/url-metrics",
                params={"url": url},
                headers={
                    "x-rapidapi-key": self.ahrefs_key,
                    "x-rapidapi-host": "ahrefs-url-research.p.rapidapi.com"
                },
                timeout=30
            )

            if resp.status_code != 200:
                return {"success": False, "error": f"API returned status {resp.status_code}"}

            raw = resp.json()
            api_data = raw.get("data", raw) if raw.get("success") else raw
            page = api_data.get("page", {}) if isinstance(api_data, dict) else {}
            domain = api_data.get("domain", {}) if isinstance(api_data, dict) else {}

            result = {
                "success": True,
                "url": url,
                "domain_rating": domain.get("domainRating", 0),
                "url_rating": page.get("urlRating", 0),
                "backlinks": page.get("backlinks", 0),
                "ref_domains": page.get("refDomains", 0),
                "organic_traffic": page.get("traffic", 0),
                "organic_keywords": domain.get("organicKeywords", 0),
                "word_count": page.get("numberOfWordsOnPage", 0),
            }

            cache.set(cache_key, result, ttl=1800)
            return result

        except Exception as e:
            print(f"Ahrefs URL analysis error: {e}")
            return {"success": False, "error": str(e)}


    def optimize_blog(self, title: str, content: str, region: str = "US") -> Dict:
        """Complete SEO optimization pipeline"""
        print(f"Starting SEO optimization for region: {region}")

        # Analyze original content first
        original_analysis = self.analyze_content(content, title)
        print(f"Original SEO Score: {original_analysis['seo_score']['total']}/100")

        # Find keywords
        keyword_data = self.find_low_competition_keywords(
            topic=title,
            content=content,
            region=region
        )

        if keyword_data.get('error'):
            return {
                "original": {"title": title, "content": content},
                "original_analysis": original_analysis,
                "keyword_research": keyword_data,
                "optimized": None,
                "error": keyword_data['error'],
                "recommendations": self._generate_recommendations_from_analysis(original_analysis)
            }

        print(f"Found {len(keyword_data.get('all_opportunities', []))} keyword opportunities")

        # Optimize content
        optimized = self.auto_implement_seo(title, content, keyword_data)

        # Calculate improvement
        original_score = original_analysis['seo_score']['total']
        new_score = optimized.get('seo_score', original_score)
        improvement = new_score - original_score

        # Build detailed comparison
        comparison = self._build_comparison(
            original_title=title,
            optimized_title=optimized.get('optimized_title', title),
            original_analysis=original_analysis,
            optimized_analysis=optimized.get('seo_analysis', {}),
            original_score=original_score,
            new_score=new_score
        )

        return {
            "original": {"title": title, "content": content},
            "original_analysis": original_analysis,
            "keyword_research": keyword_data,
            "optimized": optimized,
            "data_source": keyword_data.get('data_source', 'unknown'),
            "score_improvement": improvement,
            "comparison": comparison,
            "changes_made": self._list_changes_made(title, optimized, keyword_data),
            "recommendations": self._generate_recommendations_from_analysis(
                optimized.get('seo_analysis', original_analysis),
                keyword_data
            )
        }

    def _build_comparison(self, original_title: str, optimized_title: str,
                         original_analysis: Dict, optimized_analysis: Dict,
                         original_score: int, new_score: int) -> Dict:
        """Build detailed before/after comparison"""
        original_breakdown = original_analysis.get('seo_score', {}).get('breakdown', {})
        optimized_breakdown = optimized_analysis.get('seo_score', {}).get('breakdown', {})

        return {
            "scores": {
                "before": original_score,
                "after": new_score,
                "improvement": new_score - original_score,
                "improvement_percent": round(((new_score - original_score) / max(original_score, 1)) * 100, 1)
            },
            "grades": {
                "before": original_analysis.get('seo_score', {}).get('grade', 'N/A'),
                "after": optimized_analysis.get('seo_score', {}).get('grade', 'N/A')
            },
            "title": {
                "before": original_title,
                "after": optimized_title,
                "changed": original_title != optimized_title
            },
            "word_count": {
                "before": original_analysis.get('word_count', 0),
                "after": optimized_analysis.get('word_count', 0)
            },
            "readability": {
                "before": original_analysis.get('readability', {}).get('flesch_score', 0),
                "after": optimized_analysis.get('readability', {}).get('flesch_score', 0)
            },
            "breakdown_comparison": {
                "content_length": {
                    "before": original_breakdown.get('content_length', {}).get('score', 0),
                    "after": optimized_breakdown.get('content_length', {}).get('score', 0)
                },
                "headings": {
                    "before": original_breakdown.get('headings', {}).get('score', 0),
                    "after": optimized_breakdown.get('headings', {}).get('score', 0)
                },
                "keywords": {
                    "before": original_breakdown.get('keywords', {}).get('score', 0),
                    "after": optimized_breakdown.get('keywords', {}).get('score', 0)
                },
                "readability": {
                    "before": original_breakdown.get('readability', {}).get('score', 0),
                    "after": optimized_breakdown.get('readability', {}).get('score', 0)
                },
                "links": {
                    "before": original_breakdown.get('links', {}).get('score', 0),
                    "after": optimized_breakdown.get('links', {}).get('score', 0)
                },
                "title": {
                    "before": original_breakdown.get('title', {}).get('score', 0),
                    "after": optimized_breakdown.get('title', {}).get('score', 0)
                }
            }
        }

    def _list_changes_made(self, original_title: str, optimized: Dict, keyword_data: Dict) -> List[Dict]:
        """List all changes made during optimization"""
        changes = []
        primary_kw = keyword_data.get('primary_keyword', {}).get('keyword', 'target keyword')

        # Title change
        new_title = optimized.get('optimized_title', original_title)
        if new_title != original_title:
            changes.append({
                "type": "title",
                "description": "Title optimized with target keyword",
                "before": original_title[:60] + "..." if len(original_title) > 60 else original_title,
                "after": new_title[:60] + "..." if len(new_title) > 60 else new_title
            })

        # Meta description added
        if optimized.get('meta_description'):
            changes.append({
                "type": "meta",
                "description": "Meta description created",
                "before": "None",
                "after": optimized['meta_description'][:80] + "..."
            })

        # Keyword placement
        placement = optimized.get('keyword_placement', {})
        if placement.get('in_title'):
            changes.append({
                "type": "keyword",
                "description": f"Keyword '{primary_kw}' added to title",
                "impact": "high"
            })
        if placement.get('in_first_paragraph'):
            changes.append({
                "type": "keyword",
                "description": f"Keyword '{primary_kw}' added to first paragraph",
                "impact": "high"
            })
        if placement.get('in_headings'):
            changes.append({
                "type": "keyword",
                "description": f"Keyword '{primary_kw}' added to headings",
                "impact": "medium"
            })

        # FAQ added
        if optimized.get('faq_section'):
            changes.append({
                "type": "content",
                "description": f"FAQ section added with {len(optimized['faq_section'])} questions",
                "impact": "medium"
            })

        return changes

    def _generate_recommendations_from_analysis(self, analysis: Dict, keyword_data: Dict = None) -> List[str]:
        """Generate recommendations from actual analysis"""
        recommendations = []

        # Data source info
        if keyword_data:
            data_source = keyword_data.get('data_source', 'unknown')
            if data_source == 'google_trends':
                recommendations.append("Data Source: Google Trends (real search interest data)")
            elif data_source == 'google_related':
                recommendations.append("Data Source: Google Search API")

            primary = keyword_data.get('primary_keyword')
            if primary:
                recommendations.append(
                    f"Target Keyword: '{primary['keyword']}' - "
                    f"Difficulty: {primary['difficulty_score']}/100, "
                    f"Est. Volume: {primary['search_volume']}"
                )

        # Score-based recommendations
        seo_score = analysis.get('seo_score', {})
        total = seo_score.get('total', 0)
        grade = seo_score.get('grade', 'N/A')

        recommendations.append(f"SEO Score: {total}/100 (Grade: {grade})")

        # Specific recommendations from issues
        issues = analysis.get('issues', [])
        for issue in issues[:3]:  # Top 3 issues
            recommendations.append(f"[{issue['severity'].upper()}] {issue['message']}")

        # Readability
        readability = analysis.get('readability', {})
        if readability.get('status') == 'difficult':
            recommendations.append("Consider simplifying your content for better readability.")

        # Content length
        word_count = analysis.get('word_count', 0)
        if word_count < 600:
            recommendations.append(f"Content is {word_count} words. Aim for 1000+ words for better ranking.")
        elif word_count >= 1500:
            recommendations.append(f"Great content length ({word_count} words) for comprehensive coverage.")

        return recommendations


# =========================================
# USAGE EXAMPLE
# =========================================
if __name__ == "__main__":
    agent = SEOAgent()

    title = "Benefits of AI in Healthcare"
    content = """
    # Benefits of AI in Healthcare

    Artificial intelligence is transforming the healthcare industry...

    ## Diagnosis Improvement
    AI can analyze medical images faster than humans...

    ## Treatment Personalization
    Machine learning algorithms can predict patient outcomes...
    """

    result = agent.optimize_blog(title=title, content=content, region="PK")

    print("\n=== ORIGINAL SCORE ===")
    print(f"{result['original_analysis']['seo_score']['total']}/100")

    print("\n=== OPTIMIZED SCORE ===")
    if result.get('optimized'):
        print(f"{result['optimized']['seo_score']}/100 ({result['optimized']['seo_grade']})")

    print("\n=== RECOMMENDATIONS ===")
    for rec in result['recommendations']:
        print(f"• {rec}")
