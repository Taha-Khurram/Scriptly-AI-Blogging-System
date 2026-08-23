"""Scheduled publishing: the queue the background publisher drains.

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
from datetime import datetime, timezone
from google.cloud.firestore_v1.base_query import FieldFilter

from app.core.logging import get_logger

logger = get_logger(__name__)


class ScheduleRepository:
    """Scheduled publishing: the queue the background publisher drains."""

    def get_scheduled_blogs(self, site_owner_id):
        """Returns all scheduled blogs for a site owner, sorted by scheduled_at."""
        try:
            blogs_ref = self.db.collection("blogs")
            docs = (
                blogs_ref
                .where(filter=FieldFilter("status", "==", "SCHEDULED"))
                .stream()
            )
            results = []
            for doc in docs:
                data = doc.to_dict()
                owner = data.get("site_owner_id") or data.get("author_id")
                if owner == site_owner_id:
                    data["id"] = doc.id
                    results.append(data)
            results.sort(key=lambda x: x.get("scheduled_at") or datetime.min)
            return results
        except Exception:
            logger.exception("Error fetching scheduled blogs")
            return []

    def get_due_scheduled_blogs(self):
        """Returns blogs that are SCHEDULED and past their scheduled_at time."""
        try:
            from datetime import timezone
            now = datetime.now(timezone.utc)
            blogs_ref = self.db.collection("blogs")
            docs = (
                blogs_ref
                .where(filter=FieldFilter("status", "==", "SCHEDULED"))
                .stream()
            )
            results = []
            for doc in docs:
                data = doc.to_dict()
                scheduled_at = data.get("scheduled_at")
                if scheduled_at:
                    # Ensure both are timezone-aware for comparison
                    if scheduled_at.tzinfo is None:
                        scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
                    if scheduled_at <= now:
                        data["id"] = doc.id
                        results.append(data)
            return results
        except Exception:
            logger.exception("Error fetching due scheduled blogs")
            return []

    def get_all_scheduled_for_calendar(self, site_owner_id):
        """Returns scheduled and published (previously scheduled) blogs for the calendar page."""
        try:
            from datetime import timezone
            blogs_ref = self.db.collection("blogs")
            results = []

            # Get all user IDs under this admin
            user_ids = [site_owner_id]
            try:
                user_docs = self.db.collection("users").where("created_by", "==", site_owner_id).stream()
                for u in user_docs:
                    user_ids.append(u.id)
            except Exception:
                pass

            logger.info(f"[Calendar] Querying for user_ids: {user_ids}")

            # Query by author_id only (avoids composite index requirement), filter status in Python
            batch_size = 10
            for i in range(0, len(user_ids), batch_size):
                batch_ids = user_ids[i:i + batch_size]
                docs = (
                    blogs_ref
                    .where(filter=FieldFilter("author_id", "in", batch_ids))
                    .stream()
                )
                for doc in docs:
                    data = doc.to_dict()
                    status = data.get("status")
                    scheduled_at = data.get("scheduled_at")

                    if status == "SCHEDULED" and scheduled_at:
                        data["id"] = doc.id
                        results.append(data)
                    elif status == "PUBLISHED" and scheduled_at:
                        data["id"] = doc.id
                        results.append(data)

            logger.info(f"[Calendar] Found {len(results)} blogs (scheduled + published with scheduled_at)")

            def sort_key(x):
                dt = x.get("scheduled_at")
                if dt is None:
                    return datetime.min.replace(tzinfo=timezone.utc)
                if hasattr(dt, 'tzinfo') and dt.tzinfo is None:
                    return dt.replace(tzinfo=timezone.utc)
                return dt

            results.sort(key=sort_key)
            return results
        except Exception:
            logger.exception("Error fetching calendar blogs")
            return []

    def save_schedule_entry(self, blog_id, title, scheduled_at, author_id, site_owner_id,
                            category=None, author_name=None):
        """Save a schedule entry so it persists on the calendar even after publishing.

        category / author_name are denormalised on purpose. The calendar reads this
        collection alone, so without them it would need one blog read per entry plus
        one user read per author just to label a row — and the route that used to
        claim those two fields was in fact defaulting every entry to
        "General" / "Unknown", because nothing had ever written them.
        """
        try:
            doc_ref = self.db.collection("schedule_entries").document(blog_id)
            doc_ref.set({
                "blog_id": blog_id,
                "title": title,
                "scheduled_at": scheduled_at,
                "author_id": author_id,
                "author_name": author_name or "",
                "category": category or "",
                "site_owner_id": site_owner_id,
                "status": "SCHEDULED",
                "created_at": utcnow()
            })
            return True
        except Exception:
            logger.exception("Error saving schedule entry")
            return False

    def update_schedule_entry_status(self, blog_id, new_status):
        """Update the status of a schedule entry (PUBLISHED or CANCELLED)."""
        try:
            doc_ref = self.db.collection("schedule_entries").document(blog_id)
            doc_ref.update({"status": new_status, "updated_at": utcnow()})
            return True
        except Exception:
            logger.exception("Error updating schedule entry")
            return False

    def delete_schedule_entry(self, blog_id):
        """Delete a schedule entry (when schedule is cancelled)."""
        try:
            self.db.collection("schedule_entries").document(blog_id).delete()
            return True
        except Exception:
            logger.exception("Error deleting schedule entry")
            return False

    def get_schedule_entries_for_calendar(self, site_owner_id):
        """Get all schedule entries for the calendar page."""
        try:
            entries_ref = self.db.collection("schedule_entries")
            docs = entries_ref.where(filter=FieldFilter("site_owner_id", "==", site_owner_id)).stream()
            results = []
            for doc in docs:
                data = doc.to_dict()
                data["id"] = data.get("blog_id", doc.id)
                results.append(data)
            return results
        except Exception:
            logger.exception("Error fetching schedule entries")
            return []
