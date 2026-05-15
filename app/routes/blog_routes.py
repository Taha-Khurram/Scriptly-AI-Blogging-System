from flask import Blueprint, render_template, request, jsonify, url_for, session, redirect, abort
from app.agents.blog_agent import BlogAgent
from app.agents.category_agent import CategoryAgent
from app.agents.seo_agent import SEOAgent
from app.agents.formatting_agent import FormattingAgent
from app.agents.humanize_agent import HumanizeAgent
from app.firebase.firestore_service import FirestoreService
from app.utils.date_utils import (
    COMMON_TIMEZONES, DATE_FORMATS, TIME_FORMATS, LOCALES,
    get_current_time_preview
)
from app.utils.slug_utils import PERMALINK_STRUCTURES
from datetime import datetime
import math
import markdown
from functools import wraps

blog_bp = Blueprint('blog', __name__)
db_service = FirestoreService()


# ---------------------------------------------------
# SECURITY DECORATORS
# ---------------------------------------------------

def admin_required(f):
    """Decorator to restrict routes to admin users only"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('auth_bp.login'))
        if session.get('user_role') != 'ADMIN':
            abort(404)  # Show 404 instead of 403 to hide the existence of the page
        return f(*args, **kwargs)
    return decorated_function


# ---------------------------------------------------
# SECURITY MIDDLEWARE
# ---------------------------------------------------

@blog_bp.before_request
def require_login():
    if not session.get('logged_in'):
        return redirect(url_for('auth_bp.login'))

@blog_bp.after_request
def add_cache_headers(response):
    if request.headers.get('X-Pjax') and response.status_code == 200:
        response.headers['Cache-Control'] = 'private, max-age=10, stale-while-revalidate=30'
    return response

# ---------------------------------------------------
# WEB PAGE ROUTES
# ---------------------------------------------------

# @blog_bp.route('/dashboard')
# def home():
#     try:
#         hour = datetime.now().hour
#         greeting = "Good Morning" if hour < 12 else "Good Afternoon" if hour < 18 else "Good Evening"

#         user_id = session.get('user_id')
#         user_role = session.get('user_role', 'USER')
#         username = session.get('user_name', 'User')

#         published_count = db_service.get_published_count(user_id)
#         drafts = db_service.get_blogs_by_status("DRAFT", user_id=user_id)
#         pending = db_service.get_blogs_by_status("UNDER_REVIEW", user_id=user_id)

#         total_blogs = db_service.get_total_blogs_count(user_id=user_id)
#         categories = db_service.get_all_categories(user_id=user_id)
#         recent_activity = db_service.get_recent_activity(user_id=user_id, limit=10)

#         return render_template(
#             'home.html',
#             greeting=greeting,
#             username=username,
#             user_role=user_role,
#             total_blogs_count=total_blogs,
#             published_count=published_count,
#             drafts_count=len(drafts),
#             pending_count=len(pending),
#             categories_count=len(categories),
#             recent_activity=recent_activity
#         )

#     except Exception as e:
#         print(f"Error in home route: {e}")
#         return render_template(
#             'home.html',
#             total_blogs_count=0,
#             published_count=0,
#             drafts_count=0,
#             pending_count=0,
#             recent_activity=[]
#         )

@blog_bp.route('/dashboard')
def home():
    try:
        hour = datetime.now().hour
        greeting = "Good Morning" if hour < 12 else "Good Afternoon" if hour < 18 else "Good Evening"

        user_id = session.get('user_id')
        user_role = session.get('user_role', 'USER')
        username = session.get('user_name', 'User')

        if user_role == 'ADMIN':
            dashboard_data = db_service.get_admin_dashboard_data(user_id)
        else:
            dashboard_data = db_service.get_dashboard_data(user_id)

        published_blogs = dashboard_data.get('published_blogs', [])

        all_blogs = dashboard_data['drafts'] + dashboard_data['pending'] + published_blogs
        all_blogs.sort(key=lambda x: x.get('updated_at') or x.get('created_at') or '', reverse=True)

        return render_template(
            'home.html',
            greeting=greeting,
            username=username,
            total_blogs=dashboard_data['total_blogs'],
            published_count=dashboard_data['published_count'],
            drafts_count=len(dashboard_data['drafts']),
            pending_count=len(dashboard_data['pending']),
            all_blogs=all_blogs[:5],
            pending_blogs=dashboard_data['pending'][:5],
            published_blogs=published_blogs[:5]
        )

    except Exception as e:
        print(f"Error in home route: {e}")
        return render_template(
            'home.html',
            greeting="Welcome",
            username="User",
            total_blogs=0,
            published_count=0,
            drafts_count=0,
            pending_count=0,
            all_blogs=[],
            pending_blogs=[],
            published_blogs=[]
        )
        
@blog_bp.route('/create')
def create_page():
    return render_template('create_blog.html', username=session.get('user_name', 'User'))


@blog_bp.route('/drafts')
def drafts_page():
    user_id = session.get('user_id')
    page = request.args.get('page', 1, type=int)
    per_page = 10

    drafts, total_count = db_service.get_paginated_drafts(user_id, page=page, per_page=per_page)
    total_pages = math.ceil(total_count / per_page) if total_count else 1

    return render_template(
        'drafts.html',
        drafts=drafts,
        current_page=page,
        total_pages=total_pages,
        has_next=page < total_pages,
        has_prev=page > 1
    )


@blog_bp.route('/approval')
@admin_required
def approval_page():
    user_id = session.get('user_id')
    page = request.args.get('page', 1, type=int)
    per_page = 10

    pending_blogs = db_service.get_approval_queue(admin_id=user_id)

    total_count = len(pending_blogs)
    total_pages = math.ceil(total_count / per_page) if total_count else 1

    start = (page - 1) * per_page
    end = start + per_page
    paginated_blogs = pending_blogs[start:end]

    return render_template(
        'approval_queue.html',
        pending_blogs=paginated_blogs,
        current_page=page,
        total_pages=total_pages,
        has_next=page < total_pages,
        has_prev=page > 1
    )


@blog_bp.route('/categories')
def categories_page():
    user_id = session.get('user_id')
    user_role = session.get('user_role', 'USER')

    if user_role == 'ADMIN':
        categories = db_service.get_all_categories(user_id=user_id)
    else:
        categories = db_service.get_user_blog_categories(user_id)
    return render_template('categories.html', categories=categories)


@blog_bp.route('/comments')
@admin_required
def comments_page():
    """Comment Moderation Dashboard"""
    user_id = session.get('user_id')
    stats = db_service.get_comment_stats(user_id)
    return render_template('comments.html', comment_stats=stats)


# ---------------------------------------------------
# API ROUTES
# ---------------------------------------------------

@blog_bp.route('/api/get_blog/<blog_id>')
def get_blog(blog_id):
    try:
        blog_data = db_service.get_blog_by_id(blog_id)
        if not blog_data:
            return jsonify({"success": False, "message": "Blog not found"}), 404

        content = blog_data.get('content', '')

        # Helper to check if content looks like markdown (not HTML)
        def is_markdown(text):
            if not text:
                return False
            # Check for markdown patterns: ##, **, ---, *, ```
            markdown_patterns = ['## ', '** ', '---', '* ', '```', '# ']
            return any(pattern in text for pattern in markdown_patterns)

        # Helper to convert markdown to HTML
        def convert_to_html(markdown_text, title=''):
            try:
                formatter = FormattingAgent()
                result = formatter.format_blog(markdown_text, title)
                return {
                    'html': result.get('html', markdown_text),
                    'toc': result.get('toc', []),
                    'toc_html': result.get('toc_html', '')
                }
            except Exception as e:
                print(f"Markdown conversion error: {e}")
                # Fallback: basic conversion using markdown library
                html = markdown.markdown(markdown_text, extensions=['extra', 'tables', 'toc'])
                return {'html': html, 'toc': [], 'toc_html': ''}

        if isinstance(content, dict):
            html_content = content.get('html', '')
            body_content = content.get('body') or content.get('text') or content.get('markdown', '')

            # If HTML is empty or looks like markdown, convert it
            if not html_content or is_markdown(html_content):
                converted = convert_to_html(body_content, blog_data.get('title', ''))
                blog_data['content'] = {
                    'html': converted['html'],
                    'body': body_content,
                    'toc': converted['toc'] or content.get('toc', []),
                    'toc_html': converted['toc_html'] or content.get('toc_html', '')
                }
            else:
                blog_data['content'] = {
                    'html': html_content,
                    'body': body_content,
                    'toc': content.get('toc', []),
                    'toc_html': content.get('toc_html', '')
                }
        else:
            # Plain string content - convert to HTML
            text_content = str(content)
            if is_markdown(text_content):
                converted = convert_to_html(text_content, blog_data.get('title', ''))
                blog_data['content'] = {
                    'html': converted['html'],
                    'body': text_content,
                    'toc': converted['toc'],
                    'toc_html': converted['toc_html']
                }
            else:
                blog_data['content'] = {
                    'html': text_content,
                    'body': text_content,
                    'toc': [],
                    'toc_html': ''
                }

        # Look up author name if not stored
        if not blog_data.get('author'):
            author_id = blog_data.get('author_id')
            if author_id:
                user = db_service.get_user_by_id(author_id)
                if user:
                    blog_data['author'] = user.get('username') or user.get('displayName') or user.get('email', '').split('@')[0]

        return jsonify({"success": True, "blog": blog_data})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@blog_bp.route('/api/update_blog/<blog_id>', methods=['POST'])
def update_blog(blog_id):
    try:
        from app.utils.slug_utils import validate_slug

        data = request.get_json()
        title = data.get('title')
        content = data.get('content')
        new_slug = data.get('slug', '')
        seo_title = data.get('seo_title', '').strip()
        seo_description = data.get('seo_description', '').strip()
        cover_image = data.get('cover_image', '').strip()

        # Validate and sanitize slug if provided
        if new_slug:
            new_slug = validate_slug(new_slug)

        success = db_service.update_blog_content(
            blog_id, title, content, new_slug,
            seo_title=seo_title, seo_description=seo_description,
            cover_image=cover_image
        )

        if success:
            db_service.log_activity(
                user_id=session.get('user_id'),
                user_name=session.get('user_name', 'User'),
                type="edited",
                action_text="updated blog content",
                blog_title=title
            )

        return jsonify({"success": success})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@blog_bp.route('/api/generate', methods=['POST'])
def generate_and_submit():
    try:
        user_id = session.get('user_id')
        user_name = session.get('user_name', 'User')

        if not user_id:
            return jsonify({"success": False, "error": "User session expired"}), 401

        data = request.get_json()
        prompt = data.get('prompt')
        auto_submit = data.get('auto_submit', False)
        enable_humanize = data.get('enable_humanize', False)

        # Run optimized pipeline (SEO disabled by default for speed)
        blog_ai = BlogAgent()
        generated_data = blog_ai.run_pipeline(prompt, enable_seo=False, enable_humanize=enable_humanize)

        # Check if pipeline failed before proceeding
        if generated_data.get('status') == 'failed' or 'error' in generated_data:
            error_msg = generated_data.get('error', 'Blog generation failed')
            print(f"❌ Pipeline failed: {error_msg}")
            return jsonify({"success": False, "error": error_msg}), 500

        # Extract content safely while preserving full structure
        content_text = ""
        content_obj = generated_data.get('content', {})
        formatting_obj = generated_data.get('formatting', {})

        if isinstance(content_obj, dict):
            content_text = (
                content_obj.get('markdown')
                or content_obj.get('body')
                or content_obj.get('text', '')
            )
            # Preserve the full content structure with HTML and TOC
            generated_data['content'] = {
                'body': content_text,
                'html': content_obj.get('html', ''),
                'markdown': content_obj.get('markdown', content_text),
                'toc': formatting_obj.get('toc', []),
                'toc_html': formatting_obj.get('toc_html', '')
            }
        elif isinstance(content_obj, str):
            content_text = content_obj
            generated_data['content'] = {
                'body': content_text,
                'html': content_text,
                'markdown': content_text,
                'toc': [],
                'toc_html': ''
            }
        else:
            content_text = "AI generation completed but content could not be parsed."
            generated_data['content'] = {
                'body': content_text,
                'html': content_text,
                'markdown': content_text,
                'toc': [],
                'toc_html': ''
            }

        # Category assignment with cached categories
        cat_agent = CategoryAgent()
        categories = db_service.get_all_categories(user_id, limit=50, use_cache=True)
        generated_data['category'] = cat_agent.categorize_blog(
            generated_data.get('title'),
            content_text,
            categories=categories
        )

        status = (
            "PUBLISHED"
            if auto_submit and session.get('user_role') == 'ADMIN'
            else "UNDER_REVIEW"
            if auto_submit
            else "DRAFT"
        )

        generated_data['status'] = status
        generated_data['author_id'] = user_id
        generated_data['author'] = user_name

        db_service.create_draft(generated_data, user_id)

        db_service.log_activity(
            user_id=user_id,
            user_name=user_name,
            type="generated",
            action_text=f"generated a blog as {status}",
            blog_title=generated_data.get('title', 'Untitled')
        )

        return jsonify({
            "success": True,
            "redirect": url_for(
                'blog.approval_page'
                if status == "UNDER_REVIEW"
                else 'blog.home'
                if status == "PUBLISHED"
                else 'blog.drafts_page'
            )
        }), 201

    except Exception as e:
        print(f"❌ Route Error in Generate: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@blog_bp.route('/api/humanize/<blog_id>', methods=['POST'])
def humanize_draft(blog_id):
    """Humanize an existing draft's content post-generation."""
    try:
        user_id = session.get('user_id')
        if not user_id:
            return jsonify({"success": False, "error": "User session expired"}), 401

        blog_data = db_service.get_blog_by_id(blog_id)
        if not blog_data:
            return jsonify({"success": False, "error": "Blog not found"}), 404

        # Extract markdown content
        content = blog_data.get('content', {})
        if isinstance(content, dict):
            markdown_text = content.get('markdown') or content.get('body') or ''
        else:
            markdown_text = str(content)

        if not markdown_text.strip():
            return jsonify({"success": False, "error": "No content to humanize"}), 400

        # Run humanization
        humanizer = HumanizeAgent()
        result = humanizer.humanize_content(
            markdown=markdown_text,
            topic=blog_data.get('title', '')
        )

        if not result.get('humanization_applied'):
            return jsonify({"success": False, "error": "Humanization failed — content unchanged"}), 500

        # Re-format the humanized content
        formatter = FormattingAgent()
        formatted = formatter.format_blog(
            content=result['markdown'],
            title=blog_data.get('title', '')
        )

        # Update the blog in Firestore
        updated_content = {
            'body': result['markdown'],
            'html': formatted['html'],
            'markdown': result['markdown'],
            'toc': formatted['toc'],
            'toc_html': formatted['toc_html']
        }

        doc_ref = db_service.db.collection(db_service.collection_name).document(blog_id)
        doc_ref.update({
            'content': updated_content,
            'metadata.humanized': True,
            'updated_at': datetime.utcnow()
        })

        return jsonify({"success": True, "message": "Content humanized successfully"})

    except Exception as e:
        print(f"❌ Humanize Route Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# @blog_bp.route('/api/update_status/<blog_id>', methods=['POST'])
# def update_status(blog_id):
#     try:
#         data = request.get_json()
#         new_status = data.get('status', 'DRAFT').upper()

#         success = db_service.update_blog_status(blog_id, new_status)

#         if success:
#             blog_data = db_service.get_blog_by_id(blog_id)
#             action_text = "approved for publication" if new_status == "PUBLISHED" else "rejected back to drafts"

#             db_service.log_activity(
#                 user_id=session.get('user_id'),
#                 user_name=session.get('user_name', 'Admin'),
#                 type="published" if new_status == "PUBLISHED" else "edited",
#                 action_text=action_text,
#                 blog_title=blog_data.get('title', 'Untitled') if blog_data else "Untitled"
#             )

#         return jsonify({"success": success})

#     except Exception as e:
#         return jsonify({"success": False, "error": str(e)}), 500


@blog_bp.route('/api/update_status/<blog_id>', methods=['POST'])
def update_status(blog_id):
    try:
        user_id = session.get('user_id')
        user_role = session.get('user_role', 'USER')
        user_name = session.get('user_name', 'User')

        if not user_id:
            return jsonify({"success": False, "error": "Unauthorized"}), 401

        data = request.get_json()
        new_status = data.get('status', '').upper()

        allowed_statuses = ["DRAFT", "UNDER_REVIEW", "PUBLISHED", "SCHEDULED"]

        if new_status not in allowed_statuses:
            return jsonify({"success": False, "error": "Invalid status"}), 400

        blog_data = db_service.get_blog_by_id(blog_id)
        if not blog_data:
            return jsonify({"success": False, "error": "Blog not found"}), 404

        # Only admin can publish or schedule
        if new_status == "PUBLISHED" and user_role != "ADMIN":
            return jsonify({"success": False, "error": "Only admin can publish"}), 403

        if new_status == "SCHEDULED" and user_role != "ADMIN":
            return jsonify({"success": False, "error": "Only admin can schedule"}), 403

        # Only owner or admin can change status
        if blog_data.get("author_id") != user_id and user_role != "ADMIN":
            return jsonify({"success": False, "error": "Not allowed"}), 403

        # Apply formatting when publishing
        if new_status == "PUBLISHED":
            try:
                # Extract markdown content
                content = blog_data.get('content', '')
                if isinstance(content, dict):
                    markdown_content = content.get('markdown') or content.get('body') or content.get('text', '')
                else:
                    markdown_content = str(content)

                title = blog_data.get('title', '')

                # Apply formatting agent
                formatter = FormattingAgent()
                formatted = formatter.format_blog(markdown_content, title)

                # Update blog with formatted content
                formatted_content = {
                    'body': markdown_content,
                    'markdown': markdown_content,
                    'html': formatted['html'],
                    'toc': formatted['toc'],
                    'toc_html': formatted['toc_html'],
                    'reading_time': formatted['reading_time_text'],
                    'statistics': formatted['statistics']
                }

                # Update blog content with formatting
                db_service.update_blog_content(blog_id, title, formatted_content)
                print(f"✓ Formatting applied to blog: {title}")

                # Generate embedding for semantic search
                try:
                    from app.agents.semantic_search_agent import SemanticSearchAgent
                    search_agent = SemanticSearchAgent()
                    if search_agent.generate_and_store_embedding(blog_id):
                        print(f"✓ Embedding generated for blog: {title}")
                except Exception as embed_error:
                    print(f"⚠ Embedding generation warning (continuing): {embed_error}")

            except Exception as format_error:
                print(f"⚠ Formatting warning (continuing): {format_error}")
                # Continue with publish even if formatting fails

        success = db_service.update_blog_status(blog_id, new_status)

        if not success:
            return jsonify({"success": False, "error": "Status update failed"}), 500

        # Extra cache invalidation after publish to ensure blog appears immediately
        if new_status == "PUBLISHED":
            from app.utils.cache import cache
            site_owner_id = blog_data.get('site_owner_id') or blog_data.get('author_id') or user_id
            cache.clear_prefix(f"published_blogs:{site_owner_id}")
            cache.clear_prefix(f"published_blogs:{user_id}")

        action_text = (
            "approved for publication"
            if new_status == "PUBLISHED"
            else "submitted for approval"
            if new_status == "UNDER_REVIEW"
            else "moved back to draft"
        )

        db_service.log_activity(
            user_id=user_id,
            user_name=user_name,
            type="status_change",
            action_text=action_text,
            blog_title=blog_data.get('title', 'Untitled')
        )

        return jsonify({"success": True})

    except Exception as e:
        print("❌ Status Update Error:", str(e))
        return jsonify({"success": False, "error": str(e)}), 500


@blog_bp.route('/api/delete_blog/<blog_id>', methods=['DELETE'])
def delete_blog_api(blog_id):
    blog_data = db_service.get_blog_by_id(blog_id)
    title = blog_data.get('title', 'Untitled') if blog_data else "Untitled"

    success = db_service.delete_blog(blog_id)

    if success:
        db_service.log_activity(
            user_id=session.get('user_id'),
            user_name=session.get('user_name', 'User'),
            type="deleted",
            action_text="permanently deleted",
            blog_title=title
        )

    return jsonify({"success": success})

# Categories API routes
# ---------------------------------------------------
# CATEGORY API ROUTES
# ---------------------------------------------------


@blog_bp.route('/api/categories', methods=['POST'])
def create_category_api():
    try:
        user_id = session.get('user_id')
        if not user_id: return jsonify({"success": False, "error": "Unauthorized"}), 401
        
        data = request.get_json() if request.is_json else request.form
        name = data.get('name', '').strip()
        
        if not name: return jsonify({"success": False, "error": "Category name cannot be empty"}), 400
        
        success, result = db_service.create_category(name, user_id)
        if success:
             return jsonify({"success": True, "id": result, "name": name})
        return jsonify({"success": False, "error": result}), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@blog_bp.route('/api/edit_category/<category_id>', methods=['POST'])
def edit_category(category_id):
    try:
        user_id = session.get('user_id')
        if not user_id:
            return jsonify({"success": False, "error": "Unauthorized"}), 401

        data = request.get_json()
        new_name = data.get('name', '').strip()
        if not new_name:
            return jsonify({"success": False, "error": "Category name cannot be empty"}), 400

        # Check if category exists
        category = db_service.get_category_by_id(category_id, user_id=user_id)
        if not category:
            return jsonify({"success": False, "error": "Category not found"}), 404

        # Update category name
        success = db_service.update_category_name(category_id, new_name, user_id=user_id)

        if success:
            db_service.log_activity(
                user_id=user_id,
                user_name=session.get('user_name', 'User'),
                type="edited",
                action_text=f"updated category name to '{new_name}'",
                blog_title=""  # Optional, leave empty for category actions
            )

        return jsonify({"success": success})

    except Exception as e:
        print("❌ Edit Category Error:", e)
        return jsonify({"success": False, "error": str(e)}), 500


@blog_bp.route('/api/delete_category/<category_id>', methods=['DELETE'])
def delete_category(category_id):
    try:
        user_id = session.get('user_id')
        if not user_id:
            return jsonify({"success": False, "error": "Unauthorized"}), 401

        # Check if category exists
        category = db_service.get_category_by_id(category_id, user_id=user_id)
        if not category:
            return jsonify({"success": False, "error": "Category not found"}), 404

        # Optional: Check if any blogs are using this category before deleting
        blogs_in_category = db_service.get_blogs_by_category(category_id, user_id=user_id)
        if blogs_in_category:
            return jsonify({"success": False, "error": "Cannot delete category with assigned blogs"}), 400

        success = db_service.delete_category(category_id, user_id=user_id)

        if success:
            db_service.log_activity(
                user_id=user_id,
                user_name=session.get('user_name', 'User'),
                type="deleted",
                action_text=f"deleted category '{category.get('name')}'",
                blog_title=""  # Optional
            )

        return jsonify({"success": success})

    except Exception as e:
        print("Delete Category Error:", e)
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------
# SEO TOOLS ROUTES
# ---------------------------------------------------

@blog_bp.route('/seo-tools')
def seo_tools_page():
    """SEO Tools Dashboard"""
    return render_template(
        'seo_tools.html',
        username=session.get('user_name', 'User')
    )


@blog_bp.route('/formatting-tools')
def formatting_tools_page():
    """Formatting Tools Dashboard"""
    return render_template(
        'formatting_tools.html',
        username=session.get('user_name', 'User')
    )


@blog_bp.route('/api/seo/analyze', methods=['POST'])
def analyze_seo():
    """Analyze content for SEO and get keyword suggestions"""
    try:
        user_id = session.get('user_id')
        if not user_id:
            return jsonify({"success": False, "error": "Unauthorized"}), 401

        data = request.get_json()
        title = data.get('title', '')
        content = data.get('content', '')
        region = data.get('region', 'PK')

        if not content:
            return jsonify({"success": False, "error": "Content is required"}), 400

        seo_agent = SEOAgent()
        result = seo_agent.optimize_blog(title, content, region)

        return jsonify({
            "success": True,
            "seo_analysis": result
        })

    except Exception as e:
        print(f"SEO Analysis Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@blog_bp.route('/api/seo/keywords', methods=['POST'])
def research_keywords():
    """Research keywords for a given topic"""
    try:
        user_id = session.get('user_id')
        if not user_id:
            return jsonify({"success": False, "error": "Unauthorized"}), 401

        data = request.get_json()
        topic = data.get('topic', '')
        region = data.get('region', 'PK')

        if not topic:
            return jsonify({"success": False, "error": "Topic is required"}), 400

        seo_agent = SEOAgent()

        # Extract seed keywords
        seed_keywords = seo_agent._extract_seed_keywords(topic)

        # Get related keywords from Google
        all_keywords = []
        for seed in seed_keywords[:3]:  # Limit to 3 seeds to avoid rate limits
            related = seo_agent._get_google_related_keywords(seed, region)
            all_keywords.extend(related)

        # Remove duplicates
        seen = set()
        unique_keywords = []
        for kw in all_keywords:
            if kw['keyword'] not in seen:
                seen.add(kw['keyword'])
                unique_keywords.append(kw)

        # Sort by difficulty (easiest first)
        unique_keywords.sort(key=lambda x: x.get('difficulty_score', 50))

        return jsonify({
            "success": True,
            "seed_keywords": seed_keywords,
            "related_keywords": unique_keywords[:20],  # Top 20
            "region": region
        })

    except Exception as e:
        print(f"Keyword Research Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@blog_bp.route('/api/seo/optimize-blog/<blog_id>', methods=['POST'])
def optimize_existing_blog(blog_id):
    """Apply SEO optimization to an existing blog"""
    try:
        user_id = session.get('user_id')
        if not user_id:
            return jsonify({"success": False, "error": "Unauthorized"}), 401

        data = request.get_json()
        region = data.get('region', 'PK')

        # Get the blog
        blog_data = db_service.get_blog_by_id(blog_id)
        if not blog_data:
            return jsonify({"success": False, "error": "Blog not found"}), 404

        # Check ownership
        if blog_data.get('author_id') != user_id and session.get('user_role') != 'ADMIN':
            return jsonify({"success": False, "error": "Not authorized"}), 403

        # Extract content
        content = blog_data.get('content', '')
        if isinstance(content, dict):
            content = content.get('markdown') or content.get('body') or ''

        title = blog_data.get('title', '')

        # Run SEO optimization
        seo_agent = SEOAgent()
        result = seo_agent.optimize_blog(title, content, region)

        if result.get('optimized'):
            # Update blog with optimized content
            optimized = result['optimized']
            new_title = optimized.get('optimized_title', title)
            new_content = optimized.get('optimized_content', content)

            success = db_service.update_blog_content(blog_id, new_title, new_content)

            if success:
                # Log activity
                db_service.log_activity(
                    user_id=user_id,
                    user_name=session.get('user_name', 'User'),
                    type="seo_optimized",
                    action_text="applied SEO optimization to",
                    blog_title=new_title
                )

            return jsonify({
                "success": success,
                "seo_score": optimized.get('seo_score', 0),
                "seo_grade": optimized.get('seo_grade', 'N/A'),
                "primary_keyword": result.get('keyword_research', {}).get('primary_keyword', {}),
                "new_title": new_title,
                "comparison": result.get('comparison', {}),
                "changes_made": result.get('changes_made', []),
                "original_score": result.get('original_analysis', {}).get('seo_score', {}).get('total', 0),
                "score_improvement": result.get('score_improvement', 0)
            })

        return jsonify({
            "success": False,
            "error": "SEO optimization could not be applied"
        }), 400

    except Exception as e:
        print(f"Blog SEO Optimization Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------
# FORMATTING API ROUTES
# ---------------------------------------------------

@blog_bp.route('/api/format', methods=['POST'])
def format_content():
    """Format markdown content and return HTML with metadata"""
    try:
        user_id = session.get('user_id')
        if not user_id:
            return jsonify({"success": False, "error": "Unauthorized"}), 401

        data = request.get_json()
        content = data.get('content', '')
        title = data.get('title', '')

        if not content:
            return jsonify({"success": False, "error": "Content is required"}), 400

        formatter = FormattingAgent()
        result = formatter.format_blog(content, title)

        return jsonify({
            "success": True,
            "formatted": {
                "html": result['html'],
                "toc": result['toc'],
                "toc_html": result['toc_html'],
                "reading_time": result['reading_time_text'],
                "statistics": result['statistics']
            }
        })

    except Exception as e:
        print(f"Formatting Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@blog_bp.route('/api/seo/drafts')
def get_drafts_for_seo():
    """Get user's drafts for SEO analysis"""
    try:
        user_id = session.get('user_id')
        if not user_id:
            return jsonify({"success": False, "error": "Unauthorized"}), 401

        # Get all drafts for this user
        drafts = db_service.get_blogs_by_status("DRAFT", user_id=user_id)

        # Format for dropdown
        draft_list = []
        for draft in drafts:
            content = draft.get('content', '')
            if isinstance(content, dict):
                content = content.get('markdown') or content.get('body') or ''

            draft_list.append({
                "id": draft.get('id'),
                "title": draft.get('title', 'Untitled'),
                "preview": content[:100] + "..." if len(content) > 100 else content,
                "updated_at": draft.get('updated_at').strftime('%Y-%m-%d') if draft.get('updated_at') else None
            })

        return jsonify({
            "success": True,
            "drafts": draft_list
        })

    except Exception as e:
        print(f"Get Drafts Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@blog_bp.route('/api/seo/analyze-draft/<blog_id>', methods=['POST'])
def analyze_draft_seo(blog_id):
    """Analyze a specific draft for SEO without modifying it"""
    try:
        user_id = session.get('user_id')
        if not user_id:
            return jsonify({"success": False, "error": "Unauthorized"}), 401

        data = request.get_json() or {}
        region = data.get('region', 'PK')

        # Get the blog
        blog_data = db_service.get_blog_by_id(blog_id)
        if not blog_data:
            return jsonify({"success": False, "error": "Blog not found"}), 404

        # Check ownership
        if blog_data.get('author_id') != user_id and session.get('user_role') != 'ADMIN':
            return jsonify({"success": False, "error": "Not authorized"}), 403

        # Extract content
        content = blog_data.get('content', '')
        if isinstance(content, dict):
            content = content.get('markdown') or content.get('body') or ''

        title = blog_data.get('title', '')

        # Run SEO analysis only (without applying changes) - Step 1
        seo_agent = SEOAgent()
        result = seo_agent.analyze_only(title, content)

        return jsonify({
            "success": True,
            "blog_id": blog_id,
            "blog_title": title,
            "original_analysis": result
        })

    except Exception as e:
        print(f"Draft SEO Analysis Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------
# SITE SETTINGS ROUTES
# ---------------------------------------------------

@blog_bp.route('/site-settings')
@admin_required
def site_settings_page():
    """Site Settings Dashboard"""
    user_id = session.get('user_id')

    # Get current settings
    settings = db_service.get_site_settings(user_id)

    # Get published blogs for management (includes team members' blogs)
    published_blogs = db_service.get_published_blogs(user_id, limit=100)

    # Get stats for the settings page
    categories = db_service.get_all_categories(user_id=user_id)
    pending = db_service.get_blogs_by_status("UNDER_REVIEW", user_id=user_id)

    # Get time preview based on current settings
    time_preview = get_current_time_preview(
        timezone=settings.get('timezone', 'UTC'),
        date_format=settings.get('date_format', 'MMM DD, YYYY'),
        time_format=settings.get('time_format', '12h')
    )

    # Get service account email for Google Sheets sharing instructions
    service_account_email = ''
    try:
        import json
        with open('serviceAccountKey.json') as f:
            sa_data = json.load(f)
            service_account_email = sa_data.get('client_email', '')
    except Exception:
        pass

    return render_template(
        'site_settings.html',
        settings=settings,
        published_blogs=published_blogs,
        published_count=len(published_blogs),
        categories_count=len(categories),
        pending_count=len(pending),
        username=session.get('user_name', 'User'),
        # Locale & timezone data
        timezones=COMMON_TIMEZONES,
        date_formats=DATE_FORMATS,
        time_formats=TIME_FORMATS,
        locales=LOCALES,
        time_preview=time_preview,
        # Permalink structures
        permalink_structures=PERMALINK_STRUCTURES,
        # Google Sheets
        service_account_email=service_account_email
    )


@blog_bp.route('/api/site-settings', methods=['POST'])
@admin_required
def update_site_settings():
    """Update site settings with all configuration options"""
    try:
        user_id = session.get('user_id')

        data = request.get_json()

        # Handle site_slug: validate and check availability
        new_slug = data.get('site_slug', '').strip().lower()
        new_slug = ''.join(c for c in new_slug if c.isalnum() or c == '-')
        new_slug = new_slug.strip('-')

        if new_slug:
            if len(new_slug) < 3:
                return jsonify({"success": False, "error": "Site slug must be at least 3 characters"}), 400
            if not db_service.is_slug_available(new_slug, exclude_user_id=user_id):
                return jsonify({"success": False, "error": "This slug is already taken"}), 400

        # Build settings object with all fields
        settings = {
            # General
            'site_slug': new_slug,
            'site_name': data.get('site_name', '').strip(),
            'site_description': data.get('site_description', '').strip(),
            'niche': data.get('niche', '').strip(),
            # Appearance
            'logo_url': data.get('logo_url', '').strip(),
            'favicon_url': data.get('favicon_url', '').strip(),
            'primary_color': data.get('primary_color', '#4318FF').strip(),
            'secondary_color': data.get('secondary_color', '#6366F1').strip(),
            'cover_image_url': data.get('cover_image_url', '').strip(),
            # Content
            'posts_per_page': int(data.get('posts_per_page', 10)),
            'show_reading_time': data.get('show_reading_time', True),
            'show_author': data.get('show_author', True),
            'featured_post_id': data.get('featured_post_id', '').strip(),
            # SEO (basic)
            'meta_title': data.get('meta_title', '').strip(),
            'meta_description': data.get('meta_description', '').strip(),
            'og_image_url': data.get('og_image_url', '').strip(),
            'analytics_id': data.get('analytics_id', '').strip(),
            'custom_domain': data.get('custom_domain', '').strip(),
            # SEO (advanced) - nested object
            'seo': data.get('seo', {}),
            # Social
            'social_links': {
                'twitter': data.get('social_twitter', '').strip(),
                'linkedin': data.get('social_linkedin', '').strip(),
                'github': data.get('social_github', '').strip()
            },
            'contact_email': data.get('contact_email', '').strip(),
            'about_content': data.get('about_content', '').strip(),
            # Behavior
            'site_visibility': data.get('site_visibility', 'public'),

            # Locale & Time
            'timezone': data.get('timezone', 'UTC'),
            'date_format': data.get('date_format', 'MMM DD, YYYY'),
            'time_format': data.get('time_format', '12h'),
            'locale': data.get('locale', 'en'),

            # Header Settings
            'header': data.get('header', {}),

            # Footer Settings
            'footer': data.get('footer', {}),

            # Hero Sections
            'hero_home': data.get('hero_home', {}),
            'hero_about': data.get('hero_about', {}),
            'hero_blog': data.get('hero_blog', {}),
            'hero_contact': data.get('hero_contact', {}),

            # Permalink Settings
            'permalinks': data.get('permalinks', {}),

            # RSS Feed Settings
            'rss': data.get('rss', {}),

            # Legal Pages Settings
            'legal': data.get('legal', {}),

            # Google Sheets
            'google_sheets_id': data.get('google_sheets_id', '').strip()
        }

        # Handle boolean values that come as strings from form
        if isinstance(settings['show_reading_time'], str):
            settings['show_reading_time'] = settings['show_reading_time'].lower() == 'true'
        if isinstance(settings['show_author'], str):
            settings['show_author'] = settings['show_author'].lower() == 'true'

        if not settings['site_name']:
            return jsonify({"success": False, "error": "Site name is required"}), 400

        success = db_service.update_site_settings(user_id, settings)

        if success:
            db_service.log_activity(
                user_id=user_id,
                user_name=session.get('user_name', 'User'),
                type="settings",
                action_text="updated site settings",
                blog_title=""
            )

        return jsonify({"success": success})

    except Exception as e:
        print(f"Site Settings Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@blog_bp.route('/api/time-preview', methods=['POST'])
@admin_required
def get_time_preview():
    """Get formatted time preview for locale settings"""
    try:
        data = request.get_json()
        timezone = data.get('timezone', 'UTC')
        date_format = data.get('date_format', 'MMM DD, YYYY')
        time_format = data.get('time_format', '12h')

        preview = get_current_time_preview(timezone, date_format, time_format)

        return jsonify({
            "success": True,
            "date": preview['date'],
            "time": preview['time'],
            "full": preview['full']
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------
# NEWSLETTER PAGE ROUTE
# ---------------------------------------------------

@blog_bp.route('/newsletter')
def newsletter_page():
    """Newsletter Management Dashboard"""
    user_id = session.get('user_id')

    # Get published blogs count for stats
    published_blogs = db_service.get_blogs_by_status("PUBLISHED", user_id=user_id)

    return render_template(
        'newsletter.html',
        published_count=len(published_blogs),
        username=session.get('user_name', 'User')
    )


@blog_bp.route('/api/unpublish/<blog_id>', methods=['POST'])
def unpublish_blog(blog_id):
    """Unpublish a blog - moves it back to approval queue (UNDER_REVIEW)"""
    try:
        user_id = session.get('user_id')
        user_role = session.get('user_role', 'USER')

        if not user_id:
            return jsonify({"success": False, "error": "Unauthorized"}), 401

        # Get the blog
        blog_data = db_service.get_blog_by_id(blog_id)
        if not blog_data:
            return jsonify({"success": False, "error": "Blog not found"}), 404

        # Only owner or admin can unpublish
        if blog_data.get('author_id') != user_id and user_role != 'ADMIN':
            return jsonify({"success": False, "error": "Not authorized"}), 403

        # Check if blog is currently published
        if blog_data.get('status') != 'PUBLISHED':
            return jsonify({"success": False, "error": "Blog is not published"}), 400

        # Change status to UNDER_REVIEW
        success = db_service.update_blog_status(blog_id, 'UNDER_REVIEW')

        if success:
            db_service.log_activity(
                user_id=user_id,
                user_name=session.get('user_name', 'User'),
                type="status_change",
                action_text="unpublished and moved to review",
                blog_title=blog_data.get('title', 'Untitled')
            )

        return jsonify({"success": success})

    except Exception as e:
        print(f"Unpublish Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------
# COMMENT MODERATION API ROUTES
# ---------------------------------------------------

@blog_bp.route('/api/comments', methods=['GET'])
@admin_required
def api_get_comments():
    """Fetch comments for dashboard with filtering and pagination."""
    try:
        user_id = session.get('user_id')
        status_filter = request.args.get('status', 'all')
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 20))

        result = db_service.get_comments_for_dashboard(
            site_owner_id=user_id,
            status_filter=status_filter,
            page=page,
            per_page=per_page
        )

        # Serialize timestamps
        for comment in result['comments']:
            for field in ['created_at', 'updated_at', 'ai_moderated_at', 'removed_at']:
                val = comment.get(field)
                if val and hasattr(val, 'isoformat'):
                    comment[field] = val.isoformat()
                elif val and hasattr(val, 'timestamp'):
                    comment[field] = val.isoformat() if hasattr(val, 'isoformat') else str(val)

            # Serialize admin edit timestamps
            for edit in comment.get('admin_edits', []):
                if edit.get('edited_at') and hasattr(edit['edited_at'], 'isoformat'):
                    edit['edited_at'] = edit['edited_at'].isoformat()

        return jsonify({"success": True, **result})

    except Exception as e:
        print(f"Error fetching comments: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@blog_bp.route('/api/comments/stats', methods=['GET'])
@admin_required
def api_get_comment_stats():
    """Lightweight endpoint for refreshing comment stats after mutations."""
    try:
        user_id = session.get('user_id')
        stats = db_service.get_comment_stats(user_id)
        return jsonify({"success": True, "stats": stats})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@blog_bp.route('/api/comments/<comment_id>', methods=['GET'])
@admin_required
def api_get_comment(comment_id):
    """Get single comment with full moderation history."""
    try:
        comment = db_service.get_comment_by_id(comment_id)
        if not comment:
            return jsonify({"success": False, "error": "Comment not found"}), 404

        # Serialize timestamps
        for field in ['created_at', 'updated_at', 'ai_moderated_at', 'removed_at']:
            val = comment.get(field)
            if val and hasattr(val, 'isoformat'):
                comment[field] = val.isoformat()

        for edit in comment.get('admin_edits', []):
            if edit.get('edited_at') and hasattr(edit['edited_at'], 'isoformat'):
                edit['edited_at'] = edit['edited_at'].isoformat()

        return jsonify({"success": True, "comment": comment})

    except Exception as e:
        print(f"Error fetching comment: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@blog_bp.route('/api/comments/<comment_id>/edit', methods=['POST'])
@admin_required
def api_edit_comment(comment_id):
    """Admin edits a comment's display text."""
    try:
        data = request.get_json()
        new_text = (data.get('text', '') or '').strip()
        reason = (data.get('reason', '') or '').strip()

        if not new_text:
            return jsonify({"success": False, "error": "Comment text is required"}), 400

        user_id = session.get('user_id')
        user_name = session.get('user_name', 'Admin')

        success = db_service.update_comment_display_text(
            comment_id, new_text, user_id, user_name, reason
        )

        if success:
            comment = db_service.get_comment_by_id(comment_id)
            db_service.log_activity(
                user_id=user_id,
                user_name=user_name,
                type="comment",
                action_text=f"edited comment by {comment.get('commenter_name', 'Unknown')}",
                blog_title=comment.get('blog_title', '')
            )

        return jsonify({"success": success})

    except Exception as e:
        print(f"Error editing comment: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@blog_bp.route('/api/comments/<comment_id>/remove', methods=['POST'])
@admin_required
def api_remove_comment(comment_id):
    """Soft-remove a comment from the public site."""
    try:
        data = request.get_json() or {}
        reason = (data.get('reason', '') or '').strip()

        success = db_service.update_comment_status(
            comment_id, 'removed', removed_by='admin', reason=reason or 'Removed by admin'
        )

        if success:
            user_id = session.get('user_id')
            comment = db_service.get_comment_by_id(comment_id)
            db_service.log_activity(
                user_id=user_id,
                user_name=session.get('user_name', 'Admin'),
                type="comment",
                action_text=f"removed comment by {comment.get('commenter_name', 'Unknown')}",
                blog_title=comment.get('blog_title', '')
            )

        return jsonify({"success": success})

    except Exception as e:
        print(f"Error removing comment: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@blog_bp.route('/api/comments/<comment_id>/restore', methods=['POST'])
@admin_required
def api_restore_comment(comment_id):
    """Restore a removed comment back to published."""
    try:
        success = db_service.update_comment_status(comment_id, 'published')

        if success:
            user_id = session.get('user_id')
            comment = db_service.get_comment_by_id(comment_id)
            db_service.log_activity(
                user_id=user_id,
                user_name=session.get('user_name', 'Admin'),
                type="comment",
                action_text=f"restored comment by {comment.get('commenter_name', 'Unknown')}",
                blog_title=comment.get('blog_title', '')
            )

        return jsonify({"success": success})

    except Exception as e:
        print(f"Error restoring comment: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@blog_bp.route('/api/comments/<comment_id>/delete', methods=['DELETE'])
@admin_required
def api_delete_comment(comment_id):
    """Permanently delete a comment."""
    try:
        comment = db_service.get_comment_by_id(comment_id)
        if not comment:
            return jsonify({"success": False, "error": "Comment not found"}), 404

        success = db_service.delete_comment_permanently(comment_id)

        if success:
            user_id = session.get('user_id')
            db_service.log_activity(
                user_id=user_id,
                user_name=session.get('user_name', 'Admin'),
                type="comment",
                action_text=f"permanently deleted comment by {comment.get('commenter_name', 'Unknown')}",
                blog_title=comment.get('blog_title', '')
            )

        return jsonify({"success": success})

    except Exception as e:
        print(f"Error deleting comment: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

