import re

from flask import Blueprint, jsonify, render_template, request, session
from google.cloud.firestore_v1.base_query import FieldFilter

from app.agents.newsletter_agent import NewsletterAgent
from app.core.extensions import limiter
from app.core.logging import get_logger
from app.firebase.firestore_service import FirestoreService
from app.services.email_service import EmailService

logger = get_logger(__name__)

newsletter_bp = Blueprint('newsletter', __name__)

MAX_EMAIL_LENGTH = 254        # RFC 5321 maximum
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]{2,}$')

# Newsletter HTML template (inline for simplicity)
NEWSLETTER_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ subject }}</title>
    <style>
        body { margin: 0; padding: 0; background-color: #f4f4f4; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
        .wrapper { width: 100%; background-color: #f4f4f4; padding: 40px 0; }
        .container { max-width: 600px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05); }
        .header { background: linear-gradient(135deg, #4318FF 0%, #7B5AFF 100%); color: #ffffff; padding: 40px 30px; text-align: center; }
        .header h1 { margin: 0; font-size: 28px; font-weight: 700; }
        .content { padding: 40px 30px; }
        .intro { font-size: 16px; line-height: 1.6; color: #333333; margin-bottom: 30px; }
        .post { border: 1px solid #e5e5e5; border-radius: 8px; padding: 24px; margin-bottom: 20px; }
        .post-category { display: inline-block; background-color: #f0edff; color: #4318FF; font-size: 12px; font-weight: 600; padding: 4px 10px; border-radius: 20px; margin-bottom: 12px; text-transform: uppercase; }
        .post h3 { margin: 0 0 12px; font-size: 20px; font-weight: 600; color: #1a1a1a; }
        .post p { margin: 0 0 16px; font-size: 15px; line-height: 1.6; color: #555555; }
        .post a { color: #4318FF; font-weight: 600; text-decoration: none; }
        .cta-section { text-align: center; padding: 30px 0; }
        .cta-button { display: inline-block; background: linear-gradient(135deg, #4318FF 0%, #7B5AFF 100%); color: #ffffff !important; padding: 16px 40px; font-size: 16px; font-weight: 600; text-decoration: none; border-radius: 8px; }
        .closing { font-size: 15px; line-height: 1.6; color: #666666; text-align: center; padding: 20px 30px; border-top: 1px solid #f0f0f0; }
        .footer { background-color: #f9f9f9; padding: 30px; text-align: center; }
        .footer p { margin: 0 0 10px; font-size: 13px; color: #888888; }
        .footer a { color: #4318FF; text-decoration: none; }
        .unsubscribe { font-size: 12px; color: #aaaaaa; margin-top: 20px; }
    </style>
</head>
<body>
    <div class="wrapper">
        <div class="container">
            <div class="header">
                <h1>{{ site_name }}</h1>
            </div>
            <div class="content">
                <p class="intro">{{ intro }}</p>
                {% for post in posts %}
                <div class="post">
                    {% if post.category %}<span class="post-category">{{ post.category }}</span>{% endif %}
                    <h3>{{ post.title }}</h3>
                    <p>{{ post.summary }}</p>
                    <a href="{{ site_url }}/post/{{ post.id }}">Read More &rarr;</a>
                </div>
                {% endfor %}
                <div class="cta-section">
                    <a href="{{ site_url }}" class="cta-button">{{ cta_text }}</a>
                </div>
            </div>
            <div class="closing"><p>{{ closing }}</p></div>
            <div class="footer">
                <p><strong>{{ site_name }}</strong></p>
                <p>Thank you for being a valued subscriber!</p>
                <p class="unsubscribe">
                    <a href="{{ unsubscribe_url }}">Unsubscribe</a> | <a href="{{ site_url }}">Visit Website</a>
                </p>
            </div>
        </div>
    </div>
</body>
</html>
'''


def get_current_user():
    """Get current user ID from session."""
    return session.get('user_id') or session.get('uid')


# ==================== NEWSLETTER GENERATION ====================

@newsletter_bp.route('/api/newsletter/generate', methods=['POST'])
def generate_newsletter():
    """
    Generate AI newsletter content from recent published blogs.

    Request body:
        - topic (optional): Focus topic for newsletter
        - custom_intro (optional): Custom introduction text
        - blog_limit (optional): Number of blogs to include (default 5)
    """
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        data = request.json or {}
        topic = data.get('topic')
        custom_intro = data.get('custom_intro')
        blog_limit = min(data.get('blog_limit', 5), 10)

        firestore = FirestoreService()
        newsletter_agent = NewsletterAgent()

        # Get site settings for site name
        site_settings = firestore.get_site_settings(user_id)
        site_name = site_settings.get('site_name', 'My Blog')

        # Get recent published blogs
        blogs = firestore.get_published_blogs(user_id, limit=blog_limit)

        if not blogs:
            return jsonify({
                "success": False,
                "error": "No published blogs found. Publish some blogs first!"
            }), 400

        # Generate newsletter content
        result = newsletter_agent.generate_newsletter(
            blogs=blogs,
            site_name=site_name,
            custom_intro=custom_intro,
            topic=topic
        )

        if result.get('success'):
            return jsonify(result)
        else:
            return jsonify(result), 500

    except Exception as e:
        logger.exception("Newsletter generation error")
        return jsonify({"error": str(e)}), 500


@newsletter_bp.route('/api/newsletter/subject-variations', methods=['POST'])
def get_subject_variations():
    """Generate alternative subject line variations."""
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        data = request.json or {}
        subject = data.get('subject')

        if not subject:
            return jsonify({"error": "Subject is required"}), 400

        newsletter_agent = NewsletterAgent()
        variations = newsletter_agent.generate_subject_variations(subject)

        return jsonify({"variations": variations})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ==================== SEND NEWSLETTER ====================

@newsletter_bp.route('/api/newsletter/send', methods=['POST'])
def send_newsletter():
    """
    Send newsletter to all active subscribers.

    Request body:
        - subject: Email subject line
        - html_content: Full HTML email content
        - test_mode (optional): If true, only send to test email
        - test_email (optional): Email for test send
    """
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        data = request.json or {}
        subject = data.get('subject')
        html_content = data.get('html_content')
        test_mode = data.get('test_mode', False)
        test_email = data.get('test_email')

        if not subject or not html_content:
            return jsonify({"error": "Subject and HTML content are required"}), 400

        firestore = FirestoreService()
        email_service = EmailService()

        # Get site settings for base URL
        site_settings = firestore.get_site_settings(user_id)
        site_name = site_settings.get('site_name', 'Newsletter')

        # Test mode - send to single email
        if test_mode:
            if not test_email:
                return jsonify({"error": "Test email is required for test mode"}), 400

            result = email_service.send_single(test_email, subject, html_content)
            return jsonify({
                "success": result.get('success'),
                "message": "Test email sent" if result.get('success') else result.get('error'),
                "test_mode": True
            })

        # Production mode - send to all subscribers
        subscribers = firestore.get_newsletter_subscribers(user_id, limit=1000)

        if not subscribers:
            return jsonify({"error": "No active subscribers found"}), 400

        # Send newsletter
        result = email_service.send_batch(
            subscribers=subscribers,
            subject=subject,
            html_content=html_content,
            site_name=site_name
        )

        # Log the send
        if result.get('sent', 0) > 0:
            firestore.log_newsletter_send(
                user_id=user_id,
                recipient_count=result.get('sent', 0),
                subject=subject,
                content_preview=html_content[:500],
                html_content=html_content
            )
            firestore.log_activity(
                user_id=user_id,
                user_name=session.get('user_name', 'Admin'),
                type="newsletter",
                action_text=f"Sent newsletter to {result.get('sent', 0)} subscribers",
                target_type="newsletter",
                target_name=subject
            )

        return jsonify({
            "success": result.get('success'),
            "sent": result.get('sent', 0),
            "failed": result.get('failed', 0),
            "total_subscribers": len(subscribers)
        })

    except Exception as e:
        logger.exception("Newsletter send error")
        return jsonify({"error": str(e)}), 500


# ==================== SUBSCRIBERS MANAGEMENT ====================

@newsletter_bp.route('/api/newsletter/subscribers', methods=['GET'])
def get_subscribers():
    """Get list of newsletter subscribers."""
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        from app.utils.parallel import run_parallel_simple

        firestore = FirestoreService()
        # The list and the count are independent queries; run back to back they
        # cost the sum of two Firestore round trips (0.5-3.5 s each here) to
        # answer one request.
        subscribers, count = run_parallel_simple([
            (firestore.get_newsletter_subscribers, (user_id, 500)),
            (firestore.get_subscriber_count, (user_id,)),
        ], max_workers=2)

        return jsonify({
            "subscribers": subscribers or [],
            "count": count or 0
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@newsletter_bp.route('/api/newsletter/subscribers/count', methods=['GET'])
def get_subscriber_count():
    """Get subscriber count only (faster)."""
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        firestore = FirestoreService()
        count = firestore.get_subscriber_count(user_id)
        return jsonify({"count": count})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ==================== UNSUBSCRIBE (PUBLIC) ====================

@newsletter_bp.route('/unsubscribe', methods=['GET', 'POST'])
@limiter.limit('10 per minute; 60 per hour')
def unsubscribe():
    """Public one-click unsubscribe, reached from a newsletter footer link.

    Deliberately unauthenticated: a recipient must be able to unsubscribe
    without an account, and requiring one is both hostile and a CAN-SPAM
    problem. It is rate-limited instead, because each POST is a write and the
    ownerless path costs a collection query.

    Rendered from a template rather than an f-string: the previous version
    interpolated the `email` query parameter straight into the markup, which
    made it a reflected XSS on this origin. Autoescaping removes the whole
    class of defect rather than patching one instance.
    """
    # get_json(silent=True) rather than request.json: the confirm button posts
    # form-encoded data, and request.json raises UnsupportedMediaType on it --
    # which is why every unsubscribe attempt used to return 415.
    payload = request.get_json(silent=True) or {}

    def _field(name):
        return (
            request.form.get(name)
            or request.args.get(name)
            or payload.get(name)
            or ''
        ).strip()

    email = _field('email')[:MAX_EMAIL_LENGTH]
    owner = _field('owner')[:128]

    if request.method == 'GET':
        return render_template(
            'newsletter/unsubscribe.html',
            state='confirm',
            heading='Unsubscribe from newsletter',
            email=email,
            owner=owner,
        )

    if not email or not EMAIL_RE.match(email):
        return render_template(
            'newsletter/unsubscribe.html',
            state='error',
            heading='That address does not look right',
            message='Go back to the link in your email and try again.',
        ), 400

    try:
        removed = _unsubscribe_everywhere(email, owner)
    except Exception:
        logger.exception('Unsubscribe failed', extra={'owner': owner or None})
        return render_template(
            'newsletter/unsubscribe.html',
            state='error',
            heading='Something went wrong',
            message='We could not process that just now. Please try again shortly.',
        ), 500

    if removed:
        logger.info('Subscriber unsubscribed', extra={'owner': owner or None})
        return render_template(
            'newsletter/unsubscribe.html',
            state='done',
            heading='Unsubscribed',
            message='You have been removed from the newsletter.',
        )

    # Deliberately the same wording as success. Saying "that address is not on
    # our list" would turn this open endpoint into a subscriber-enumeration
    # oracle, and the outcome the user cares about -- no more email -- holds
    # either way.
    return render_template(
        'newsletter/unsubscribe.html',
        state='done',
        heading='Unsubscribed',
        message='You have been removed from the newsletter.',
    )


def _unsubscribe_everywhere(email, owner=None):
    """Deactivate ``email``, for one owner if known or wherever it is found.

    With an owner it is a single indexed lookup. Without one -- an older link
    that omits the parameter -- it falls back to a bounded query rather than
    scanning the whole collection.
    """
    db = FirestoreService()

    if owner:
        return bool(db.unsubscribe_newsletter(owner, email))

    query = (
        db.db.collection('newsletter_subscribers')
        .where(filter=FieldFilter('email', '==', email.lower()))
        .where(filter=FieldFilter('active', '==', True))
        .limit(10)
    )
    removed = False
    for doc in query.stream():
        owner_id = (doc.to_dict() or {}).get('site_owner_id')
        if owner_id and db.unsubscribe_newsletter(owner_id, email):
            removed = True
    return removed


# ==================== NEWSLETTER HISTORY ====================

@newsletter_bp.route('/api/newsletter/history', methods=['GET'])
def get_history():
    """Get newsletter send history."""
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        firestore = FirestoreService()
        history = firestore.get_newsletter_history(user_id, limit=20)

        return jsonify({"history": history})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@newsletter_bp.route('/api/newsletter/history/<newsletter_id>', methods=['GET'])
def get_newsletter_by_id(newsletter_id):
    """Get a single newsletter by ID."""
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        firestore = FirestoreService()
        newsletter = firestore.get_newsletter_by_id(newsletter_id, user_id)

        if newsletter:
            return jsonify({"success": True, "newsletter": newsletter})
        else:
            return jsonify({"success": False, "error": "Newsletter not found"}), 404

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@newsletter_bp.route('/api/newsletter/history/<newsletter_id>', methods=['DELETE'])
def delete_newsletter(newsletter_id):
    """Delete a newsletter from history."""
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        firestore = FirestoreService()
        success = firestore.delete_newsletter(newsletter_id, user_id)

        if success:
            firestore.log_activity(
                user_id=user_id,
                user_name=session.get('user_name', 'Admin'),
                type="newsletter",
                action_text="Deleted newsletter from history",
                target_type="newsletter",
                target_name="Newsletter"
            )
            return jsonify({"success": True})
        else:
            return jsonify({"success": False, "error": "Newsletter not found or unauthorized"}), 404

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ==================== DRAFTS ====================

@newsletter_bp.route('/api/newsletter/drafts', methods=['GET'])
def get_drafts():
    """Get newsletter drafts."""
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        firestore = FirestoreService()
        drafts = firestore.get_newsletter_drafts(user_id)
        return jsonify({"drafts": drafts})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@newsletter_bp.route('/api/newsletter/drafts', methods=['POST'])
def save_draft():
    """Save newsletter draft."""
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        data = request.json or {}
        firestore = FirestoreService()
        draft_id = firestore.save_newsletter_draft(user_id, data)

        if draft_id:
            firestore.log_activity(
                user_id=user_id,
                user_name=session.get('user_name', 'Admin'),
                type="newsletter",
                action_text="Saved newsletter draft",
                target_type="newsletter",
                target_name=data.get('subject', 'Newsletter Draft')
            )
            return jsonify({"success": True, "draft_id": draft_id})
        else:
            return jsonify({"error": "Failed to save draft"}), 500

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@newsletter_bp.route('/api/newsletter/drafts/<draft_id>', methods=['DELETE'])
def delete_draft(draft_id):
    """Delete a newsletter draft."""
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        firestore = FirestoreService()
        success = firestore.delete_newsletter_draft(draft_id, user_id)

        if success:
            firestore.log_activity(
                user_id=user_id,
                user_name=session.get('user_name', 'Admin'),
                type="newsletter",
                action_text="Deleted newsletter draft",
                target_type="newsletter",
                target_name="Newsletter Draft"
            )
            return jsonify({"success": True})
        else:
            return jsonify({"error": "Draft not found or unauthorized"}), 404

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ==================== EMAIL SERVICE STATUS ====================

@newsletter_bp.route('/api/newsletter/render', methods=['POST'])
def render_newsletter_html():
    """
    Render newsletter HTML from generated content.

    Request body:
        - subject: Email subject
        - intro: Introduction text
        - posts: List of posts with title, summary, id
        - cta_text: Call-to-action button text
        - closing: Closing message
        - base_url (optional): Base URL for links
    """
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        from jinja2 import Template

        data = request.json or {}
        firestore = FirestoreService()

        # Get site settings
        site_settings = firestore.get_site_settings(user_id)
        site_name = site_settings.get('site_name', 'Newsletter')

        # Build the public site URL (prefer slug over user_id)
        base_url = request.host_url.rstrip('/')
        site_slug = site_settings.get('site_slug') or user_id
        site_url = f"{base_url}/site/{site_slug}"

        # Prepare template data
        template_data = {
            'subject': data.get('subject', 'Newsletter'),
            'site_name': site_name,
            'intro': data.get('intro', ''),
            'posts': data.get('posts', []),
            'cta_text': data.get('cta_text', 'Visit Blog'),
            'closing': data.get('closing', 'Thanks for reading!'),
            'site_url': site_url,
            'unsubscribe_url': '{{ unsubscribe_url }}'  # Placeholder for personalization
        }

        # Render template
        template = Template(NEWSLETTER_TEMPLATE)
        html = template.render(**template_data)

        return jsonify({
            "success": True,
            "html": html
        })

    except Exception as e:
        logger.exception("Newsletter render error")
        return jsonify({"error": str(e)}), 500


@newsletter_bp.route('/api/newsletter/status', methods=['GET'])
def get_status():
    """Check email service status and subscriber count."""
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        from app.utils.parallel import run_parallel_simple

        firestore = FirestoreService()
        email_service = EmailService()

        # A Firestore count and an SMTP credential check share nothing. The
        # SMTP check is cached, so on a warm cache this is one round trip; on a
        # cold one the two overlap instead of adding up.
        subscriber_count, email_status = run_parallel_simple([
            (firestore.get_subscriber_count, (user_id,)),
            (email_service.test_connection, ()),
        ], max_workers=2)
        email_status = email_status or {'valid': False, 'error': 'check failed'}

        return jsonify({
            "subscriber_count": subscriber_count,
            "email_service": {
                "configured": email_status.get('valid', False),
                "error": email_status.get('error') if not email_status.get('valid') else None
            }
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500
