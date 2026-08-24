"""Public, visitor-facing site routes.

Everything here is served to anonymous traffic, which makes it the part of the
application most exposed to both load and abuse. Two properties matter more
here than anywhere else.

**Cost control.** ``/semantic-search`` spends a Gemini embedding call plus an
LLM call per request, and ``/comment`` spends a moderation call per submission.
Both were unauthenticated and unthrottled, so a trivial loop drained the API
quota -- not hypothetical for this project, which already had to change models
over rate-limit errors under single-developer load. Every endpoint that costs
money or writes data now carries an explicit per-IP limit.

**Read amplification.** These pages are identical for every visitor, so each
uncached view spent Firestore reads against a hard daily quota. The data layer
caches, and these responses carry ``Cache-Control`` so a repeat visitor and any
CDN in front of the app can skip the round trip entirely.
"""
from flask import Blueprint, current_app, render_template, abort, request, redirect, url_for, jsonify
from app.core.extensions import limiter
from app.core.logging import get_logger
from app.core.sanitize import strip_all_html
from app.firebase.firestore_service import FirestoreService
from app.utils.parallel import run_parallel_simple
from app.utils.date_utils import utcnow
import markdown
import math
import re as _re

logger = get_logger(__name__)

# Ceilings on visitor-submitted text. Applied before the value reaches the data
# layer or an AI call: an unbounded field is both a storage problem and, on the
# AI paths, a way to inflate token spend per request.
MAX_NAME_LENGTH = 100
MAX_EMAIL_LENGTH = 254        # RFC 5321 maximum
MAX_SUBJECT_LENGTH = 200
MAX_MESSAGE_LENGTH = 5000
MAX_COMMENT_LENGTH = 5000
MAX_SEARCH_QUERY_LENGTH = 200

_EMAIL_RE = _re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]{2,}$')


def _clean_field(value, max_length):
    """Trim, bound and strip markup from one visitor-supplied field."""
    return strip_all_html((value or '').strip())[:max_length]

site_bp = Blueprint('site_bp', __name__, url_prefix='/site')
db_service = FirestoreService()


@site_bp.after_request
def add_cache_headers(response):
    """Add browser cache headers for public site pages to reduce server load."""
    if response.status_code == 200 and request.method == 'GET':
        if 'text/html' in response.content_type:
            response.headers['Cache-Control'] = 'public, max-age=60, stale-while-revalidate=120'
        elif 'application/json' in response.content_type:
            response.headers['Cache-Control'] = 'public, max-age=30, stale-while-revalidate=60'
    return response


class _SlugRedirect(Exception):
    """Raised when a user_id URL should redirect to the slug URL."""
    def __init__(self, slug):
        self.slug = slug


@site_bp.errorhandler(_SlugRedirect)
def _handle_slug_redirect(e):
    """301 redirect from /site/<user_id>/... to /site/<slug>/..."""
    new_path = _re.sub(
        r'^/site/[^/]+',
        f'/site/{e.slug}',
        request.full_path.rstrip('?')
    )
    return redirect(new_path, code=301)


def _get_blog_text_content(blog):
    """Extract searchable text content from a blog post"""
    content = blog.get('content', '')
    if isinstance(content, dict):
        return content.get('body', '') or content.get('markdown', '') or content.get('text', '')
    return str(content) if content else ''


def _render_site_404(user_id, settings):
    """
    Render the site-specific 404 page with proper styling.
    Returns a tuple of (rendered template, status code).
    """
    try:
        categories = db_service.get_all_categories(user_id=user_id)
        return render_template(
            'site/site_404.html',
            settings=settings,
            categories=categories,
            user_id=user_id
        ), 404
    except Exception:
        abort(404)


def _resolve_site(site_identifier):
    """
    Resolve site identifier (slug or user_id) to actual user_id and settings.
    Returns (user_id, settings) or aborts with 404 if not found.
    If accessed via user_id but a slug exists, raises _SlugRedirect so the
    route can 301-redirect to the canonical slug URL.
    """
    user_id, settings = db_service.resolve_site_identifier(site_identifier)
    if not user_id:
        abort(404)

    # Canonical redirect: if accessed via user_id but slug exists, redirect
    slug = settings.get('site_slug', '')
    if slug and site_identifier == user_id and site_identifier != slug:
        raise _SlugRedirect(slug)

    return user_id, settings


