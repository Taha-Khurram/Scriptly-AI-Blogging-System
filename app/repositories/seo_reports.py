"""Saved SEO audit reports.

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
from app.utils.date_utils import utcnow
from firebase_admin import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from app.core.logging import get_logger

logger = get_logger(__name__)


class SeoReportRepository:
    """Saved SEO audit reports."""

    def save_seo_report(self, user_id, report_data):
        try:
            doc_ref = self.db.collection("seo_reports").document()
            doc_ref.set({
                "user_id": user_id,
                "blog_id": report_data.get("blog_id", ""),
                "blog_title": report_data.get("new_title", ""),
                "original_score": report_data.get("original_score", 0),
                "seo_score": report_data.get("seo_score", 0),
                "score_improvement": report_data.get("score_improvement", 0),
                "seo_grade": report_data.get("seo_grade", ""),
                "primary_keyword": report_data.get("primary_keyword", {}),
                "comparison": report_data.get("comparison", {}),
                "changes_made": report_data.get("changes_made", []),
                "recommendations": report_data.get("recommendations", []),
                "new_title": report_data.get("new_title", ""),
                "created_at": firestore.SERVER_TIMESTAMP,
                "timestamp": utcnow()
            })
            return doc_ref.id
        except Exception:
            logger.exception("Error saving SEO report")
            return None

    def get_user_seo_reports(self, user_id, limit=50):
        try:
            docs = (self.db.collection("seo_reports")
                    .where(filter=FieldFilter("user_id", "==", user_id))
                    .order_by("timestamp", direction=firestore.Query.DESCENDING)
                    .limit(limit)
                    .stream())
            reports = []
            for doc in docs:
                data = doc.to_dict()
                data["id"] = doc.id
                if "timestamp" in data and data["timestamp"]:
                    data["timestamp"] = data["timestamp"].isoformat()
                if "created_at" in data and data["created_at"]:
                    data["created_at"] = data["created_at"].isoformat() if hasattr(data["created_at"], 'isoformat') else str(data["created_at"])
                reports.append(data)
            return reports
        except Exception:
            logger.exception("Error fetching SEO reports")
            return []

    def delete_seo_report(self, report_id, user_id):
        try:
            doc_ref = self.db.collection("seo_reports").document(report_id)
            doc = doc_ref.get()
            if not doc.exists:
                return False
            if doc.to_dict().get("user_id") != user_id:
                return False
            doc_ref.delete()
            return True
        except Exception:
            logger.exception("Error deleting SEO report")
            return False
