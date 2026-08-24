"""Blog categories and their post counts, scoped per site owner.

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
from app.utils.retry import retry_on_unavailable
from firebase_admin import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from app.core.logging import get_logger

logger = get_logger(__name__)


class CategoryRepository:
    """Blog categories and their post counts, scoped per site owner."""

    # Inside FirestoreService
    def get_category_names(self):
        """Fetch only category names for AI categorization."""
        try:
            docs = self.db.collection("categories").select(["name"]).stream()
            return [doc.to_dict()["name"] for doc in docs]
        except Exception:
            logger.exception("FirestoreService.get_category_names Error")
            return []

    @retry_on_unavailable
    def get_all_categories(self, user_id=None, limit=None, use_cache=True):
        """
        Fetch all categories for the user's team (stored under site owner).
        """
        if user_id:
            site_owner_id = self.get_site_owner_for_user(user_id)
        else:
            site_owner_id = None

        if use_cache and site_owner_id:
            cache_key = f"categories:{site_owner_id}:{limit}"
            cached_result = cache.get(cache_key)
            if cached_result is not None:
                return cached_result

        try:
            query = self.db.collection("categories")
            if site_owner_id:
                query = query.where(filter=FieldFilter("created_by", "==", site_owner_id))
            if limit:
                query = query.limit(limit)
            docs = query.stream()
            categories = []
            for doc in docs:
                data = doc.to_dict()
                categories.append({
                    "id": doc.id,
                    "name": data.get("name"),
                    "count": data.get("count", 0)
                })

            if use_cache and site_owner_id:
                cache.set(cache_key, categories, ttl=300)

            return categories
        except Exception:
            logger.exception("FirestoreService.get_all_categories Error")
            return []

    def get_team_categories(self, admin_id):
        """Fetch categories for admin and all their sub-users, merging duplicates."""
        try:
            sub_users = self.get_my_sub_users(admin_id)
            all_ids = [admin_id] + [u.get('uid') for u in sub_users if u.get('uid')]

            merged = {}
            for i in range(0, len(all_ids), 30):
                batch = all_ids[i:i+30]
                docs = (self.db.collection("categories")
                        .where(filter=FieldFilter("created_by", "in", batch))
                        .stream())
                for doc in docs:
                    data = doc.to_dict()
                    name = data.get("name", "").lower()
                    if name in merged:
                        merged[name]["count"] += data.get("count", 0)
                    else:
                        merged[name] = {
                            "id": doc.id,
                            "name": data.get("name"),
                            "count": data.get("count", 0)
                        }

            return list(merged.values())
        except Exception:
            logger.exception("Error fetching team categories")
            return []

    def get_user_blog_categories(self, user_id):
        """Get categories from the user's own blogs with counts."""
        try:
            docs = (self.db.collection(self.collection_name)
                    .where(filter=FieldFilter("author_id", "==", user_id))
                    .stream())

            cat_counts = {}
            for doc in docs:
                data = doc.to_dict()
                cat_name = data.get("category")
                if cat_name:
                    if cat_name in cat_counts:
                        cat_counts[cat_name] += 1
                    else:
                        cat_counts[cat_name] = 1

            categories = []
            for name, count in cat_counts.items():
                categories.append({
                    "id": name.lower().replace(" ", "-"),
                    "name": name,
                    "count": count
                })
            return categories
        except Exception:
            logger.exception("Error fetching user blog categories")
            return []

    def update_category_count(self, category_name, increment_by, user_id):
        try:
            site_owner_id = self.get_site_owner_for_user(user_id)
            cat_query = self.db.collection("categories")\
                .where(filter=FieldFilter("name", "==", category_name))\
                .where(filter=FieldFilter("created_by", "==", site_owner_id)).limit(1).get()

            if cat_query:
                cat_ref = cat_query[0].reference
                cat_ref.update({"count": firestore.Increment(increment_by)})
            else:
                self.db.collection("categories").add({
                    "name": category_name,
                    "count": 1 if increment_by > 0 else 0,
                    "created_by": site_owner_id,
                    "created_at": firestore.SERVER_TIMESTAMP
                })
                cache.clear_prefix(f"categories:{site_owner_id}")
        except Exception:
            logger.exception("Error updating category count")

    def delete_category(self, category_id, user_id):
        """
        Deletes a category if it belongs to the user's team.
        """
        try:
            site_owner_id = self.get_site_owner_for_user(user_id)
            doc_ref = self.db.collection("categories").document(category_id)
            doc = doc_ref.get()
            if not doc.exists:
                return False
            if doc.to_dict().get("created_by") != site_owner_id:
                return False
            doc_ref.delete()

            cache.clear_prefix(f"categories:{site_owner_id}")

            return True
        except Exception:
            logger.exception("Error deleting category")
            return False

    def update_category(self, category_id, update_data):
        try:
            doc_ref = self.db.collection("categories").document(category_id)
            doc_ref.update(update_data)
            return True
        except Exception:
            logger.exception("Error updating category")
            return False

# Categories functions
    def get_category_by_id(self, category_id, user_id=None):
        try:
            doc_ref = self.db.collection("categories").document(category_id)
            doc = doc_ref.get()
            if not doc.exists:
                return None
            data = doc.to_dict()
            if user_id:
                site_owner_id = self.get_site_owner_for_user(user_id)
                if data.get("created_by") != site_owner_id:
                    return None
            data["id"] = doc.id
            return data
        except Exception:
            logger.exception(f"Error fetching category {category_id}")
            return None

    def get_blogs_by_category(self, category_id, user_id):
        try:
            # Fetch the category name
            cat = self.get_category_by_id(category_id, user_id)
            if not cat:
                return []

            category_name = cat.get("name")
            docs = self.db.collection("blogs")\
                .where(filter=FieldFilter("category", "==", category_name))\
                .where(filter=FieldFilter("author_id", "==", user_id))\
                .stream()

            return [doc.to_dict() for doc in docs]
        except Exception:
            logger.exception(f"Error fetching blogs by category {category_id}")
            return []

    def update_category_name(self, category_id, new_name, user_id):
        try:
            site_owner_id = self.get_site_owner_for_user(user_id)
            doc_ref = self.db.collection("categories").document(category_id)
            doc = doc_ref.get()
            if not doc.exists:
                return False
            data = doc.to_dict()
            if data.get("created_by") != site_owner_id:
                return False
            doc_ref.update({"name": new_name})

            cache.clear_prefix(f"categories:{site_owner_id}")

            return True
        except Exception:
            logger.exception("Error updating category name")
            return False

    def create_category(self, name, user_id):
        try:
            site_owner_id = self.get_site_owner_for_user(user_id)

            existing = self.db.collection("categories")\
                .where(filter=FieldFilter("name", "==", name))\
                .where(filter=FieldFilter("created_by", "==", site_owner_id)).limit(1).get()

            if len(existing) > 0:
                return False, "Category already exists"

            doc_ref = self.db.collection("categories").add({
                "name": name,
                "count": 0,
                "created_by": site_owner_id,
                "created_at": firestore.SERVER_TIMESTAMP
            })

            cache.clear_prefix(f"categories:{site_owner_id}")

            return True, doc_ref[1].id
        except Exception as e:
            logger.exception("Error creating category")
            return False, str(e)
