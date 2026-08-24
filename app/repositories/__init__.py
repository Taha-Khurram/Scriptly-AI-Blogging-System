"""Per-domain data-access mixins composed into ``FirestoreService``.

Each module owns one domain of the Firestore schema. Import the composed
``FirestoreService`` for application code; import a single mixin when a test
needs to exercise one domain in isolation.
"""
from app.repositories.blogs import BlogRepository
from app.repositories.categories import CategoryRepository
from app.repositories.activity import ActivityRepository
from app.repositories.users import UserRepository
from app.repositories.schedule import ScheduleRepository
from app.repositories.dashboard import DashboardRepository
from app.repositories.settings import SettingsRepository
from app.repositories.contact import ContactRepository
from app.repositories.comments import CommentRepository
from app.repositories.newsletter import NewsletterRepository
from app.repositories.embeddings import EmbeddingRepository
from app.repositories.gallery import GalleryRepository
from app.repositories.seo_reports import SeoReportRepository

__all__ = [
    'BlogRepository',
    'CategoryRepository',
    'ActivityRepository',
    'UserRepository',
    'ScheduleRepository',
    'DashboardRepository',
    'SettingsRepository',
    'ContactRepository',
    'CommentRepository',
    'NewsletterRepository',
    'EmbeddingRepository',
    'GalleryRepository',
    'SeoReportRepository',
]