# ---------------------------------------------------
# PUBLIC SITE ROUTES (No authentication required)
# ---------------------------------------------------

@site_bp.route('/<site_identifier>')
def site_home(site_identifier):
    """Public site homepage - displays published blogs"""
    try:
        user_id, settings = _resolve_site(site_identifier)

        posts_limit = settings.get('posts_per_page', 10)
        featured_post_id = settings.get('featured_post_id')

        queries = [
            (db_service.get_published_blogs, (user_id, posts_limit)),
            (db_service.get_all_categories, (user_id,)),
            (db_service.get_published_blogs, (user_id, 100)),
        ]
        if featured_post_id:
            # The hero card renders a title, image, category, date and a link --
            # site_home.html never touches the body. Unprojected this single
            # document read measured 3986 ms and was the slowest thing on the
            # public homepage.
            from app.repositories._helpers import BLOG_CARD_FIELDS

            queries.append((
                db_service.get_published_blog_by_id,
                (featured_post_id, BLOG_CARD_FIELDS),
            ))

        results = run_parallel_simple(queries, max_workers=4)

        published_blogs = results[0] or []
        categories = results[1] or []
        slider_blogs = results[2] or []
        featured_post = results[3] if featured_post_id else None

        return render_template(
            'site/site_home.html',
            settings=settings,
            blogs=published_blogs,
            slider_blogs=slider_blogs,
            categories=categories,
            featured_post=featured_post,
            user_id=user_id
        )

    except Exception:
        logger.exception("Site Home Error")
        abort(404)


@site_bp.route('/<site_identifier>/post/<slug_or_id>')
def site_post(site_identifier, slug_or_id):
    """Single blog post view - supports both slug and ID for backwards compatibility"""
    try:
        user_id, settings = _resolve_site(site_identifier)

        # Try slug lookup first (for SEO-friendly URLs)
        result = db_service.get_published_blog_by_slug(user_id, slug_or_id)

        if result:
            if result.get('redirect'):
                return redirect(
                    url_for('site_bp.site_post', site_identifier=site_identifier, slug_or_id=result['new_slug']),
                    code=301
                )
            blog = result['blog']
        else:
            blog = db_service.get_published_blog_by_id(slug_or_id)

        if not blog:
            return _render_site_404(user_id, settings)

        if blog.get('site_owner_id') != user_id and blog.get('author_id') != user_id:
            return _render_site_404(user_id, settings)

        # Process content for display
        content = blog.get('content', '')
        if isinstance(content, dict):
            html_content = content.get('html', '')
            if not html_content:
                md_content = content.get('markdown') or content.get('body') or ''
                html_content = markdown.markdown(md_content, extensions=['extra', 'tables', 'toc'])
            blog['html_content'] = html_content
            blog['toc'] = content.get('toc', [])
            blog['toc_html'] = content.get('toc_html', '')
        else:
            blog['html_content'] = markdown.markdown(str(content), extensions=['extra', 'tables'])
            blog['toc'] = []
            blog['toc_html'] = ''

        # Fetch related blogs and categories in parallel
        def _get_related_blogs():
            if blog.get('category'):
                all_published = db_service.get_published_blogs(user_id, limit=10)
                return [
                    b for b in all_published
                    if b.get('category') == blog.get('category') and b.get('id') != blog.get('id')
                ][:3]
            return []

        results = run_parallel_simple([
            (_get_related_blogs, ()),
            (db_service.get_all_categories, (user_id,)),
        ], max_workers=2)

        related_blogs = results[0] or []
        categories = results[1] or []

        return render_template(
            'site/site_post.html',
            settings=settings,
            blog=blog,
            related_blogs=related_blogs,
            categories=categories,
            user_id=user_id
        )

    except Exception:
        logger.exception("Site Post Error")
        abort(404)


@site_bp.route('/<site_identifier>/about')
def site_about(site_identifier):
    """About page with site description"""
    try:
        user_id, settings = _resolve_site(site_identifier)

        results = run_parallel_simple([
            (db_service.get_published_blogs, (user_id, 100)),
            (db_service.get_all_categories, (user_id,)),
        ], max_workers=2)

        published_blogs = results[0] or []
        categories = results[1] or []

        return render_template(
            'site/site_about.html',
            settings=settings,
            published_count=len(published_blogs),
            categories_count=len(categories),
            categories=categories,
            user_id=user_id
        )

    except Exception:
        logger.exception("Site About Error")
        abort(404)


