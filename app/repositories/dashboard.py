"""Batched aggregate reads that back the dashboard screens.

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
from app.utils.cache import cache
from app.utils.date_utils import ensure_aware, utcnow
from app.utils.retry import retry_on_unavailable
from firebase_admin import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from app.core.logging import get_logger

logger = get_logger(__name__)


class DashboardRepository:
    """Batched aggregate reads that back the dashboard screens."""

    @retry_on_unavailable
    def get_dashboard_data(self, user_id):
        """
        Fetch dashboard data for a regular user (their own blogs only).
        """
        cache_key = f"dashboard:{user_id}"
        cached = cache.get(cache_key)
        if cached:
            return cached

        from app.utils.parallel import run_parallel_simple

        try:
            queries = [
                (self.get_user_published_count, (user_id,)),
                (self.get_blogs_by_status, ("DRAFT", user_id)),
                (self.get_blogs_by_status, ("UNDER_REVIEW", user_id)),
                (self.get_blogs_by_status, ("PUBLISHED", user_id)),
                (self.get_total_blogs_count, (user_id,)),
                (self.get_user_blog_categories, (user_id,)),
                (self.get_recent_activity, (user_id, 10)),
            ]

            results = run_parallel_simple(queries, max_workers=7)

            data = {
                "published_count": results[0] or 0,
                "drafts": results[1] or [],
                "pending": results[2] or [],
                "published_blogs": results[3] or [],
                "total_blogs": results[4] or 0,
                "categories": results[5] or [],
                "recent_activity": results[6] or [],
            }
            cache.set(cache_key, data, ttl=180)
            return data
        except Exception:
            logger.exception("Error fetching dashboard data")
            return {
                "published_count": 0,
                "drafts": [],
                "pending": [],
                "published_blogs": [],
                "total_blogs": 0,
                "categories": [],
                "recent_activity": [],
            }

    @retry_on_unavailable
    def get_admin_dashboard_data(self, admin_id):
        """
        Fetch dashboard data for admin including all team members' blogs.
        """
        cache_key = f"admin_dashboard:{admin_id}"
        cached = cache.get(cache_key)
        if cached:
            return cached
        from app.utils.parallel import run_parallel_simple

        try:
            sub_users = self.get_my_sub_users(admin_id)
            all_user_ids = [admin_id] + [u.get('uid') for u in sub_users if u.get('uid')]

            def get_team_blogs_by_status(status):
                blogs = []
                for i in range(0, len(all_user_ids), 30):
                    batch = all_user_ids[i:i+30]
                    docs = (self.db.collection(self.collection_name)
                            .where(filter=FieldFilter("author_id", "in", batch))
                            .where(filter=FieldFilter("status", "==", status))
                            .stream())
                    for doc in docs:
                        data = doc.to_dict()
                        data['id'] = doc.id
                        blogs.append(data)
                return blogs

            def get_team_total_count():
                total = 0
                for i in range(0, len(all_user_ids), 30):
                    batch = all_user_ids[i:i+30]
                    count_query = (self.db.collection(self.collection_name)
                                   .where(filter=FieldFilter("author_id", "in", batch))
                                   .count())
                    count_result = count_query.get()
                    total += count_result[0][0].value
                return total

            def get_team_recent_activity():
                activities = []
                now = utcnow()
                for i in range(0, len(all_user_ids), 30):
                    batch = all_user_ids[i:i+30]
                    docs = (self.db.collection(self.activity_collection)
                            .where(filter=FieldFilter("user_id", "in", batch))
                            .order_by("timestamp", direction=firestore.Query.DESCENDING)
                            .limit(10)
                            .stream())
                    for doc in docs:
                        data = doc.to_dict()
                        if 'timestamp' in data:
                            ts = ensure_aware(data['timestamp'])
                            diff = now - ts
                            if diff.days > 0:
                                data['timestamp'] = f"{diff.days}d ago"
                            elif diff.seconds > 3600:
                                data['timestamp'] = f"{diff.seconds // 3600}h ago"
                            elif diff.seconds > 60:
                                data['timestamp'] = f"{diff.seconds // 60}m ago"
                            else:
                                data['timestamp'] = "Just now"
                        activities.append(data)
                activities.sort(key=lambda x: x.get('timestamp', ''), reverse=False)
                return activities[:10]

            queries = [
                (self.get_published_count, (admin_id,)),
                (get_team_blogs_by_status, ("DRAFT",)),
                (get_team_blogs_by_status, ("UNDER_REVIEW",)),
                (get_team_blogs_by_status, ("PUBLISHED",)),
                (get_team_total_count, ()),
                (self.get_all_categories, (admin_id,)),
                (get_team_recent_activity, ()),
            ]

            results = run_parallel_simple(queries, max_workers=7)

            data = {
                "published_count": results[0] or 0,
                "drafts": results[1] or [],
                "pending": results[2] or [],
                "published_blogs": results[3] or [],
                "total_blogs": results[4] or 0,
                "categories": results[5] or [],
                "recent_activity": results[6] or [],
            }
            cache.set(cache_key, data, ttl=180)
            return data
        except Exception:
            logger.exception("Error fetching admin dashboard data")
            return {
                "published_count": 0,
                "drafts": [],
                "pending": [],
                "published_blogs": [],
                "total_blogs": 0,
                "categories": [],
                "recent_activity": [],
            }
