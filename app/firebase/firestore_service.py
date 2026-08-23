"""The composed Firestore data layer.

This was a single class of 3,300 lines and ~120 methods spanning thirteen
unrelated domains -- blogs, categories, activity, users, invitations,
scheduling, dashboards, settings, contact, comments, newsletter, embeddings,
gallery and SEO reports. Every blueprint imported it, so any change to one
domain risked all of them, and nothing could be tested without loading
everything.

The behaviour now lives in :mod:`app.repositories`, one module per domain:

* :class:`~app.repositories.blogs.BlogRepository` -- blog documents: create, read, update, list, delete, and publish state
* :class:`~app.repositories.categories.CategoryRepository` -- blog categories and their post counts, scoped per site owner
* :class:`~app.repositories.activity.ActivityRepository` -- the audit trail: who changed what, and the feeds built from it
* :class:`~app.repositories.users.UserRepository` -- user records, team membership, and pending invitations
* :class:`~app.repositories.schedule.ScheduleRepository` -- scheduled publishing: the queue the background publisher drains
* :class:`~app.repositories.dashboard.DashboardRepository` -- batched aggregate reads that back the dashboard screens
* :class:`~app.repositories.settings.SettingsRepository` -- application-wide settings and each site owner's public-site settings
* :class:`~app.repositories.contact.ContactRepository` -- contact-form submissions from public site visitors
* :class:`~app.repositories.comments.CommentRepository` -- visitor comments and their ai/admin moderation state
* :class:`~app.repositories.newsletter.NewsletterRepository` -- newsletter subscribers, send history and drafts
* :class:`~app.repositories.embeddings.EmbeddingRepository` -- vector embeddings that back semantic search over published posts
* :class:`~app.repositories.gallery.GalleryRepository` -- metadata for the media library. the bytes live in storage_service
* :class:`~app.repositories.seo_reports.SeoReportRepository` -- saved seo audit reports

``FirestoreService`` composes them. Its public surface is byte-for-byte the
method set it had before, so every existing call site works unchanged -- the
split moved code, it did not change behaviour. New work should import the
specific mixin it needs; this class exists so the migration did not require
touching 150 call sites at once.
"""
from app.core.logging import get_logger
from app.firebase.firebase_admin import FirebaseLoader
from app.repositories import (
    BlogRepository,
    CategoryRepository,
    ActivityRepository,
    UserRepository,
    ScheduleRepository,
    DashboardRepository,
    SettingsRepository,
    ContactRepository,
    CommentRepository,
    NewsletterRepository,
    EmbeddingRepository,
    GalleryRepository,
    SeoReportRepository,
)

logger = get_logger(__name__)

# Re-exported: modules imported these helpers from this module before the split.
from app.repositories._helpers import (  # noqa: E402  (kept for import compatibility)
    _parse_filter_date,
    _safe_asset_url,
    _sanitize_blog_content,
)


class FirestoreService(
    BlogRepository,
    CategoryRepository,
    ActivityRepository,
    UserRepository,
    ScheduleRepository,
    DashboardRepository,
    SettingsRepository,
    ContactRepository,
    CommentRepository,
    NewsletterRepository,
    EmbeddingRepository,
    GalleryRepository,
    SeoReportRepository,
):
    """Every Firestore operation the application performs.

    Construction is cheap: ``FirebaseLoader.get_instance()`` returns the
    process-wide client, so the many modules that instantiate this at import
    time all share one connection pool and one gRPC channel.
    """

    def __init__(self):
        self.db = FirebaseLoader.get_instance()
        self.collection_name = "blogs"
        self.activity_collection = "activities"
        self.user_collection = "users"