@site_bp.route('/<site_identifier>/category/<category_name>')
def site_category(site_identifier, category_name):
    """Filter blogs by category"""
    try:
        user_id, settings = _resolve_site(site_identifier)

        posts_limit = settings.get('posts_per_page', 10)

        results = run_parallel_simple([
            (db_service.get_published_blogs, (user_id, posts_limit)),
            (db_service.get_all_categories, (user_id,)),
        ], max_workers=2)

        all_published = results[0] or []
        categories = results[1] or []

        filtered_blogs = [
            b for b in all_published
            if b.get('category', '').lower() == category_name.lower()
        ]

        return render_template(
            'site/site_home.html',
            settings=settings,
            blogs=filtered_blogs,
            categories=categories,
            user_id=user_id,
            current_category=category_name
        )

    except Exception:
        logger.exception("Site Category Error")
        abort(404)


@site_bp.route('/<site_identifier>/blog')
def site_blog(site_identifier):
    """Dedicated blog listing page with pagination and search"""
    try:
        user_id, settings = _resolve_site(site_identifier)

        page = request.args.get('page', 1, type=int)
        per_page = settings.get('posts_per_page', 12)
        category = request.args.get('category', None)
        search_query = request.args.get('search', '').strip()

        results = run_parallel_simple([
            (db_service.get_published_blogs, (user_id, 100)),
        ], max_workers=1)

        all_blogs = results[0] or []

        # Build categories only from published blogs
        category_counts = {}
        for blog in all_blogs:
            cat = blog.get('category', '').strip()
            if cat:
                category_counts[cat] = category_counts.get(cat, 0) + 1
        categories = [{'name': name, 'count': count} for name, count in category_counts.items()]
        categories.sort(key=lambda c: c['count'], reverse=True)

        total_posts = len(all_blogs)

        if search_query:
            search_lower = search_query.lower()
            all_blogs = [
                b for b in all_blogs
                if search_lower in b.get('title', '').lower()
                or search_lower in _get_blog_text_content(b).lower()
                or search_lower in b.get('category', '').lower()
            ]

        if category:
            all_blogs = [
                b for b in all_blogs
                if b.get('category', '').lower() == category.lower()
            ]

        filtered_count = len(all_blogs)
        total_pages = math.ceil(filtered_count / per_page) if filtered_count > 0 else 1
        start = (page - 1) * per_page
        paginated_blogs = all_blogs[start:start + per_page]

        return render_template(
            'site/site_blog.html',
            settings=settings,
            blogs=paginated_blogs,
            categories=categories,
            current_category=category,
            search_query=search_query,
            current_page=page,
            total_pages=total_pages,
            total_posts=total_posts,
            per_page=per_page,
            user_id=user_id
        )

    except Exception:
        logger.exception("Site Blog Error")
        abort(404)


@site_bp.route('/<site_identifier>/contact')
def site_contact(site_identifier):
    """Contact page"""
    try:
        user_id, settings = _resolve_site(site_identifier)
        categories = db_service.get_all_categories(user_id=user_id)

        return render_template(
            'site/site_contact.html',
            settings=settings,
            categories=categories,
            user_id=user_id
        )

    except Exception:
        logger.exception("Site Contact Error")
        abort(404)


@site_bp.route('/<site_identifier>/contact', methods=['POST'])
@limiter.limit('5 per minute; 20 per hour')
def site_contact_submit(site_identifier):
    """Handle a contact form submission from an anonymous visitor.

    Fields are stripped of markup before storage: they are rendered back to the
    site owner in the leads dashboard, so an unsanitised message body is a
    stored-XSS route from an anonymous visitor straight into an admin session.
    """
    user_id, _ = _resolve_site(site_identifier)

    name = _clean_field(request.form.get('name'), MAX_NAME_LENGTH)
    email = _clean_field(request.form.get('email'), MAX_EMAIL_LENGTH)
    subject = _clean_field(request.form.get('subject'), MAX_SUBJECT_LENGTH)
    message = _clean_field(request.form.get('message'), MAX_MESSAGE_LENGTH)

    if not name or not message or not _EMAIL_RE.match(email):
        return redirect(url_for(
            'site_bp.site_contact', site_identifier=site_identifier, error=1
        ))

    try:
        db_service.save_contact_submission(user_id, {
            'name': name, 'email': email, 'subject': subject, 'message': message,
        })
    except Exception:
        logger.exception('Contact submission failed', extra={'site': user_id})
        return redirect(url_for(
            'site_bp.site_contact', site_identifier=site_identifier, error=1
        ))

    logger.info('Contact submission received', extra={'site': user_id})
    return redirect(url_for(
        'site_bp.site_contact', site_identifier=site_identifier, success=1
    ))


