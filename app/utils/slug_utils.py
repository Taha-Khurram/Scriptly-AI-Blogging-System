"""
Slug and Permalink Utilities

Provides functions for generating URL-friendly slugs and building permalinks
based on configurable permalink structures.
"""

import re
from datetime import datetime
from app.utils.date_utils import utcnow

# Available permalink structures with display pattern and example
PERMALINK_STRUCTURES = [
    ('post-name', '/post/{slug}', 'my-blog-title'),
    ('date-post-name', '/post/{year}/{month}/{day}/{slug}', '2024/01/15/my-blog-title'),
    ('category-post-name', '/post/{category}/{slug}', 'technology/my-blog-title'),
    ('numeric', '/post/{id}', '123'),
]


def generate_slug(title):
    """
    Convert a title to a URL-friendly slug.

    Args:
        title: The title to convert

    Returns:
        A lowercase, hyphen-separated slug (max 100 chars)

    Example:
        "My Blog Title!" -> "my-blog-title"
        "Hello   World" -> "hello-world"
    """
    if not title:
        return 'untitled'

    slug = title.lower().strip()
    # Remove non-word characters except spaces and hyphens
    slug = re.sub(r'[^\w\s-]', '', slug)
    # Replace spaces and underscores with hyphens
    slug = re.sub(r'[\s_]+', '-', slug)
    # Remove multiple consecutive hyphens
    slug = re.sub(r'-+', '-', slug)
    # Remove leading/trailing hyphens and limit length
    slug = slug.strip('-')[:100]

    return slug if slug else 'untitled'


def ensure_unique_slug(base_slug, existing_slugs):
    """
    Ensure slug uniqueness by adding a numeric suffix if needed.

    Args:
        base_slug: The initial slug to check
        existing_slugs: Set or list of existing slugs to check against

    Returns:
        A unique slug (possibly with -2, -3, etc. suffix)

    Example:
        ensure_unique_slug("my-post", {"my-post"}) -> "my-post-2"
        ensure_unique_slug("my-post", {"my-post", "my-post-2"}) -> "my-post-3"
    """
    if not existing_slugs or base_slug not in existing_slugs:
        return base_slug

    counter = 2
    while f"{base_slug}-{counter}" in existing_slugs:
        counter += 1

    return f"{base_slug}-{counter}"


def build_permalink(blog, structure='post-name', category_base='category'):
    """
    Build a full permalink path based on the structure setting.

    Args:
        blog: Blog dict with slug, created_at, category, numeric_id fields
        structure: One of 'post-name', 'date-post-name', 'category-post-name', 'numeric'
        category_base: URL base for categories (not used in post permalinks but for consistency)

    Returns:
        The permalink path string

    Example:
        build_permalink({'slug': 'my-post'}, 'post-name') -> "/post/my-post"
        build_permalink({'slug': 'my-post', 'created_at': datetime(2024,1,15)}, 'date-post-name')
            -> "/post/2024/01/15/my-post"
    """
    slug = blog.get('slug', '')

    if structure == 'date-post-name':
        created = blog.get('created_at')
        if created is None:
            created = utcnow()
        elif isinstance(created, str):
            try:
                created = datetime.fromisoformat(created.replace('Z', '+00:00'))
            except:
                created = utcnow()
        return f"/post/{created.year}/{created.month:02d}/{created.day:02d}/{slug}"

    elif structure == 'category-post-name':
        category = blog.get('category', 'uncategorized')
        cat_slug = generate_slug(category)
        return f"/post/{cat_slug}/{slug}"

    elif structure == 'numeric':
        numeric_id = blog.get('numeric_id') or blog.get('id', '')
        return f"/post/{numeric_id}"

    else:  # post-name (default)
        return f"/post/{slug}"


def build_category_url(category_name, category_base='category'):
    """
    Build a category URL with custom base.

    Args:
        category_name: The category name
        category_base: URL base for categories

    Returns:
        The category URL path

    Example:
        build_category_url("Technology", "topics") -> "/topics/technology"
    """
    cat_slug = generate_slug(category_name)
    return f"/{category_base}/{cat_slug}"


def build_tag_url(tag_name, tag_base='tag'):
    """
    Build a tag URL with custom base.

    Args:
        tag_name: The tag name
        tag_base: URL base for tags

    Returns:
        The tag URL path

    Example:
        build_tag_url("Python", "labels") -> "/labels/python"
    """
    tag_slug = generate_slug(tag_name)
    return f"/{tag_base}/{tag_slug}"


def validate_slug(slug):
    """
    Validate and sanitize a user-provided slug.

    Args:
        slug: The slug to validate

    Returns:
        Sanitized slug or None if invalid
    """
    if not slug:
        return None

    # Only allow lowercase alphanumeric and hyphens
    sanitized = re.sub(r'[^a-z0-9-]', '', slug.lower())
    # Remove multiple consecutive hyphens
    sanitized = re.sub(r'-+', '-', sanitized)
    # Remove leading/trailing hyphens
    sanitized = sanitized.strip('-')

    return sanitized[:100] if sanitized else None
