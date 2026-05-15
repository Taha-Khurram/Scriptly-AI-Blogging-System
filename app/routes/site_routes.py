from flask import Blueprint, render_template, abort, request, redirect, url_for, jsonify, make_response
from app.firebase.firestore_service import FirestoreService
from app.utils.parallel import run_parallel_simple
import markdown
import math
import re as _re

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
        ]
        if featured_post_id:
            queries.append((db_service.get_published_blog_by_id, (featured_post_id,)))

        results = run_parallel_simple(queries, max_workers=3)

        published_blogs = results[0] or []
        categories = results[1] or []
        featured_post = results[2] if featured_post_id else None

        return render_template(
            'site/site_home.html',
            settings=settings,
            blogs=published_blogs,
            categories=categories,
            featured_post=featured_post,
            user_id=user_id
        )

    except Exception as e:
        print(f"Site Home Error: {e}")
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

    except Exception as e:
        print(f"Site Post Error: {e}")
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

    except Exception as e:
        print(f"Site About Error: {e}")
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

    except Exception as e:
        print(f"Site Category Error: {e}")
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
            (db_service.get_all_categories, (user_id,)),
        ], max_workers=2)

        all_blogs = results[0] or []
        categories = results[1] or []

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

        total_posts = len(all_blogs)
        total_pages = math.ceil(total_posts / per_page) if total_posts > 0 else 1
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

    except Exception as e:
        print(f"Site Blog Error: {e}")
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

    except Exception as e:
        print(f"Site Contact Error: {e}")
        abort(404)


@site_bp.route('/<site_identifier>/contact', methods=['POST'])
def site_contact_submit(site_identifier):
    """Handle contact form submission"""
    try:
        user_id, _ = _resolve_site(site_identifier)
        data = request.form
        db_service.save_contact_submission(user_id, {
            'name': data.get('name'),
            'email': data.get('email'),
            'subject': data.get('subject'),
            'message': data.get('message')
        })
        # Redirect back with success message
        return redirect(url_for('site_bp.site_contact', site_identifier=site_identifier, success=1))
    except Exception as e:
        print(f"Contact Submit Error: {e}")
        return redirect(url_for('site_bp.site_contact', site_identifier=site_identifier, error=1))


@site_bp.route('/<site_identifier>/subscribe', methods=['POST'])
def site_subscribe(site_identifier):
    """Handle newsletter subscription"""
    try:
        user_id, _ = _resolve_site(site_identifier)
        email = request.form.get('email', '').strip()
        if email and '@' in email:
            doc_id, is_new = db_service.save_newsletter_subscriber(user_id, email)
            if doc_id:
                if is_new:
                    return jsonify({'success': True, 'is_new': True, 'message': 'Subscribed successfully!'})
                else:
                    return jsonify({'success': True, 'is_new': False, 'message': 'Already subscribed!'})
            return jsonify({'success': False, 'message': 'Subscription failed'}), 500
        return jsonify({'success': False, 'message': 'Invalid email'}), 400
    except Exception as e:
        print(f"Subscribe Error: {e}")
        return jsonify({'success': False, 'message': 'Subscription failed'}), 500


@site_bp.route('/<site_identifier>/semantic-search', methods=['POST'])
def site_semantic_search(site_identifier):
    """
    Semantic search API endpoint.
    Accepts a JSON body with 'query' field and returns semantically relevant blogs.
    """
    try:
        user_id, _ = _resolve_site(site_identifier)
        data = request.get_json()
        query = data.get('query', '').strip() if data else ''

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

    except Exception as e:
        print(f"Semantic Search Error: {e}")
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

    except Exception as e:
        print(f"Robots.txt Error: {e}")
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
        from datetime import datetime

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

        # Add all published blog posts
        published_blogs = db_service.get_published_blogs(user_id, limit=500)

        for blog in published_blogs:
            slug_or_id = blog.get('slug') or blog.get('id')
            post_url = f"{base_url}/site/{site_identifier}/post/{slug_or_id}"

            # Format lastmod date
            updated = blog.get('updated_at')
            if updated:
                if hasattr(updated, 'strftime'):
                    lastmod = updated.strftime('%Y-%m-%d')
                else:
                    lastmod = datetime.utcnow().strftime('%Y-%m-%d')
            else:
                lastmod = datetime.utcnow().strftime('%Y-%m-%d')

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

    except Exception as e:
        print(f"Sitemap Error: {e}")
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
        from datetime import datetime
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

        # Get published blogs
        published_blogs = db_service.get_published_blogs(user_id, limit=posts_count)

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
                    pub_date_str = datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S +0000')
            else:
                pub_date_str = datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S +0000')

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

    except Exception as e:
        print(f"RSS Feed Error: {e}")
        return Response(
            '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>Error</title></channel></rss>',
            mimetype='application/rss+xml'
        ), 500