@site_bp.route('/<site_identifier>/subscribe', methods=['POST'])
@limiter.limit('5 per minute; 20 per hour')
def site_subscribe(site_identifier):
    """Subscribe an anonymous visitor to the site's newsletter.

    Throttled because each accepted address is a write, and an unthrottled
    subscribe endpoint is a way to fill another site's list with addresses its
    owner never consented to mail -- which is how a sending domain earns a spam
    reputation.
    """
    user_id, _ = _resolve_site(site_identifier)
    email = _clean_field(request.form.get('email'), MAX_EMAIL_LENGTH)

    # A real format check, not `'@' in email`: the address is later used as a
    # mail recipient, and `a@b` passed the old test.
    if not _EMAIL_RE.match(email):
        return jsonify({
            'success': False, 'message': 'Please enter a valid email address'
        }), 400

    try:
        doc_id, is_new = db_service.save_newsletter_subscriber(user_id, email)
    except Exception:
        logger.exception('Newsletter subscribe failed', extra={'site': user_id})
        return jsonify({'success': False, 'message': 'Subscription failed'}), 500

    if not doc_id:
        return jsonify({'success': False, 'message': 'Subscription failed'}), 500

    return jsonify({
        'success': True,
        'is_new': is_new,
        'message': 'Subscribed successfully!' if is_new else 'Already subscribed!',
    })


@site_bp.route('/<site_identifier>/semantic-search', methods=['POST'])
@limiter.limit(lambda: current_app.config.get('RATELIMIT_PUBLIC_AI', '10 per minute'))
def site_semantic_search(site_identifier):
    """
    Semantic search API endpoint.
    Accepts a JSON body with 'query' field and returns semantically relevant blogs.
    """
    try:
        user_id, _ = _resolve_site(site_identifier)
        data = request.get_json(silent=True) or {}
        # Bounded before it reaches an embedding call: query length drives
        # token spend, and the field was previously unbounded.
        query = _clean_field(data.get('query'), MAX_SEARCH_QUERY_LENGTH)

        if not query:
            return jsonify({'success': False, 'message': 'Query is required', 'results': []}), 400

        if len(query) < 2:
            return jsonify({'success': False, 'message': 'Query too short', 'results': []}), 400

        # Import here to avoid circular imports
        from app.agents.semantic_search_agent import SemanticSearchAgent

        search_agent = SemanticSearchAgent()
        results, insights = search_agent.search(user_id, query, top_k=6, include_insights=True)

        # Format results for frontend
        formatted_results = []
        for result in results:
            formatted_results.append({
                'id': result['id'],
                'title': result['title'],
                'category': result.get('category', ''),
                'excerpt': result.get('excerpt', ''),
                'cover_image': result.get('cover_image', ''),
                'score': result.get('score', 0),
                'match_reason': result.get('match_reason', ''),
                'url': url_for('site_bp.site_post', site_identifier=site_identifier, slug_or_id=result.get('slug') or result['id'])
            })

        return jsonify({
            'success': True,
            'query': query,
            'results': formatted_results,
            'count': len(formatted_results),
            'insights': insights
        })

    except Exception:
        logger.exception('Semantic search failed', extra={'site': site_identifier})
        return jsonify({'success': False, 'message': 'Search failed', 'results': []}), 500


@site_bp.route('/<site_identifier>/robots.txt')
def site_robots_txt(site_identifier):
    """
    Generate dynamic robots.txt based on site settings.
    - If custom content is set, use that
    - Otherwise, auto-generate based on indexing settings
    """
    try:
        from flask import Response

        user_id, settings = _resolve_site(site_identifier)
        seo_settings = settings.get('seo', {})

        # Check for custom robots.txt content
        custom_robots = seo_settings.get('robots_txt_custom', '').strip()
        if custom_robots:
            return Response(custom_robots, mimetype='text/plain')

        # Auto-generate based on indexing settings
        indexing_enabled = seo_settings.get('indexing_enabled', True)

        # Use the site_identifier (slug) in the URL for cleaner sitemap reference
        if indexing_enabled:
            robots_content = f"""User-agent: *
Allow: /

# Sitemap
Sitemap: {request.host_url}site/{site_identifier}/sitemap.xml
"""
        else:
            robots_content = """User-agent: *
Disallow: /

# This site has disabled search engine indexing
"""

        return Response(robots_content, mimetype='text/plain')

    except Exception:
        logger.exception("Robots.txt Error")
        # Default permissive robots.txt on error
        return Response("User-agent: *\nAllow: /\n", mimetype='text/plain')


