"""Blog documents: create, read, update, list, delete, and publish state.

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
from app.repositories._helpers import _parse_filter_date, _sanitize_blog_content
from app.utils.cache import cache
from app.utils.date_utils import ensure_aware, utcnow
from app.utils.retry import retry_on_unavailable
from datetime import datetime
from firebase_admin import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from app.core.logging import get_logger

logger = get_logger(__name__)


class BlogRepository:
    """Blog documents: create, read, update, list, delete, and publish state."""

    @retry_on_unavailable
    def get_blog_by_id(self, blog_id):
        """Fetches a blog and ensures content is a string so TinyMCE can display it."""
        try:
            doc = self.db.collection(self.collection_name).document(blog_id).get()
            if doc.exists:
                data = doc.to_dict()
                data['id'] = doc.id
                
                # --- FIX: ROBUST CONTENT HANDLING ---
                raw_content = data.get('content', '')
                
                if raw_content is None:
                    data['content'] = ""
                elif isinstance(raw_content, dict):
                    # If content was accidentally saved as a map/dict, extract known keys
                    data['content'] = raw_content.get('body', raw_content.get('text', ''))
                else:
                    # Ensure it is a string (prevents issues if it's an int or other type)
                    data['content'] = str(raw_content)
                # ------------------------------------
                    
                return data
            return None
        except Exception:
            logger.exception(f"Error fetching blog {blog_id}")
            return None

    def create_draft(self, blog_data, user_id):
        """Saves blog as DRAFT, increments category count, and generates unique slug."""
        try:
            from app.utils.slug_utils import generate_slug, ensure_unique_slug

            blog_data['created_at'] = firestore.SERVER_TIMESTAMP
            blog_data['updated_at'] = utcnow()
            blog_data['author_id'] = user_id
            # Sanitised on the way in, so nothing downstream has to remember to.
            if 'content' in blog_data:
                blog_data['content'] = _sanitize_blog_content(blog_data['content'])
            blog_data['site_owner_id'] = self.get_site_owner_for_user(user_id)
            blog_data['status'] = blog_data.get('status', 'DRAFT').upper()

            # Generate unique slug from title
            site_owner = blog_data['site_owner_id']
            title = blog_data.get('title', 'Untitled')
            base_slug = generate_slug(title)
            existing_slugs = self._get_user_slugs(site_owner)
            blog_data['slug'] = ensure_unique_slug(base_slug, existing_slugs)
            blog_data['old_slugs'] = []
            blog_data['numeric_id'] = self._get_next_numeric_id(site_owner)

            doc_ref = self.db.collection(self.collection_name).add(blog_data)
            blog_id = doc_ref[1].id

            # Use site_owner_id for category management
            category_name = blog_data.get('category')
            if category_name:
                self.update_category_count(category_name, 1, site_owner)

            try:
                from app.services.google_sheets_service import GoogleSheetsService
                sheets = GoogleSheetsService.get_instance()
                sid = GoogleSheetsService.get_spreadsheet_id_for_user(user_id)
                sheets.sync_blog(blog_id, title, blog_data['status'],
                                 category_name or '', user_id, None, blog_data['updated_at'],
                                 blog_data.get('author', ''), spreadsheet_id=sid)
            except Exception:
                pass

            return blog_id
        except Exception:
            logger.exception("Firestore Error creating draft")
            return None

    def update_blog_content(self, blog_id, title, content, new_slug=None, seo_title=None, seo_description=None, cover_image=None):
        """
        Updates the title and body content of a blog post.
        If new_slug is provided and different from current, updates slug and tracks old one.
        If title changes and no new_slug provided, auto-generates new slug from title.
        Also handles SEO meta title and description fields.
        """
        try:
            from app.utils.slug_utils import generate_slug, ensure_unique_slug

            doc_ref = self.db.collection(self.collection_name).document(blog_id)
            doc = doc_ref.get()

            if not doc.exists:
                return False

            current_data = doc.to_dict()
            current_slug = current_data.get('slug', '')
            current_title = current_data.get('title', '')

            update_data = {
                'title': title,
                'content': _sanitize_blog_content(content),
                'updated_at': utcnow()
            }

            # Update SEO fields if provided
            if seo_title is not None:
                update_data['seo_title'] = seo_title
            if seo_description is not None:
                update_data['seo_description'] = seo_description
            if cover_image is not None:
                update_data['cover_image'] = cover_image

            # Determine slug to use
            slug_to_set = new_slug

            # If no explicit slug provided and title changed, auto-generate new slug
            if not slug_to_set and title != current_title:
                base_slug = generate_slug(title)
                user_id = current_data.get('site_owner_id') or current_data.get('author_id')
                if user_id:
                    existing_slugs = self._get_user_slugs(user_id)
                    # Remove current slug from existing to allow keeping it
                    existing_slugs.discard(current_slug)
                    slug_to_set = ensure_unique_slug(base_slug, existing_slugs)
                else:
                    slug_to_set = base_slug

            # Update slug if we have a new one different from current
            if slug_to_set and slug_to_set != current_slug:
                # Store old slug for 301 redirects
                old_slugs = current_data.get('old_slugs', [])
                if current_slug and current_slug not in old_slugs:
                    old_slugs.append(current_slug)
                # Keep only last 10 old slugs
                update_data['old_slugs'] = old_slugs[-10:]
                update_data['slug'] = slug_to_set

            doc_ref.update(update_data)
            return True
        except Exception:
            logger.exception("Error updating blog content")
            return False

    @retry_on_unavailable
    def get_blogs_by_status(self, status, user_id):
        """Filters blogs by status AND author."""
        try:
            docs = self.db.collection(self.collection_name)\
                .where(filter=FieldFilter('author_id', '==', user_id))\
                .where(filter=FieldFilter('status', '==', status.upper())).stream()
            
            blogs = []
            for doc in docs:
                data = doc.to_dict()
                data['id'] = doc.id
                blogs.append(data)
            return blogs
        except Exception:
            logger.exception(f"Error fetching blogs by status {status}")
            return []

    def get_approval_queue(self, admin_id):
        """
        Returns pending blogs for an admin's approval queue:
        - Blogs submitted by the admin themselves
        - Blogs submitted by users created by this admin
        """
        try:
            # Step 1: Get users created by this admin
            users_ref = self.db.collection("users")
            user_docs = users_ref.where("created_by", "==", admin_id).stream()
            user_ids = [user.id for user in user_docs]

            # Include admin themselves
            user_ids.append(admin_id)

            # Step 2: Fetch pending blogs for these users (batched if needed)
            blogs_ref = self.db.collection("blogs")
            pending_blogs = []

            batch_size = 10  # Firestore 'in' query limit
            for i in range(0, len(user_ids), batch_size):
                batch_ids = user_ids[i:i + batch_size]
                docs = (
                    blogs_ref
                    .where("author_id", "in", batch_ids)
                    .where("status", "==", "UNDER_REVIEW")
                    .order_by("updated_at", direction=firestore.Query.DESCENDING)
                    .stream()
                )
                for doc in docs:
                    data = doc.to_dict()
                    data["id"] = doc.id
                    pending_blogs.append(data)

            return pending_blogs

        except Exception as e:
            logger.exception("Approval Queue Error:", e)
            return []

    def get_total_blogs_count(self, user_id):
        try:
            count_query = self.db.collection(self.collection_name)\
                                     .where(filter=FieldFilter('author_id', '==', user_id)).count()
            count_result = count_query.get()
            return count_result[0][0].value
        except Exception:
            logger.exception("Error getting total blogs count")
            return 0

    def get_paginated_drafts(self, user_id, page=1, per_page=10):
        try:
            skip = (page - 1) * per_page
            query = self.db.collection(self.collection_name)\
                           .where(filter=FieldFilter('author_id', '==', user_id))\
                           .where(filter=FieldFilter('status', '==', 'DRAFT'))\
                           .order_by('updated_at', direction=firestore.Query.DESCENDING)\
                           .offset(skip)\
                           .limit(per_page)
            
            drafts = []
            for doc in query.stream():
                data = doc.to_dict()
                data['id'] = doc.id
                drafts.append(data)

            total_count_query = self.db.collection(self.collection_name)\
                                           .where(filter=FieldFilter('author_id', '==', user_id))\
                                           .where(filter=FieldFilter('status', '==', 'DRAFT'))\
                                           .count()
            total_count = total_count_query.get()[0][0].value

            return drafts, total_count
        except Exception:
            logger.exception("Error fetching paginated drafts")
            return [], 0

    def get_all_blogs_filtered(self, user_ids, status_filter='all', category_filter='all',
                                search='', date_from='', date_to='', page=1, per_page=10):
        try:
            from app.utils.parallel import run_parallel_simple

            # Batch-fetch all user names in parallel (instead of N sequential calls)
            def fetch_user_name(uid):
                doc = self.db.collection(self.user_collection).document(uid).get()
                if doc.exists:
                    u = doc.to_dict()
                    return (uid, u.get('name') or u.get('email', '').split('@')[0] or 'Unknown')
                return (uid, 'Unknown')

            user_tasks = [(fetch_user_name, (uid,)) for uid in user_ids]
            user_results = run_parallel_simple(user_tasks, max_workers=min(len(user_ids), 10))
            user_name_map = {uid: name for uid, name in user_results if uid}

            all_blogs = []
            for i in range(0, len(user_ids), 30):
                batch_ids = user_ids[i:i+30]
                docs = (self.db.collection(self.collection_name)
                        .where(filter=FieldFilter("author_id", "in", batch_ids))
                        .stream())
                for doc in docs:
                    data = doc.to_dict()
                    data['id'] = doc.id
                    data['author_name'] = user_name_map.get(data.get('author_id'), 'Unknown')
                    all_blogs.append(data)

            all_blogs.sort(key=lambda x: x.get('updated_at') or x.get('created_at') or datetime.min, reverse=True)

            filtered = []
            for b in all_blogs:
                if status_filter != 'all' and b.get('status', '').upper() != status_filter.upper():
                    continue

                if category_filter != 'all' and b.get('category', '').lower() != category_filter.lower():
                    continue

                # Both sides are made timezone-aware before comparing. The
                # previous code stripped tzinfo from the stored value, which
                # compares two naive datetimes that only coincidentally agree,
                # and raises TypeError the moment one of them is aware.
                if date_from or date_to:
                    updated = ensure_aware(
                        b.get('updated_at') or b.get('created_at')
                    )
                    if date_from:
                        from_date = _parse_filter_date(date_from)
                        if from_date and updated and updated < from_date:
                            continue
                    if date_to:
                        to_date = _parse_filter_date(date_to, end_of_day=True)
                        if to_date and updated and updated > to_date:
                            continue

                if search:
                    search_lower = search.lower()
                    searchable = f"{b.get('title', '')} {b.get('category', '')} {b.get('author', '')}".lower()
                    if search_lower not in searchable:
                        continue

                filtered.append(b)

            total = len(filtered)
            total_pages = max(1, (total + per_page - 1) // per_page)
            start = (page - 1) * per_page
            page_blogs = filtered[start:start + per_page]

            # Serialize timestamps
            for b in page_blogs:
                for field in ['updated_at', 'created_at']:
                    val = b.get(field)
                    if val and hasattr(val, 'isoformat'):
                        b[field] = val.isoformat()

            return {
                "blogs": page_blogs,
                "total": total,
                "page": page,
                "per_page": per_page,
                "total_pages": total_pages
            }
        except Exception:
            logger.exception("Error fetching filtered blogs")
            return {"blogs": [], "total": 0, "page": 1, "per_page": per_page, "total_pages": 1}

    def delete_blog(self, blog_id):
        try:
            blog_ref = self.db.collection(self.collection_name).document(blog_id)
            blog_snap = blog_ref.get()
            if not blog_snap.exists:
                return False

            blog_data = blog_snap.to_dict()
            category_name = blog_data.get("category")
            user_id = blog_data.get("author_id")

            @firestore.transactional
            def delete_in_transaction(transaction):
                if category_name and user_id:
                    cat_query = self.db.collection("categories")\
                        .where(filter=FieldFilter("name", "==", category_name))\
                        .where(filter=FieldFilter("created_by", "==", user_id)).limit(1)
                    cat_docs = cat_query.get(transaction=transaction)
                    if len(cat_docs) > 0:
                        transaction.update(cat_docs[0].reference, {"count": firestore.Increment(-1)})
                transaction.delete(blog_ref)
                return True

            transaction = self.db.transaction()
            return delete_in_transaction(transaction)
        except Exception:
            logger.exception("Error deleting blog")
            return False

    @retry_on_unavailable
    def get_published_count(self, user_id):
        """Get count of published blogs for a site owner (includes team members' blogs)."""
        try:
            from app.utils.parallel import run_parallel_simple

            def count_by_site_owner():
                q = self.db.collection(self.collection_name)\
                    .where(filter=FieldFilter('site_owner_id', '==', user_id))\
                    .where(filter=FieldFilter('status', '==', 'PUBLISHED'))\
                    .count()
                return q.get()[0][0].value

            def count_by_author():
                q = self.db.collection(self.collection_name)\
                    .where(filter=FieldFilter('author_id', '==', user_id))\
                    .where(filter=FieldFilter('status', '==', 'PUBLISHED'))\
                    .count()
                return q.get()[0][0].value

            results = run_parallel_simple([
                (count_by_site_owner, ()),
                (count_by_author, ()),
            ], max_workers=2)

            site_owner_count = results[0] or 0
            author_count = results[1] or 0
            return max(site_owner_count, author_count)
        except Exception:
            logger.exception("Error getting published blogs count")
            return 0

    def get_user_published_count(self, user_id):
        """Get count of published blogs authored by this specific user only."""
        try:
            count_query = self.db.collection(self.collection_name)\
                                .where(filter=FieldFilter('author_id', '==', user_id))\
                                .where(filter=FieldFilter('status', '==', 'PUBLISHED'))\
                                .count()
            count_result = count_query.get()
            return count_result[0][0].value
        except Exception:
            logger.exception("Error getting user published count")
            return 0

    def update_blog_status(self, blog_id, new_status, scheduled_at=None, scheduled_by=None):
        """Updates blog status and invalidates published blogs cache."""
        try:
            doc_ref = self.db.collection("blogs").document(blog_id)

            # Get blog to find site_owner_id for cache invalidation
            doc = doc_ref.get()
            site_owner_id = None
            if doc.exists:
                data = doc.to_dict()
                site_owner_id = data.get('site_owner_id') or data.get('author_id')

            update_data = {
                "status": new_status,
                "updated_at": utcnow()
            }

            if new_status == "SCHEDULED" and scheduled_at:
                update_data["scheduled_at"] = scheduled_at
                update_data["scheduled_by"] = scheduled_by
            elif new_status == "PUBLISHED":
                # Keep scheduled_at so published blogs remain on calendar
                update_data["scheduled_by"] = firestore.DELETE_FIELD
            elif new_status != "SCHEDULED":
                update_data["scheduled_at"] = firestore.DELETE_FIELD
                update_data["scheduled_by"] = firestore.DELETE_FIELD

            doc_ref.update(update_data)

            # Invalidate published blogs cache for this site owner
            if site_owner_id:
                cache.clear_prefix(f"published_blogs:{site_owner_id}")

            try:
                from app.services.google_sheets_service import GoogleSheetsService
                sheets = GoogleSheetsService.get_instance()
                sid = GoogleSheetsService.get_spreadsheet_id_for_user(data.get('author_id', ''))
                sheets.sync_blog(blog_id, data.get('title', ''), new_status,
                                 data.get('category', ''), data.get('author_id', ''),
                                 data.get('created_at'), update_data['updated_at'],
                                 data.get('author', ''), spreadsheet_id=sid)
            except Exception:
                pass

            return True
        except Exception as e:
            logger.exception("Firestore Status Error:", e)
            return False

    @retry_on_unavailable
    def get_published_blogs(self, user_id, limit=20):
        """
        Fetches published blogs for the public site.
        Returns blogs ordered by updated_at descending.
        Filters by site_owner_id to include blogs from all team members.
        Falls back to author_id for backwards compatibility with older blogs.
        Uses in-memory cache with 2-minute TTL to reduce Firestore queries.
        Runs both queries in parallel for faster response times.
        """
        cache_key = f"published_blogs:{user_id}:{limit}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            from app.utils.parallel import run_parallel_simple

            def _fetch_by_site_owner():
                results = []
                query = self.db.collection(self.collection_name)\
                    .where(filter=FieldFilter('site_owner_id', '==', user_id))\
                    .where(filter=FieldFilter('status', '==', 'PUBLISHED'))
                for doc in query.stream():
                    data = doc.to_dict()
                    data['id'] = doc.id
                    raw_content = data.get('content', '')
                    if isinstance(raw_content, dict):
                        data['content'] = raw_content
                    else:
                        data['content'] = {'body': str(raw_content) if raw_content else ''}
                    data = self._ensure_blog_slug(data, doc.id)
                    results.append(data)
                return results

            def _fetch_by_author():
                results = []
                query = self.db.collection(self.collection_name)\
                    .where(filter=FieldFilter('author_id', '==', user_id))\
                    .where(filter=FieldFilter('status', '==', 'PUBLISHED'))
                for doc in query.stream():
                    data = doc.to_dict()
                    data['id'] = doc.id
                    raw_content = data.get('content', '')
                    if isinstance(raw_content, dict):
                        data['content'] = raw_content
                    else:
                        data['content'] = {'body': str(raw_content) if raw_content else ''}
                    data = self._ensure_blog_slug(data, doc.id)
                    results.append(data)
                return results

            parallel_results = run_parallel_simple([
                (_fetch_by_site_owner, ()),
                (_fetch_by_author, ()),
            ], max_workers=2)

            site_owner_blogs = parallel_results[0] or []
            author_blogs = parallel_results[1] or []

            # Merge and deduplicate
            blog_ids = {b['id'] for b in site_owner_blogs}
            blogs = site_owner_blogs + [b for b in author_blogs if b['id'] not in blog_ids]

            def _sort_key(blog):
                val = blog.get('updated_at')
                if val is None:
                    return 0.0
                if hasattr(val, 'timestamp'):
                    return val.timestamp()
                return 0.0

            blogs.sort(key=_sort_key, reverse=True)

            result = blogs[:limit] if limit else blogs
            cache.set(cache_key, result, ttl=120)
            return result
        except Exception:
            logger.exception("Error fetching published blogs")
            return []

    def get_published_blog_by_id(self, blog_id):
        """
        Fetches a single published blog by ID.
        Returns None if blog doesn't exist or is not published.
        Auto-generates slug if missing.
        """
        try:
            doc = self.db.collection(self.collection_name).document(blog_id).get()
            if doc.exists:
                data = doc.to_dict()
                # Only return if published
                if data.get('status') != 'PUBLISHED':
                    return None
                data['id'] = doc.id
                # Process content for display
                raw_content = data.get('content', '')
                if isinstance(raw_content, dict):
                    data['content'] = raw_content
                else:
                    data['content'] = {'body': str(raw_content) if raw_content else ''}
                # Ensure slug exists (auto-migrate if needed)
                data = self._ensure_blog_slug(data, doc.id)
                return data
            return None
        except Exception:
            logger.exception(f"Error fetching published blog {blog_id}")
            return None

    def get_published_blog_by_slug(self, user_id, slug):
        """
        Fetches a published blog by slug.
        Also checks old_slugs for 301 redirect handling.
        Returns dict with 'blog', 'redirect' (bool), and 'new_slug' (if redirect).
        """
        try:
            # Try current slug first
            query = self.db.collection(self.collection_name)\
                .where(filter=FieldFilter('site_owner_id', '==', user_id))\
                .where(filter=FieldFilter('slug', '==', slug))\
                .where(filter=FieldFilter('status', '==', 'PUBLISHED'))\
                .limit(1)
            docs = list(query.stream())
            if docs:
                data = docs[0].to_dict()
                data['id'] = docs[0].id
                # Process content for display
                raw_content = data.get('content', '')
                if isinstance(raw_content, dict):
                    data['content'] = raw_content
                else:
                    data['content'] = {'body': str(raw_content) if raw_content else ''}
                return {'blog': data, 'redirect': False}

            # Check old_slugs for 301 redirect
            query = self.db.collection(self.collection_name)\
                .where(filter=FieldFilter('site_owner_id', '==', user_id))\
                .where(filter=FieldFilter('old_slugs', 'array_contains', slug))\
                .where(filter=FieldFilter('status', '==', 'PUBLISHED'))\
                .limit(1)
            docs = list(query.stream())
            if docs:
                data = docs[0].to_dict()
                data['id'] = docs[0].id
                # Process content for display
                raw_content = data.get('content', '')
                if isinstance(raw_content, dict):
                    data['content'] = raw_content
                else:
                    data['content'] = {'body': str(raw_content) if raw_content else ''}
                return {'blog': data, 'redirect': True, 'new_slug': data.get('slug')}

            return None
        except Exception:
            logger.exception(f"Error fetching blog by slug {slug}")
            return None

    def _get_user_slugs(self, user_id):
        """
        Gets all existing slugs for a user's blogs (for uniqueness check).
        Returns a set of slugs.
        """
        try:
            slugs = set()
            query = self.db.collection(self.collection_name)\
                .where(filter=FieldFilter('site_owner_id', '==', user_id))\
                .select(['slug'])
            for doc in query.stream():
                data = doc.to_dict()
                if data.get('slug'):
                    slugs.add(data['slug'])
            return slugs
        except Exception:
            logger.exception("Error fetching user slugs")
            return set()

    def _get_next_numeric_id(self, user_id):
        """
        Gets the next numeric ID for a user's blogs (for numeric permalink structure).
        Returns the next available integer ID.
        """
        try:
            query = self.db.collection(self.collection_name)\
                .where(filter=FieldFilter('site_owner_id', '==', user_id))\
                .order_by('numeric_id', direction=firestore.Query.DESCENDING)\
                .limit(1)
            docs = list(query.stream())
            if docs:
                data = docs[0].to_dict()
                return (data.get('numeric_id') or 0) + 1
            return 1
        except Exception:
            # If query fails (e.g., no index), fallback to count
            try:
                count = len(list(self.db.collection(self.collection_name)
                    .where(filter=FieldFilter('site_owner_id', '==', user_id))
                    .select([]).stream()))
                return count + 1
            except:
                return 1

    def _ensure_blog_slug(self, blog_data, blog_id):
        """
        Ensures a blog has a slug. If not, generates one from the title and saves it.
        This handles migration of existing blogs that don't have slugs.
        Returns the blog data with slug guaranteed to be set.
        """
        if blog_data.get('slug'):
            return blog_data

        try:
            from app.utils.slug_utils import generate_slug, ensure_unique_slug

            title = blog_data.get('title', 'Untitled')
            base_slug = generate_slug(title)

            # Get existing slugs for this user
            user_id = blog_data.get('site_owner_id') or blog_data.get('author_id')
            if user_id:
                existing_slugs = self._get_user_slugs(user_id)
                slug = ensure_unique_slug(base_slug, existing_slugs)
            else:
                slug = base_slug

            # Save the slug to the database
            self.db.collection(self.collection_name).document(blog_id).update({
                'slug': slug,
                'old_slugs': []
            })

            blog_data['slug'] = slug
            blog_data['old_slugs'] = []

        except Exception:
            logger.exception(f"Error ensuring blog slug for {blog_id}")
            # Fallback: use the document ID as slug
            blog_data['slug'] = blog_id

        return blog_data
