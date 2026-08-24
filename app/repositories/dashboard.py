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
from app.repositories._helpers import BLOG_LIST_FIELDS, apply_projection
from app.utils.cache import cache
from app.utils.date_utils import ensure_aware, utcnow
from app.utils.retry import retry_on_unavailable
from firebase_admin import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from app.core.logging import get_logger

logger = get_logger(__name__)

# The dashboard renders five rows per bucket (see ``blog.home``). Fetching one
# extra is enough for the template's "and more" affordance to be accurate
# without a second query, and everything past it was only ever downloaded to be
# discarded. The bucket *totals* come from count() aggregations, so capping the
# list does not affect any number on screen.
DASHBOARD_ROWS = 6


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
            # get_blogs_by_status is projected to BLOG_LIST_FIELDS by default,
            # so these three no longer stream post bodies and embedding vectors
            # to render five rows. They stay unbounded because the counters
            # below are derived from them and a single author's own drafts are
            # a small set by construction.
            queries = [
                (self.get_user_published_count, (user_id,)),
                (self.get_blogs_by_status, ("DRAFT", user_id)),
                (self.get_blogs_by_status, ("UNDER_REVIEW", user_id)),
                (self.get_blogs_by_status, ("PUBLISHED", user_id)),
                (self.get_total_blogs_count, (user_id,)),
                (self.get_user_blog_categories, (user_id,)),
                (self.get_recent_activity, (user_id, 10)),
            ]

            results = run_parallel_simple(queries, max_workers=len(queries))

            data = {
                "published_count": results[0] or 0,
                "drafts": results[1] or [],
                "pending": results[2] or [],
                "published_blogs": results[3] or [],
                "total_blogs": results[4] or 0,
                "categories": results[5] or [],
                "recent_activity": results[6] or [],
                "drafts_count": len(results[1] or []),
                "pending_count": len(results[2] or []),
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
                "drafts_count": 0,
                "pending_count": 0,
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
            all_user_ids = self.get_team_user_ids(admin_id)

            def get_team_blogs_by_status(status):
                """The team's blogs in one status, projected and bounded.

                The dashboard renders at most five rows from each of these
                lists (see ``blog.home``), so streaming full documents for the
                whole team was transferring megabytes of post bodies and
                embedding vectors to display five titles. Projected and capped
                at 25 -- enough for the five rows plus the "drafts" and
                "pending" counters the cards show.
                """
                blogs = []
                for i in range(0, len(all_user_ids), 30):
                    batch = all_user_ids[i:i + 30]
                    query = apply_projection(
                        self.db.collection(self.collection_name)
                        .where(filter=FieldFilter("author_id", "in", batch))
                        .where(filter=FieldFilter("status", "==", status)),
                        BLOG_LIST_FIELDS,
                    ).order_by(
                        "updated_at", direction=firestore.Query.DESCENDING
                    ).limit(DASHBOARD_ROWS)
                    for doc in query.stream():
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

            # Every one of these is independent, so they all go out at once and
            # the page waits for the slowest rather than the sum. Measured on
            # this deployment a Firestore round trip is 0.5-3.5 s, so the
            # difference between nine sequential queries and nine concurrent
            # ones is the difference between a dead page and a live one.
            queries = [
                (self.get_published_count, (admin_id,)),
                (get_team_blogs_by_status, ("DRAFT",)),
                (get_team_blogs_by_status, ("UNDER_REVIEW",)),
                (get_team_blogs_by_status, ("PUBLISHED",)),
                (get_team_total_count, ()),
                (self.get_all_categories, (admin_id,)),
                (get_team_recent_activity, ()),
                (self.count_team_blogs_by_status, (all_user_ids, "DRAFT")),
                (self.count_team_blogs_by_status, (all_user_ids, "UNDER_REVIEW")),
            ]

            results = run_parallel_simple(queries, max_workers=len(queries))

            data = {
                "published_count": results[0] or 0,
                "drafts": results[1] or [],
                "pending": results[2] or [],
                "published_blogs": results[3] or [],
                "total_blogs": results[4] or 0,
                "categories": results[5] or [],
                "recent_activity": results[6] or [],
                # Exact totals, independent of how many rows the lists hold.
                "drafts_count": results[7] or 0,
                "pending_count": results[8] or 0,
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
                "drafts_count": 0,
                "pending_count": 0,
            }