@site_bp.route('/<site_identifier>/sitemap.xml')
def site_sitemap(site_identifier):
    """
    Generate dynamic XML sitemap for SEO.
    Includes all published blog posts and main pages.
    """
    try:
        from flask import Response

        user_id, settings = _resolve_site(site_identifier)
        seo_settings = settings.get('seo', {})

        # If indexing is disabled, return empty sitemap
        if not seo_settings.get('indexing_enabled', True):
            return Response(
                '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>',
                mimetype='application/xml'
            )

        base_url = request.host_url.rstrip('/')

        # Start XML
        xml_parts = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        ]

        # Add main pages - use site_identifier for clean URLs
        main_pages = [
            ('', 1.0, 'daily'),      # Home
            ('/blog', 0.9, 'daily'),  # Blog listing
            ('/about', 0.7, 'monthly'),
            ('/contact', 0.5, 'monthly')
        ]

        for path, priority, changefreq in main_pages:
            xml_parts.append(f'''  <url>
    <loc>{base_url}/site/{site_identifier}{path}</loc>
    <changefreq>{changefreq}</changefreq>
    <priority>{priority}</priority>
  </url>''')

        # Add all published blog posts. Projected: the sitemap emits a slug, a
        # document id and a lastmod date, so fetching 500 whole posts -- each
        # carrying its rendered body and semantic-search embedding vector --
        # transferred megabytes to produce a few kilobytes of XML. This is a
        # public, crawler-facing endpoint, so it is hit far more often than any
        # dashboard page.
        from app.repositories._helpers import BLOG_LIST_FIELDS

        published_blogs = db_service.get_published_blogs(
            user_id, limit=500, fields=BLOG_LIST_FIELDS
        )

        for blog in published_blogs:
            slug_or_id = blog.get('slug') or blog.get('id')
            post_url = f"{base_url}/site/{site_identifier}/post/{slug_or_id}"

            # Format lastmod date
            updated = blog.get('updated_at')
            if updated:
                if hasattr(updated, 'strftime'):
                    lastmod = updated.strftime('%Y-%m-%d')
                else:
                    lastmod = utcnow().strftime('%Y-%m-%d')
            else:
                lastmod = utcnow().strftime('%Y-%m-%d')

            xml_parts.append(f'''  <url>
    <loc>{post_url}</loc>
    <lastmod>{lastmod}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
  </url>''')

        # Add category pages
        categories = db_service.get_all_categories(user_id=user_id)
        for category in categories:
            cat_name = category.get('name', '')
            if cat_name:
                cat_url = f"{base_url}/site/{site_identifier}/category/{cat_name}"
                xml_parts.append(f'''  <url>
    <loc>{cat_url}</loc>
    <changefreq>weekly</changefreq>
    <priority>0.6</priority>
  </url>''')

        xml_parts.append('</urlset>')

        return Response('\n'.join(xml_parts), mimetype='application/xml')

    except Exception:
        logger.exception("Sitemap Error")
        return Response(
            '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>',
            mimetype='application/xml'
        ), 500


