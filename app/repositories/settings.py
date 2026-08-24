"""Application-wide settings and each site owner's public-site settings.

One slice of what used to be a single 3,300-line ``FirestoreService`` class.
That class was imported by every blueprint, so any data-layer change risked the
whole application, and its size made it effectively untestable -- there was no
way to exercise one domain without loading all of them.

This is a mixin, not a standalone repository object, because the methods call
each other across domain lines (creating a draft updates a category count;
listing published posts backfills slugs). Composing mixins keeps those calls
working with no rewiring, so the split is a pure move: same method set, same
behaviour, reviewable units. ``FirestoreService`` composes every mixin, so all
existing call sites are unchanged.

``self.db`` (the Firestore client) and the collection names come from
``FirestoreService.__init__``.
"""
from app.core.sanitize import sanitize_basic_html
from app.repositories._helpers import _safe_asset_url
from app.utils.cache import cache
from app.utils.date_utils import utcnow
from app.utils.retry import retry_on_unavailable
from google.cloud.firestore_v1.base_query import FieldFilter

from app.core.logging import get_logger

logger = get_logger(__name__)


class SettingsRepository:
    """Application-wide settings and each site owner's public-site settings."""

    def _get_app_settings_defaults(self):
        """Returns default app-level settings schema."""
        return {
            "app_name": "Scriptly",
            "tagline": "Create, Manage & Publish Beautiful Blogs",
            "app_logo": "",
            "app_favicon": "",
            "created_at": utcnow(),
            "updated_at": utcnow()
        }

    @retry_on_unavailable
    def get_app_settings(self):
        """Fetches app-level settings from Firestore."""
        try:
            cache_key = "app_settings"
            cached = cache.get(cache_key)
            if cached:
                return cached

            doc = self.db.collection("app_config").document("general").get()
            defaults = self._get_app_settings_defaults()

            if doc.exists:
                stored_data = doc.to_dict()
                merged = {**defaults, **stored_data}
                cache.set(cache_key, merged, ttl=300)
                return merged

            # Initialize with defaults if not exists
            self.db.collection("app_config").document("general").set(defaults)
            cache.set(cache_key, defaults, ttl=300)
            return defaults

        except Exception:
            logger.exception("Error fetching app settings")
            return self._get_app_settings_defaults()

    def update_app_settings(self, settings_data):
        """Updates app-level settings in Firestore."""
        try:
            settings_data['updated_at'] = utcnow()

            self.db.collection("app_config").document("general").set(
                settings_data,
                merge=True
            )

            # Clear cache
            cache.delete("app_settings")

            return True
        except Exception:
            logger.exception("Error updating app settings")
            return False

    def _get_site_settings_defaults(self, user_id):
        """Returns the default site settings schema."""
        return {
            "id": user_id,
            "owner_id": user_id,
            "site_slug": "",  # URL-friendly slug for clean URLs (e.g., 'my-blog' -> /site/my-blog)
            # General
            "site_name": "My Blog",
            "site_description": "Welcome to my blog",
            "niche": "",
            # Appearance
            "logo_url": "",
            "favicon_url": "",
            "primary_color": "#4318FF",
            "secondary_color": "#6366F1",
            "cover_image_url": "",
            # Content
            "posts_per_page": 10,
            "default_language": "en",
            "show_reading_time": True,
            "show_author": True,
            "featured_post_id": "",
            # SEO
            "meta_title": "",
            "meta_description": "",
            "og_image_url": "",
            "analytics_id": "",
            "custom_domain": "",
            # Social
            "social_links": {
                "twitter": "",
                "linkedin": "",
                "github": ""
            },
            "contact_email": "",
            "about_content": "",
            # Behavior
            "site_visibility": "public",
            # Locale & Timezone
            "timezone": "UTC",
            "date_format": "MMM DD, YYYY",
            "time_format": "12h",
            "locale": "en",
            # Header Settings
            "header": {
                "nav_home": "Home",
                "nav_blog": "Blog",
                "nav_about": "About",
                "nav_contact": "Contact",
                "cta_text": "Subscribe",
                "show_search": True
            },
            # Footer Settings
            "footer": {
                "copyright": "2024 {site_name}. All rights reserved.",
                "col1_title": "Navigation",
                "col2_title": "Support",
                "col3_title": "Legal & Social",
                "show_newsletter": True,
                "newsletter_title": "Stay Updated",
                "newsletter_description": "Get the latest posts delivered to your inbox."
            },
            # Hero Sections
            "hero_home": {
                "badge": "{site_name} Stories",
                "cta_secondary": "Learn More",
                "stats_label_1": "Articles",
                "stats_label_2": "Categories",
                "stats_label_3": "Readers",
                "latest_title": "Latest Posts",
                "latest_subtitle": "Curated insights from our latest published articles.",
                "view_all_text": "View All Posts",
                "about_kicker": "About the Platform",
                "about_title": "{site_name} blends practical expertise with clear publishing vision.",
                "newsletter_disclaimer": "No spam, unsubscribe anytime.",
                "newsletter_image": ""
            },
            "hero_about": {
                "subtitle": "{site_description}",
                "story_title": "Our Story",
                "values_title": "What We Stand For",
                "value_1_title": "Quality Content",
                "value_1_desc": "Every article is crafted with care and attention to detail.",
                "value_2_title": "Community First",
                "value_2_desc": "We believe in building meaningful connections.",
                "value_3_title": "Authenticity",
                "value_3_desc": "Real experiences, honest opinions, genuine insights.",
                "stats_title": "By the Numbers",
                "cta_title": "Ready to Explore?",
                "cta_subtitle": "Dive into our articles and join the conversation.",
                "badge_text": "About Us",
                "values_subtitle": "The principles that guide everything we create and share.",
                "stat_1_label": "Articles Published",
                "stat_2_label": "Categories",
                "stat_3_label": "Happy Readers",
                "stat_4_label": "Always Available",
                "stat_4_value": "24/7",
                "cta_btn_primary": "Browse Articles",
                "cta_btn_secondary": "Contact Us"
            },
            "hero_blog": {
                "title": "Our Blog",
                "subtitle": "Explore our collection of articles, guides, and insights."
            },
            "hero_contact": {
                "title": "Get in Touch",
                "subtitle": "Have questions or feedback? We would love to hear from you.",
                "form_title": "Send a Message",
                "form_subtitle": "Fill out the form and we will get back to you.",
                "faq_1_q": "How quickly do you respond?",
                "faq_1_a": "We typically respond within 24-48 hours.",
                "faq_2_q": "Can I contribute articles?",
                "faq_2_a": "Yes! We welcome guest contributions.",
                "faq_3_q": "Do you offer sponsorships?",
                "faq_3_a": "Contact us to discuss partnership opportunities.",
                "faq_4_q": "How do I report an issue?",
                "faq_4_a": "Use the contact form or email us directly."
            },

            # Permalink settings
            "permalinks": {
                "structure": "post-name",     # post-name, date-post-name, category-post-name, numeric
                "category_base": "category",  # URL base for categories (e.g., /category/tech)
                "tag_base": "tag",            # URL base for tags (e.g., /tag/python)
            },

            # SEO & Search Visibility
            "seo": {
                "indexing_enabled": True,     # Enable/disable search engine indexing
                "robots_txt_custom": "",      # Custom robots.txt content (if empty, auto-generate)
                "og_site_name": "",           # Open Graph site name
                "og_default_image": "",       # Default OG image for posts without cover
                "twitter_card": "summary_large_image",  # summary, summary_large_image
                "twitter_site": "",           # @username for site
                "google_site_verification": "",  # Google Search Console verification
                "bing_site_verification": "",    # Bing Webmaster verification
            },

            # RSS Feed Settings
            "rss": {
                "enabled": True,              # Enable/disable RSS feed
                "posts_count": 20,            # Number of posts in feed
                "content_type": "summary",    # 'full' or 'summary'
                "include_featured_image": True,  # Include cover images in feed
            },

            # Legal Pages & Cookie Consent
            "legal": {
                "contact_email": "",  # Specific email for legal pages, falls back to main contact_email
                "privacy_policy_enabled": True,
                "privacy_policy_content": """## Privacy Policy

**Last updated: {date}**

### Introduction
Welcome to {site_name}. We respect your privacy and are committed to protecting your personal data.

### Information We Collect
We may collect information you provide directly, including:
- Name and email address when you subscribe to our newsletter
- Contact information when you reach out via our contact form
- Comments and feedback you submit

### How We Use Your Information
We use the information we collect to:
- Send you newsletters and updates (if subscribed)
- Respond to your inquiries
- Improve our content and services

### Cookies
We use cookies to enhance your browsing experience. You can control cookie preferences through your browser settings.

### Third-Party Services
We may use third-party services like Google Analytics to understand how visitors use our site.

### Your Rights
You have the right to:
- Access your personal data
- Request correction of your data
- Request deletion of your data
- Unsubscribe from communications

### Contact Us
If you have questions about this Privacy Policy, please contact us at {contact_email}.
""",
                "terms_of_service_enabled": True,
                "terms_of_service_content": """## Terms of Service

**Last updated: {date}**

### Agreement to Terms
By accessing {site_name}, you agree to be bound by these Terms of Service.

### Intellectual Property
All content on this site, including text, images, and graphics, is owned by {site_name} and protected by copyright laws.

### User Conduct
You agree not to:
- Use the site for any unlawful purpose
- Attempt to gain unauthorized access
- Interfere with the site's operation
- Copy or reproduce content without permission

### Comments and Submissions
By submitting comments or content, you grant us a non-exclusive license to use, modify, and display that content.

### Disclaimer
The content on this site is provided "as is" without warranties of any kind. We do not guarantee the accuracy or completeness of any information.

### Limitation of Liability
{site_name} shall not be liable for any damages arising from your use of this site.

### Changes to Terms
We reserve the right to modify these terms at any time. Continued use of the site constitutes acceptance of updated terms.

### Contact
For questions about these Terms, contact us at {contact_email}.
""",
                "cookie_consent_enabled": True,
                "cookie_consent_text": "We use cookies to enhance your browsing experience and analyze site traffic.",
                "cookie_consent_button": "Accept",
                "cookie_consent_link_text": "Learn more",
            }
        }

    def get_site_settings(self, user_id):
        """
        Retrieves site settings for a user.
        Merges stored data with defaults to ensure all fields exist.
        Uses in-memory cache with 2-minute TTL to reduce Firestore queries.
        """
        cache_key = f"site_settings:{user_id}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            defaults = self._get_site_settings_defaults(user_id)
            doc = self.db.collection("site_settings").document(user_id).get()

            if doc.exists:
                stored_data = doc.to_dict()
                # Deep merge: defaults first, then stored data overwrites
                merged = {**defaults, **stored_data}
                merged['id'] = doc.id

                # Handle nested object merges
                nested_fields = ['social_links', 'header', 'footer',
                               'hero_home', 'hero_about', 'hero_blog', 'hero_contact', 'permalinks', 'seo', 'rss', 'legal']
                for field in nested_fields:
                    default_obj = defaults.get(field, {})
                    stored_obj = stored_data.get(field, {})
                    merged[field] = {**default_obj, **stored_obj}

                cache.set(cache_key, merged, ttl=120)
                return merged

            cache.set(cache_key, defaults, ttl=120)
            return defaults
        except Exception:
            logger.exception("Error fetching site settings")
            return self._get_site_settings_defaults(user_id)

    def resolve_site_identifier(self, identifier):
        """
        Resolves a site identifier (slug or user_id) to the actual user_id.
        Returns tuple: (user_id, settings) or (None, None) if not found.
        Supports both clean slug URLs and legacy user_id URLs for backwards compatibility.
        """
        try:
            # Check cache first for slug resolution
            cache_key = f"slug_resolve:{identifier}"
            cached = cache.get(cache_key)
            if cached:
                return cached, self.get_site_settings(cached)

            # Try direct user_id lookup first (for backwards compatibility)
            doc = self.db.collection("site_settings").document(identifier).get()
            if doc.exists:
                cache.set(cache_key, identifier, ttl=300)
                return identifier, self.get_site_settings(identifier)

            # Try slug lookup
            query = self.db.collection("site_settings").where(
                filter=FieldFilter('site_slug', '==', identifier)
            ).limit(1)
            docs = list(query.stream())

            if docs:
                user_id = docs[0].id
                cache.set(cache_key, user_id, ttl=300)
                return user_id, self.get_site_settings(user_id)

            # No site_settings document yet: if the identifier is a valid user
            # (admin hasn't saved settings), fall back to default settings so
            # the public site is still viewable instead of 404ing.
            if self.get_user_by_id(identifier):
                cache.set(cache_key, identifier, ttl=300)
                return identifier, self.get_site_settings(identifier)

            return None, None

        except Exception:
            logger.exception("Error resolving site identifier")
            return None, None

    def is_slug_available(self, slug, exclude_user_id=None):
        """
        Check if a site slug is available.
        Returns True if available, False if taken.
        """
        try:
            if not slug:
                return False

            query = self.db.collection("site_settings").where(
                filter=FieldFilter('site_slug', '==', slug)
            ).limit(1)
            docs = list(query.stream())

            if not docs:
                return True

            # If excluding a user (for updates), check if the found doc belongs to them
            if exclude_user_id and docs[0].id == exclude_user_id:
                return True

            return False

        except Exception:
            logger.exception("Error checking slug availability")
            return False

    def generate_unique_site_slug(self, base_slug, exclude_user_id=None):
        """
        Generate a unique site slug from a base slug.
        Appends numbers if slug is taken: my-blog -> my-blog-2 -> my-blog-3
        """
        from app.utils.slug_utils import generate_slug

        # Clean the base slug
        slug = generate_slug(base_slug)
        if not slug:
            slug = "my-site"

        # Check if available
        if self.is_slug_available(slug, exclude_user_id):
            return slug

        # Try with numbers
        counter = 2
        while counter < 100:  # Reasonable limit
            new_slug = f"{slug}-{counter}"
            if self.is_slug_available(new_slug, exclude_user_id):
                return new_slug
            counter += 1

        # Fallback to timestamp-based slug
        import time
        return f"{slug}-{int(time.time())}"

    def _validate_site_settings(self, settings):
        """Validates and sanitizes site settings input."""
        validated = {}

        # String fields with max lengths
        string_fields = {
            'site_name': 100,
            'site_slug': 50,
            'site_description': 500,
            'niche': 50,
            'logo_url': 500,
            'favicon_url': 500,
            'cover_image_url': 500,
            'default_language': 10,
            'featured_post_id': 100,
            'meta_title': 70,
            'meta_description': 160,
            'og_image_url': 500,
            'analytics_id': 50,
            'custom_domain': 253,
            'contact_email': 100,
            'about_content': 5000,
            'google_sheets_id': 100,
            'timezone': 50,
            'date_format': 20,
            'time_format': 5,
            'locale': 10,
        }

        # about_content is rendered with |safe on the public About page, so it
        # gets the markup allowlist rather than the plain length trim.
        html_fields = {'about_content'}
        # Fields that end up in an href/src attribute. A javascript: value in
        # logo_url or og_image_url is a stored injection on every page of the
        # site, and these are set by the site owner but rendered to the public.
        url_fields = {
            'logo_url', 'favicon_url', 'cover_image_url', 'og_image_url',
        }

        for field, max_len in string_fields.items():
            if field not in settings:
                continue
            val = str(settings[field]).strip()[:max_len]
            if field in html_fields:
                val = sanitize_basic_html(val)
            elif field in url_fields:
                val = _safe_asset_url(val)
            validated[field] = val

        # Primary color validation (hex format)
        if 'primary_color' in settings:
            color = str(settings['primary_color']).strip()
            if color.startswith('#') and len(color) in [4, 7]:
                validated['primary_color'] = color
            else:
                validated['primary_color'] = '#4318FF'

        # Secondary color validation (hex format)
        if 'secondary_color' in settings:
            color = str(settings['secondary_color']).strip()
            if color.startswith('#') and len(color) in [4, 7]:
                validated['secondary_color'] = color
            else:
                validated['secondary_color'] = '#6366F1'

        # Integer fields with bounds
        if 'posts_per_page' in settings:
            try:
                val = int(settings['posts_per_page'])
                validated['posts_per_page'] = max(1, min(50, val))
            except (ValueError, TypeError):
                validated['posts_per_page'] = 10

        # Boolean fields
        bool_fields = ['show_reading_time', 'show_author', 'activity_tracking_enabled']
        for field in bool_fields:
            if field in settings:
                validated[field] = bool(settings[field])

        # Enum validation for site_visibility
        if 'site_visibility' in settings:
            vis = str(settings['site_visibility']).lower()
            validated['site_visibility'] = vis if vis in ['public', 'unlisted'] else 'public'

        # Social links (nested object)
        if 'social_links' in settings and isinstance(settings['social_links'], dict):
            validated['social_links'] = {
                'twitter': str(settings['social_links'].get('twitter', '')).strip()[:200],
                'linkedin': str(settings['social_links'].get('linkedin', '')).strip()[:200],
                'github': str(settings['social_links'].get('github', '')).strip()[:200],
            }

        # Header settings (nested object)
        if 'header' in settings and isinstance(settings['header'], dict):
            h = settings['header']
            validated['header'] = {
                'nav_home': str(h.get('nav_home', 'Home')).strip()[:50],
                'nav_blog': str(h.get('nav_blog', 'Blog')).strip()[:50],
                'nav_about': str(h.get('nav_about', 'About')).strip()[:50],
                'nav_contact': str(h.get('nav_contact', 'Contact')).strip()[:50],
                'cta_text': str(h.get('cta_text', 'Subscribe')).strip()[:50],
                'show_search': bool(h.get('show_search', True)),
            }

        # Footer settings (nested object)
        if 'footer' in settings and isinstance(settings['footer'], dict):
            f = settings['footer']
            validated['footer'] = {
                'copyright': str(f.get('copyright', '')).strip()[:200],
                'col1_title': str(f.get('col1_title', 'Navigation')).strip()[:50],
                'col2_title': str(f.get('col2_title', 'Support')).strip()[:50],
                'col3_title': str(f.get('col3_title', 'Legal & Social')).strip()[:50],
                'show_newsletter': bool(f.get('show_newsletter', True)),
                'newsletter_title': str(f.get('newsletter_title', '')).strip()[:100],
                'newsletter_description': str(f.get('newsletter_description', '')).strip()[:300],
            }

        # Hero sections (nested objects)
        hero_sections = ['hero_home', 'hero_about', 'hero_blog', 'hero_contact']
        for section in hero_sections:
            if section in settings and isinstance(settings[section], dict):
                validated[section] = {}
                for key, val in settings[section].items():
                    if isinstance(val, str):
                        validated[section][key] = val.strip()[:500]
                    elif isinstance(val, bool):
                        validated[section][key] = val

        # Permalink settings (nested object)
        if 'permalinks' in settings and isinstance(settings['permalinks'], dict):
            p = settings['permalinks']
            valid_structures = ['post-name', 'date-post-name', 'category-post-name', 'numeric']
            structure = str(p.get('structure', 'post-name')).strip().lower()
            validated['permalinks'] = {
                'structure': structure if structure in valid_structures else 'post-name',
                'category_base': str(p.get('category_base', 'category')).strip().lower()[:50],
                'tag_base': str(p.get('tag_base', 'tag')).strip().lower()[:50],
            }
            # Sanitize URL bases (only alphanumeric and hyphens)
            import re
            validated['permalinks']['category_base'] = re.sub(r'[^a-z0-9-]', '', validated['permalinks']['category_base']) or 'category'
            validated['permalinks']['tag_base'] = re.sub(r'[^a-z0-9-]', '', validated['permalinks']['tag_base']) or 'tag'

        # SEO settings (nested object)
        if 'seo' in settings and isinstance(settings['seo'], dict):
            s = settings['seo']
            valid_twitter_cards = ['summary', 'summary_large_image']
            twitter_card = str(s.get('twitter_card', 'summary_large_image')).strip().lower()
            validated['seo'] = {
                'indexing_enabled': bool(s.get('indexing_enabled', True)),
                'robots_txt_custom': str(s.get('robots_txt_custom', '')).strip()[:2000],
                'og_site_name': str(s.get('og_site_name', '')).strip()[:100],
                'og_default_image': str(s.get('og_default_image', '')).strip()[:500],
                'twitter_card': twitter_card if twitter_card in valid_twitter_cards else 'summary_large_image',
                'twitter_site': str(s.get('twitter_site', '')).strip()[:50],
                'google_site_verification': str(s.get('google_site_verification', '')).strip()[:100],
                'bing_site_verification': str(s.get('bing_site_verification', '')).strip()[:100],
            }

        return validated

    def update_site_settings(self, user_id, settings):
        """
        Updates or creates site settings for a user.
        Validates input before saving. Invalidates cache on update.
        """
        try:
            # Validate settings
            validated = self._validate_site_settings(settings)
            validated['owner_id'] = user_id
            validated['updated_at'] = utcnow()

            doc_ref = self.db.collection("site_settings").document(user_id)
            doc_ref.set(validated, merge=True)

            # Invalidate cached settings and slug resolution
            cache.delete(f"site_settings:{user_id}")
            cache.delete(f"slug_resolve:{user_id}")
            new_slug = validated.get('site_slug', '')
            if new_slug:
                cache.delete(f"slug_resolve:{new_slug}")
            return True
        except Exception:
            logger.exception("Error updating site settings")
            return False