@site_bp.route('/<site_identifier>/privacy-policy')
def site_privacy_policy(site_identifier):
    """Privacy Policy page"""
    try:
        from datetime import datetime

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
        content = content.replace('{date}', datetime.utcnow().strftime('%B %d, %Y'))

        # Convert markdown to HTML
        html_content = markdown.markdown(content, extensions=['extra', 'tables'])

        # Get categories for footer
        categories = db_service.get_all_categories(user_id=user_id)

        return render_template(
            'site/site_legal.html',
            settings=settings,
            page_title='Privacy Policy',
            page_content=html_content,
            last_updated=datetime.utcnow().strftime('%B %d, %Y'),
            categories=categories,
            user_id=user_id
        )

    except Exception as e:
        print(f"Privacy Policy Error: {e}")
        abort(404)


@site_bp.route('/<site_identifier>/terms-of-service')
def site_terms_of_service(site_identifier):
    """Terms of Service page"""
    try:
        from datetime import datetime

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
        content = content.replace('{date}', datetime.utcnow().strftime('%B %d, %Y'))

        # Convert markdown to HTML
        html_content = markdown.markdown(content, extensions=['extra', 'tables'])

        # Get categories for footer
        categories = db_service.get_all_categories(user_id=user_id)

        return render_template(
            'site/site_legal.html',
            settings=settings,
            page_title='Terms of Service',
            page_content=html_content,
            last_updated=datetime.utcnow().strftime('%B %d, %Y'),
            categories=categories,
            user_id=user_id
        )

    except Exception as e:
        print(f"Terms of Service Error: {e}")
        abort(404)


# ---------------------------------------------------
# COMMENT ROUTES
# ---------------------------------------------------

@site_bp.route('/<site_identifier>/post/<slug_or_id>/comment', methods=['POST'])
def site_submit_comment(site_identifier, slug_or_id):
    """Handle public comment submission with AI moderation."""
    import re as _re
    from datetime import datetime

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

        name = (data.get('name', '') or '').strip()[:100]
        email = (data.get('email', '') or '').strip()[:100]
        comment_text = (data.get('comment', '') or '').strip()[:5000]

        # Validate
        if not name or len(name) < 1:
            return jsonify({"success": False, "error": "Name is required"}), 400
        if not email or not _re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
            return jsonify({"success": False, "error": "Valid email is required"}), 400
        if not comment_text or len(comment_text) < 1:
            return jsonify({"success": False, "error": "Comment is required"}), 400

        # Sanitize: strip HTML tags
        comment_text = _re.sub(r'<[^>]+>', '', comment_text)

        # AI Moderation (single API call)
        from app.agents.comment_agent import CommentAgent
        agent = CommentAgent()
        moderation = agent.moderate_comment(comment_text, blog.get('title', ''))

        # Build comment document
        ai_action = moderation['action']
        now = datetime.utcnow()

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

    except Exception as e:
        print(f"Comment submission error: {e}")
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

    except Exception as e:
        print(f"Error fetching comments: {e}")
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

    except Exception as e:
        print(f"Catch-all 404 Error: {e}")
        # If site doesn't exist, return generic 404
        abort(404)