@site_bp.route('/<site_identifier>/feed.xml')
def site_rss_feed(site_identifier):
    """
    Generate RSS 2.0 feed for the site.
    Respects RSS settings: posts_count, content_type, include_featured_image.
    """
    try:
        from flask import Response
        import html

        user_id, settings = _resolve_site(site_identifier)
        rss_settings = settings.get('rss', {})

        # Check if RSS is enabled
        if not rss_settings.get('enabled', True):
            return Response(
                '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>Feed Disabled</title></channel></rss>',
                mimetype='application/rss+xml'
            ), 404

        # Get settings
        posts_count = rss_settings.get('posts_count', 20)
        content_type = rss_settings.get('content_type', 'summary')  # 'full' or 'summary'
        include_image = rss_settings.get('include_featured_image', True)

        base_url = request.host_url.rstrip('/')
        site_url = f"{base_url}/site/{site_identifier}"

        # The feed needs each post's body (content_type can be 'full'), but not
        # its embedding vector, outline, seo map or formatting map. Unprojected
        # this endpoint measured 13.2 s of Firestore time across its two
        # queries; BLOG_FEED_FIELDS keeps the body and drops the rest.
        from app.repositories._helpers import BLOG_FEED_FIELDS

        published_blogs = db_service.get_published_blogs(
            user_id, limit=posts_count, fields=BLOG_FEED_FIELDS
        )

        # Build RSS feed
        rss_parts = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/">',
            '<channel>',
            f'  <title>{html.escape(settings.get("site_name", "Blog"))}</title>',
            f'  <link>{site_url}</link>',
            f'  <description>{html.escape(settings.get("site_description", ""))}</description>',
            f'  <language>{settings.get("default_language", "en")}</language>',
            f'  <atom:link href="{site_url}/feed.xml" rel="self" type="application/rss+xml"/>',
        ]

        # Add optional channel elements
        if settings.get('contact_email'):
            rss_parts.append(f'  <managingEditor>{html.escape(settings["contact_email"])}</managingEditor>')

        if settings.get('og_image_url'):
            rss_parts.append(f'''  <image>
    <url>{html.escape(settings["og_image_url"])}</url>
    <title>{html.escape(settings.get("site_name", "Blog"))}</title>
    <link>{site_url}</link>
  </image>''')

        # Add items
        for blog in published_blogs:
            slug_or_id = blog.get('slug') or blog.get('id')
            post_url = f"{site_url}/post/{slug_or_id}"
            title = html.escape(blog.get('title', 'Untitled'))

            # Get content
            content = blog.get('content', '')
            if isinstance(content, dict):
                if content_type == 'full':
                    description = content.get('html', '') or content.get('body', '') or content.get('markdown', '')
                else:
                    # Summary - strip HTML and truncate
                    text = content.get('body', '') or content.get('markdown', '') or content.get('html', '')
                    text = text.replace('<', ' <')  # Add space before tags for better text extraction
                    import re
                    text = re.sub(r'<[^>]+>', '', text)  # Strip HTML
                    text = ' '.join(text.split())  # Normalize whitespace
                    description = text[:300] + '...' if len(text) > 300 else text
            else:
                text = str(content)
                if content_type == 'summary':
                    import re
                    text = re.sub(r'<[^>]+>', '', text)
                    text = ' '.join(text.split())
                    description = text[:300] + '...' if len(text) > 300 else text
                else:
                    description = text

            description = html.escape(description)

            # Format pub date (RFC 822)
            pub_date = blog.get('updated_at') or blog.get('created_at')
            if pub_date:
                if hasattr(pub_date, 'strftime'):
                    pub_date_str = pub_date.strftime('%a, %d %b %Y %H:%M:%S +0000')
                else:
                    pub_date_str = utcnow().strftime('%a, %d %b %Y %H:%M:%S +0000')
            else:
                pub_date_str = utcnow().strftime('%a, %d %b %Y %H:%M:%S +0000')

            # Build item
            rss_parts.append('  <item>')
            rss_parts.append(f'    <title>{title}</title>')
            rss_parts.append(f'    <link>{post_url}</link>')
            rss_parts.append(f'    <guid isPermaLink="true">{post_url}</guid>')
            rss_parts.append(f'    <pubDate>{pub_date_str}</pubDate>')
            rss_parts.append(f'    <description><![CDATA[{description}]]></description>')

            # Category
            if blog.get('category'):
                rss_parts.append(f'    <category>{html.escape(blog["category"])}</category>')

            # Author
            if blog.get('author'):
                rss_parts.append(f'    <author>{html.escape(blog["author"])}</author>')

            # Featured image
            if include_image and blog.get('cover_image'):
                rss_parts.append(f'    <media:content url="{html.escape(blog["cover_image"])}" medium="image"/>')

            rss_parts.append('  </item>')

        rss_parts.append('</channel>')
        rss_parts.append('</rss>')

        return Response('\n'.join(rss_parts), mimetype='application/rss+xml')

    except Exception:
        logger.exception("RSS Feed Error")
        return Response(
            '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>Error</title></channel></rss>',
            mimetype='application/rss+xml'
        ), 500


