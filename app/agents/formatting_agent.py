"""
Formatting Agent - Ensures consistent formatting and professional presentation
Handles markdown processing, TOC generation, reading time, and HTML conversion
"""

import re
import markdown
from typing import Dict, List, Optional


class FormattingAgent:
    def __init__(self):
        # Markdown extensions for better formatting
        self.md_extensions = [
            'extra',           # Tables, fenced code, footnotes
            'codehilite',      # Code syntax highlighting
            'toc',             # Table of contents
            'tables',          # Table support
            'nl2br',           # Newlines to <br>
        ]

    def format_blog(self, content: str, title: str = "") -> Dict:
        """
        Main formatting pipeline

        Args:
            content: Raw markdown content
            title: Blog title

        Returns:
            Dict with formatted content, TOC, reading time, etc.
        """
        # Content may arrive as markdown OR as already-rendered HTML (e.g. from a
        # WYSIWYG editor or a previous formatting pass). The markdown-specific
        # cleaning would corrupt HTML (it rewrites '#' so it even breaks
        # `href="#anchor"` links), so only apply it to genuine markdown.
        is_html = self._looks_like_html(content)
        cleaned = content if is_html else self._clean_content(content)

        # Generate table of contents
        toc = self._generate_toc(cleaned)

        # Calculate reading time
        reading_time = self._calculate_reading_time(cleaned)

        # Convert to HTML
        html_content = self._markdown_to_html(cleaned)

        # Extract headings structure
        headings = self._extract_headings(cleaned)

        # Count statistics
        stats = self._calculate_stats(cleaned)

        return {
            "original_markdown": content,
            "formatted_markdown": cleaned,
            "html": html_content,
            "toc": toc,
            "toc_html": self._toc_to_html(toc),
            "reading_time_minutes": reading_time,
            "reading_time_text": f"{reading_time} min read",
            "headings": headings,
            "statistics": stats,
            "has_code_blocks": self._has_code_blocks(content),
            "has_images": self._has_images(content),
            "has_tables": self._has_tables(content)
        }

    def _clean_content(self, content: str) -> str:
        """Clean and normalize markdown content"""
        # Remove excessive blank lines (more than 2 consecutive)
        content = re.sub(r'\n{3,}', '\n\n', content)

        # Ensure consistent heading spacing
        content = re.sub(r'(#{1,6})\s*', r'\1 ', content)

        # Fix bullet point spacing
        content = re.sub(r'^\s*[-*+]\s+', '- ', content, flags=re.MULTILINE)

        # Ensure proper spacing around headings
        content = re.sub(r'\n(#{1,6})', r'\n\n\1', content)
        content = re.sub(r'(#{1,6}.*)\n(?!\n)', r'\1\n\n', content)

        # Remove trailing whitespace
        content = '\n'.join(line.rstrip() for line in content.split('\n'))

        return content.strip()

    def _generate_toc(self, content: str) -> List[Dict]:
        """Generate table of contents from headings (markdown OR HTML).

        Markdown `#` headings are preferred. If none are found, the content is
        treated as HTML and headings are extracted from `<h1>`-`<h6>` tags so a
        TOC is still produced (previously HTML-bodied posts lost their TOC
        entirely because only markdown headings were recognized).
        """
        toc = []
        heading_pattern = re.compile(r'^(#{1,6})\s+(.+)$', re.MULTILINE)

        for match in heading_pattern.finditer(content):
            level = len(match.group(1))
            text = match.group(2).strip()

            # Generate slug for anchor
            slug = self._slugify(text)

            toc.append({
                "level": level,
                "text": text,
                "slug": slug
            })

        if not toc:
            html_heading_pattern = re.compile(
                r'<h([1-6])\b[^>]*>(.*?)</h\1>', re.IGNORECASE | re.DOTALL
            )
            for match in html_heading_pattern.finditer(content):
                level = int(match.group(1))
                # Strip any inner tags to get plain heading text
                text = re.sub(r'<[^>]+>', '', match.group(2)).strip()
                if not text:
                    continue
                toc.append({
                    "level": level,
                    "text": text,
                    "slug": self._slugify(text)
                })

        return toc

    def _looks_like_html(self, content: str) -> bool:
        """Heuristic: does this content contain rendered HTML block markup?"""
        if not content:
            return False
        return bool(re.search(
            r'<(article|section|div|p|h[1-6]|ul|ol|table)\b', content, re.IGNORECASE
        ))

    def _add_ids_to_html_headings(self, html: str) -> str:
        """Inject anchor `id`s into HTML headings so TOC links resolve.

        Headings that already carry an `id` are left untouched; the slug matches
        the one produced by `_generate_toc`, so the TOC anchors line up.
        """
        def repl(match):
            level = match.group(1)
            attrs = match.group(2) or ''
            inner = match.group(3)
            if re.search(r'\bid\s*=', attrs, re.IGNORECASE):
                return match.group(0)
            text = re.sub(r'<[^>]+>', '', inner).strip()
            if not text:
                return match.group(0)
            slug = self._slugify(text)
            return f'<h{level}{attrs} id="{slug}">{inner}</h{level}>'

        return re.sub(
            r'<h([1-6])((?:\s+[^>]*)?)>(.*?)</h\1>',
            repl, html, flags=re.IGNORECASE | re.DOTALL
        )

    def _slugify(self, text: str, separator: str = '-') -> str:
        """Convert heading text to URL-friendly slug"""
        # Remove special characters
        slug = re.sub(r'[^\w\s-]', '', text.lower())
        # Replace spaces with separator
        slug = re.sub(r'[\s_]+', separator, slug)
        # Remove leading/trailing separators
        slug = slug.strip(separator)
        return slug

    def _toc_to_html(self, toc: List[Dict]) -> str:
        """Convert TOC to HTML list"""
        if not toc:
            return ""

        html = '<nav class="toc">\n<h4>Table of Contents</h4>\n<ul>\n'

        for item in toc:
            indent = "  " * (item['level'] - 1)
            html += f'{indent}<li class="toc-level-{item["level"]}">'
            html += f'<a href="#{item["slug"]}">{item["text"]}</a></li>\n'

        html += '</ul>\n</nav>'
        return html

    def _calculate_reading_time(self, content: str) -> int:
        """Calculate estimated reading time in minutes"""
        # Average reading speed: 200-250 words per minute
        words_per_minute = 200

        # Count words (excluding code blocks)
        text_only = re.sub(r'```[\s\S]*?```', '', content)
        text_only = re.sub(r'`[^`]+`', '', text_only)

        word_count = len(text_only.split())

        # Minimum 1 minute
        return max(1, round(word_count / words_per_minute))

    def _markdown_to_html(self, content: str) -> str:
        """Convert markdown to HTML with proper formatting.

        If the content is already HTML (from a WYSIWYG editor or an earlier
        formatting pass) it is NOT re-run through the markdown converter — doing
        so double-wrapped it in nested <article> tags and mangled it. Instead we
        just ensure headings have anchor ids and wrap once if not already wrapped.
        """
        if self._looks_like_html(content):
            html = self._add_ids_to_html_headings(content)
            if '<article' not in html.lower():
                html = f'<article class="blog-content formatted">\n{html}\n</article>'
            return html

        try:
            html = markdown.markdown(
                content,
                extensions=['extra', 'codehilite', 'tables', 'toc'],
                extension_configs={
                    'codehilite': {
                        'css_class': 'highlight',
                        'linenums': False
                    },
                    'toc': {
                        'permalink': False,
                        'slugify': self._slugify
                    }
                }
            )

            # Wrap in article tag with proper classes
            html = f'<article class="blog-content formatted">\n{html}\n</article>'

            return html

        except Exception as e:
            print(f"Markdown conversion error: {e}")
            # Fallback: basic conversion
            return f'<article class="blog-content">{content.replace(chr(10), "<br>")}</article>'

    def _extract_headings(self, content: str) -> List[Dict]:
        """Extract all headings with their hierarchy"""
        headings = []
        pattern = re.compile(r'^(#{1,6})\s+(.+)$', re.MULTILINE)

        for match in pattern.finditer(content):
            level = len(match.group(1))
            text = match.group(2).strip()

            headings.append({
                "level": level,
                "text": text,
                "tag": f"h{level}"
            })

        return headings

    def _calculate_stats(self, content: str) -> Dict:
        """Calculate content statistics"""
        # Remove code blocks for accurate text stats
        text_only = re.sub(r'```[\s\S]*?```', '', content)
        text_only = re.sub(r'`[^`]+`', '', text_only)

        words = text_only.split()
        sentences = re.split(r'[.!?]+', text_only)
        paragraphs = [p for p in text_only.split('\n\n') if p.strip()]

        # Count headings by level
        headings_count = {}
        for i in range(1, 7):
            pattern = f'^{"#" * i}\\s+'
            count = len(re.findall(pattern, content, re.MULTILINE))
            if count > 0:
                headings_count[f'h{i}'] = count

        return {
            "word_count": len(words),
            "sentence_count": len([s for s in sentences if s.strip()]),
            "paragraph_count": len(paragraphs),
            "character_count": len(text_only),
            "headings_count": headings_count,
            "avg_words_per_sentence": round(len(words) / max(1, len(sentences)), 1)
        }

    def _has_code_blocks(self, content: str) -> bool:
        """Check if content contains code blocks"""
        return bool(re.search(r'```[\s\S]*?```|`[^`]+`', content))

    def _has_images(self, content: str) -> bool:
        """Check if content contains images"""
        return bool(re.search(r'!\[.*?\]\(.*?\)', content))

    def _has_tables(self, content: str) -> bool:
        """Check if content contains tables"""
        return bool(re.search(r'\|.*\|.*\|', content))

    def add_toc_to_content(self, content: str, position: str = "top") -> str:
        """Insert table of contents into the content"""
        toc = self._generate_toc(content)

        if not toc:
            return content

        toc_markdown = "## Table of Contents\n\n"
        for item in toc:
            indent = "  " * (item['level'] - 1)
            toc_markdown += f"{indent}- [{item['text']}](#{item['slug']})\n"
        toc_markdown += "\n---\n\n"

        if position == "top":
            # Insert after the first heading (title)
            first_heading = re.search(r'^#\s+.+\n', content)
            if first_heading:
                insert_pos = first_heading.end()
                return content[:insert_pos] + "\n" + toc_markdown + content[insert_pos:]
            return toc_markdown + content
        else:
            return content + "\n\n" + toc_markdown

    def optimize_headings(self, content: str) -> str:
        """Ensure proper heading hierarchy (no skipped levels)"""
        lines = content.split('\n')
        optimized_lines = []
        current_level = 0

        for line in lines:
            heading_match = re.match(r'^(#{1,6})\s+(.+)$', line)

            if heading_match:
                level = len(heading_match.group(1))
                text = heading_match.group(2)

                # Don't skip levels
                if current_level == 0:
                    # First heading should be h1 or h2
                    new_level = min(level, 2)
                elif level > current_level + 1:
                    # Prevent skipping (e.g., h2 -> h4)
                    new_level = current_level + 1
                else:
                    new_level = level

                current_level = new_level
                optimized_lines.append(f"{'#' * new_level} {text}")
            else:
                optimized_lines.append(line)

        return '\n'.join(optimized_lines)


# =========================================
# USAGE EXAMPLE
# =========================================
if __name__ == "__main__":
    agent = FormattingAgent()

    sample_content = """
# My Blog Title

This is the introduction paragraph.

## Section One

Some content here with **bold** and *italic* text.

### Subsection 1.1

- Bullet point 1
- Bullet point 2

## Section Two

More content with a code block:

```python
print("Hello World")
```

## Conclusion

Final thoughts here.
"""

    result = agent.format_blog(sample_content, "My Blog Title")

    print("=== READING TIME ===")
    print(result['reading_time_text'])

    print("\n=== TABLE OF CONTENTS ===")
    for item in result['toc']:
        print(f"{'  ' * (item['level']-1)}- {item['text']}")

    print("\n=== STATISTICS ===")
    print(result['statistics'])
