"""The audit trail: who changed what, and the feeds built from it.

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
from app.utils.date_utils import ensure_aware, utcnow
from datetime import datetime, timezone
from firebase_admin import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from app.core.logging import get_logger

logger = get_logger(__name__)


class ActivityRepository:
    """The audit trail: who changed what, and the feeds built from it."""

    def log_activity(self, user_id, user_name, type, action_text, blog_title="",
                     target_type=None, target_id=None, target_name=None, metadata=None):
        try:
            doc_data = {
                "user_id": user_id,
                "user_name": user_name,
                "type": type,
                "action_text": action_text,
                "blog_title": blog_title,
                "timestamp": utcnow(),
                "created_at": firestore.SERVER_TIMESTAMP
            }
            if target_type:
                doc_data["target_type"] = target_type
            if target_id:
                doc_data["target_id"] = target_id
            if target_name:
                doc_data["target_name"] = target_name
            if metadata:
                doc_data["metadata"] = metadata
            doc_ref = self.db.collection(self.activity_collection).document()
            doc_ref.set(doc_data)

            try:
                from app.services.google_sheets_service import GoogleSheetsService
                sheets = GoogleSheetsService.get_instance()
                details = target_name or ""
                if metadata:
                    details = str(metadata)
                sid = GoogleSheetsService.get_spreadsheet_id_for_user(user_id)
                sheets.log_activity(user_name, type, action_text, blog_title, details, spreadsheet_id=sid)
            except Exception:
                pass

            return True
        except Exception:
            logger.exception("Error logging activity")
            return False

    def get_recent_activity(self, user_id, limit=10):
        try:
            docs = (self.db.collection(self.activity_collection)
                        .where(filter=FieldFilter("user_id", "==", user_id))
                        .order_by("timestamp", direction=firestore.Query.DESCENDING)
                        .limit(limit)
                        .stream())
            activities = []
            now = utcnow()
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
            return activities
        except Exception:
            logger.exception("Error fetching activities")
            return []

    def get_all_activity_for_admin(self, admin_id, type_filter='all', user_filter='all',
                                    search='', date_from='', date_to='', page=1, per_page=10):
        try:
            sub_users = self.get_my_sub_users(admin_id)
            user_ids = [admin_id] + [u.get('uid') for u in sub_users if u.get('uid')]

            all_activities = []
            # Firestore 'in' supports max 30 values, batch if needed
            for i in range(0, len(user_ids), 30):
                batch_ids = user_ids[i:i+30]
                docs = (self.db.collection(self.activity_collection)
                        .where(filter=FieldFilter("user_id", "in", batch_ids))
                        .order_by("timestamp", direction=firestore.Query.DESCENDING)
                        .limit(500)
                        .stream())
                for doc in docs:
                    data = doc.to_dict()
                    data['id'] = doc.id
                    self._normalize_activity(data)
                    all_activities.append(data)

            # Sort combined results
            all_activities.sort(key=lambda x: x.get('timestamp', datetime.min), reverse=True)

            # Apply filters
            filtered = []
            type_map = {
                'blog': ['blog', 'generated', 'edited', 'published', 'deleted', 'status_change', 'seo_optimized'],
                'user': ['user'],
                'comment': ['comment'],
                'settings': ['settings'],
                'newsletter': ['newsletter'],
                'category': ['category']
            }

            for a in all_activities:
                # Type filter
                if type_filter != 'all':
                    allowed_types = type_map.get(type_filter, [type_filter])
                    if a.get('type') not in allowed_types and a.get('target_type') != type_filter:
                        continue

                # User filter
                if user_filter != 'all' and a.get('user_id') != user_filter:
                    continue

                # Date filter
                if date_from:
                    try:
                        from_date = datetime.strptime(date_from, '%Y-%m-%d')
                        ts = a.get('timestamp')
                        if isinstance(ts, datetime) and ts < from_date:
                            continue
                    except (ValueError, TypeError):
                        pass

                if date_to:
                    try:
                        to_date = datetime.strptime(date_to, '%Y-%m-%d').replace(hour=23, minute=59, second=59)
                        ts = a.get('timestamp')
                        if isinstance(ts, datetime) and ts > to_date:
                            continue
                    except (ValueError, TypeError):
                        pass

                # Search filter
                if search:
                    search_lower = search.lower()
                    searchable = f"{a.get('action_text', '')} {a.get('target_name', '')} {a.get('blog_title', '')} {a.get('user_name', '')}".lower()
                    if search_lower not in searchable:
                        continue

                filtered.append(a)

            total = len(filtered)
            total_pages = max(1, (total + per_page - 1) // per_page)
            start = (page - 1) * per_page
            page_activities = filtered[start:start + per_page]

            # Serialize timestamps for JSON
            for a in page_activities:
                ts = a.get('timestamp')
                if isinstance(ts, datetime):
                    a['timestamp'] = ts.isoformat()
                ca = a.get('created_at')
                if ca and hasattr(ca, 'isoformat'):
                    a['created_at'] = ca.isoformat()

            return {
                "activities": page_activities,
                "total": total,
                "page": page,
                "per_page": per_page,
                "total_pages": total_pages
            }
        except Exception:
            logger.exception("Error fetching admin activities")
            return {"activities": [], "total": 0, "page": 1, "per_page": per_page, "total_pages": 1}

    def get_activity_stats(self, admin_id):
        try:
            sub_users = self.get_my_sub_users(admin_id)
            user_ids = [admin_id] + [u.get('uid') for u in sub_users if u.get('uid')]

            stats = {"total": 0, "blog": 0, "user": 0, "comment": 0, "settings": 0, "newsletter": 0, "category": 0}
            blog_types = ['blog', 'generated', 'edited', 'published', 'deleted', 'status_change', 'seo_optimized']

            for i in range(0, len(user_ids), 30):
                batch_ids = user_ids[i:i+30]
                docs = (self.db.collection(self.activity_collection)
                        .where(filter=FieldFilter("user_id", "in", batch_ids))
                        .stream())
                for doc in docs:
                    data = doc.to_dict()
                    stats["total"] += 1
                    act_type = data.get('target_type') or data.get('type', '')
                    if act_type in blog_types:
                        stats["blog"] += 1
                    elif act_type == 'user':
                        stats["user"] += 1
                    elif act_type == 'comment':
                        stats["comment"] += 1
                    elif act_type == 'settings':
                        stats["settings"] += 1
                    elif act_type == 'newsletter':
                        stats["newsletter"] += 1
                    elif act_type == 'category':
                        stats["category"] += 1
                    else:
                        stats["blog"] += 1

            return stats
        except Exception:
            logger.exception("Error fetching activity stats")
            return {"total": 0, "blog": 0, "user": 0, "comment": 0, "settings": 0, "newsletter": 0, "category": 0}

    def _normalize_activity(self, data):
        if not data.get('target_type'):
            old_type = data.get('type', '')
            if old_type in ('generated', 'edited', 'published', 'deleted', 'status_change', 'seo_optimized'):
                data['target_type'] = 'blog'
                data['target_name'] = data.get('blog_title', '')
            elif old_type == 'comment':
                data['target_type'] = 'comment'
                data['target_name'] = data.get('blog_title', '')
            elif old_type == 'settings':
                data['target_type'] = 'settings'
                data['target_name'] = 'Settings'
            elif old_type == 'category':
                data['target_type'] = 'category'
                data['target_name'] = data.get('blog_title', '')
            else:
                data['target_type'] = 'blog'
                data['target_name'] = data.get('blog_title', '')
        # Normalise to a timezone-aware datetime so a later sort or comparison
        # cannot mix naive and aware values and raise TypeError.
        ts = data.get('timestamp')
        if ts is not None:
            aware = ensure_aware(ts)
            if aware is not None:
                data['timestamp'] = aware