@site_bp.route('/<site_identifier>/privacy-policy')
def site_privacy_policy(site_identifier):
    """Privacy Policy page"""
    try:

        user_id, settings = _resolve_site(site_identifier)
        legal_settings = settings.get('legal', {})

        # Check if privacy policy is enabled
        if not legal_settings.get('privacy_policy_enabled', True):
            abort(404)

        # Get content and replace placeholders
        content = legal_settings.get('privacy_policy_content', '')
        content = content.replace('{site_name}', settings.get('site_name', 'Our Site'))
        # Use legal contact email if set, otherwise fall back to main contact email
        contact_email = legal_settings.get('contact_email', '') or settings.get('contact_email', '')
        content = content.replace('{contact_email}', contact_email)
        content = content.replace('{date}', utcnow().strftime('%B %d, %Y'))

        # Convert markdown to HTML
        html_content = markdown.markdown(content, extensions=['extra', 'tables'])

        # Get categories for footer
        categories = db_service.get_all_categories(user_id=user_id)

        return render_template(
            'site/site_legal.html',
            settings=settings,
            page_title='Privacy Policy',
            page_content=html_content,
            last_updated=utcnow().strftime('%B %d, %Y'),
            categories=categories,
            user_id=user_id
        )

    except Exception:
        logger.exception("Privacy Policy Error")
        abort(404)


@site_bp.route('/<site_identifier>/terms-of-service')
def site_terms_of_service(site_identifier):
    """Terms of Service page"""
    try:

        user_id, settings = _resolve_site(site_identifier)
        legal_settings = settings.get('legal', {})

        # Check if terms of service is enabled
        if not legal_settings.get('terms_of_service_enabled', True):
            abort(404)

        # Get content and replace placeholders
        content = legal_settings.get('terms_of_service_content', '')
        content = content.replace('{site_name}', settings.get('site_name', 'Our Site'))
        # Use legal contact email if set, otherwise fall back to main contact email
        contact_email = legal_settings.get('contact_email', '') or settings.get('contact_email', '')
        content = content.replace('{contact_email}', contact_email)
        content = content.replace('{date}', utcnow().strftime('%B %d, %Y'))

        # Convert markdown to HTML
        html_content = markdown.markdown(content, extensions=['extra', 'tables'])

        # Get categories for footer
        categories = db_service.get_all_categories(user_id=user_id)

        return render_template(
            'site/site_legal.html',
            settings=settings,
            page_title='Terms of Service',
            page_content=html_content,
            last_updated=utcnow().strftime('%B %d, %Y'),
            categories=categories,
            user_id=user_id
        )

    except Exception:
        logger.exception("Terms of Service Error")
        abort(404)


# ---------------------------------------------------
# COMMENT ROUTES
# ---------------------------------------------------

@site_bp.route('/<site_identifier>/post/<slug_or_id>/comment', methods=['POST'])
@limiter.limit(lambda: current_app.config.get('RATELIMIT_PUBLIC_WRITE', '5 per minute'))
def site_submit_comment(site_identifier, slug_or_id):
    """Accept an anonymous comment, moderated by the AI before publication.

    Rate-limited per IP because each submission spends a Gemini moderation call.
    Unthrottled, this was the cheapest way for anyone to drain the project's AI
    quota -- and the most expensive endpoint to leave open, since a rejected
    comment still costs a full model call.
    """
    try:
        user_id, settings = _resolve_site(site_identifier)

        # Resolve the blog post
        blog = None
        result = db_service.get_published_blog_by_slug(user_id, slug_or_id)
        if result and result.get('blog'):
            blog = result['blog']
        else:
            blog = db_service.get_published_blog_by_id(slug_or_id)

        if not blog:
            return jsonify({"success": False, "error": "Blog post not found"}), 404

        # Parse request
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "Invalid request"}), 400

        # strip_all_html parses the markup rather than pattern-matching it.
        # The previous `re.sub(r'<[^>]+>', '', ...)` left several vectors
        # intact -- an unclosed `<img src=x onerror=alert(1)` has no closing
        # `>` for that pattern to match, and the browser closes it happily.
        name = _clean_field(data.get('name'), MAX_NAME_LENGTH)
        email = _clean_field(data.get('email'), MAX_EMAIL_LENGTH)
        comment_text = _clean_field(data.get('comment'), MAX_COMMENT_LENGTH)

        if not name:
            return jsonify({"success": False, "error": "Name is required"}), 400
        if not _EMAIL_RE.match(email):
            return jsonify({"success": False, "error": "Valid email is required"}), 400
        if not comment_text:
            return jsonify({"success": False, "error": "Comment is required"}), 400

        # AI Moderation (single API call)
        from app.agents.comment_agent import CommentAgent
        agent = CommentAgent()
        moderation = agent.moderate_comment(comment_text, blog.get('title', ''))

        # Build comment document
        ai_action = moderation['action']
        now = utcnow()

        comment_data = {
            'site_owner_id': user_id,
            'blog_id': blog['id'],
            'blog_title': blog.get('title', ''),
            'commenter_name': name,
            'commenter_email': email,
            'original_text': comment_text,
            'moderated_text': moderation['moderated_text'],
            'ai_action': ai_action,
            'ai_reason': moderation.get('reason'),
            'ai_moderated_at': now,
        }

        if ai_action == 'remove':
            comment_data['status'] = 'removed'
            comment_data['display_text'] = comment_text
            comment_data['removed_by'] = 'ai'
            comment_data['removed_at'] = now
            comment_data['removed_reason'] = moderation.get('reason', 'Flagged by AI')
        elif ai_action == 'edit':
            comment_data['status'] = 'published'
            comment_data['display_text'] = moderation['moderated_text']
        else:
            comment_data['status'] = 'published'
            comment_data['display_text'] = comment_text

        doc_id = db_service.create_comment(comment_data)

        if not doc_id:
            return jsonify({"success": False, "error": "Failed to save comment"}), 500

        # Response: never reveal removal to user
        if ai_action == 'remove':
            return jsonify({
                "success": True,
                "status": "moderated",
                "message": "Thank you for your comment!"
            })
        else:
            return jsonify({
                "success": True,
                "status": "published",
                "comment": {
                    "id": doc_id,
                    "commenter_name": name,
                    "display_text": comment_data['display_text'],
                    "created_at": now.isoformat()
                }
            })

    except Exception:
        logger.exception(
            'Comment submission failed',
            extra={'site': site_identifier, 'post': slug_or_id},
        )
        return jsonify({"success": False, "error": "Something went wrong"}), 500


@site_bp.route('/<site_identifier>/post/<slug_or_id>/comments', methods=['GET'])
def site_get_comments(site_identifier, slug_or_id):
    """Fetch published comments for a blog post."""
    try:
        user_id, settings = _resolve_site(site_identifier)

        # Resolve blog
        blog = None
        result = db_service.get_published_blog_by_slug(user_id, slug_or_id)
        if result and result.get('blog'):
            blog = result['blog']
        else:
            blog = db_service.get_published_blog_by_id(slug_or_id)

        if not blog:
            return jsonify({"success": False, "error": "Blog not found"}), 404

        comments = db_service.get_comments_for_blog(blog['id'])

        # Return only public fields (never expose email)
        public_comments = []
        for c in comments:
            created = c.get('created_at')
            if hasattr(created, 'isoformat'):
                created = created.isoformat()
            elif hasattr(created, 'timestamp'):
                from datetime import datetime as _dt
                created = _dt.fromtimestamp(created.timestamp()).isoformat()
            else:
                created = str(created) if created else ''

            public_comments.append({
                'id': c.get('id'),
                'commenter_name': c.get('commenter_name', 'Anonymous'),
                'display_text': c.get('display_text', ''),
                'created_at': created
            })

        return jsonify({"success": True, "comments": public_comments})

    except Exception:
        logger.exception("Error fetching comments")
        return jsonify({"success": True, "comments": []})


# ---------------------------------------------------
# CATCH-ALL ROUTE FOR 404 ON PUBLIC SITE
# Must be defined last to catch all undefined routes
# ---------------------------------------------------

@site_bp.route('/<site_identifier>/<path:undefined_path>')
def site_catch_all(site_identifier, undefined_path):
    """
    Catch-all route for undefined URLs on the public site.
    Returns a custom 404 page that matches the site's styling.
    """
    try:
        user_id, settings = _resolve_site(site_identifier)
        categories = db_service.get_all_categories(user_id=user_id)

        return render_template(
            'site/site_404.html',
            settings=settings,
            categories=categories,
            user_id=user_id
        ), 404

    except Exception:
        logger.exception("Catch-all 404 Error")
        # If site doesn't exist, return generic 404
        abort(404)
