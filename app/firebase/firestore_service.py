from datetime import datetime
from app.firebase.firebase_admin import FirebaseLoader
from firebase_admin import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from app.utils.cache import cache
from app.utils.retry import retry_on_unavailable

class FirestoreService:
    def __init__(self):
        self.db = FirebaseLoader.get_instance()
        self.collection_name = "blogs"
        self.activity_collection = "activities"
        self.user_collection = "users" 

    # ---------------- BLOG METHODS ----------------

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
        except Exception as e:
            print(f"❌ Error fetching blog {blog_id}: {e}")
            return None

    def create_draft(self, blog_data, user_id):
        """Saves blog as DRAFT, increments category count, and generates unique slug."""
        try:
            from app.utils.slug_utils import generate_slug, ensure_unique_slug

            blog_data['created_at'] = firestore.SERVER_TIMESTAMP
            blog_data['updated_at'] = datetime.utcnow()
            blog_data['author_id'] = user_id
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
        except Exception as e:
            print(f"❌ Firestore Error creating draft: {e}")
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
                'content': content,
                'updated_at': datetime.utcnow()
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
        except Exception as e:
            print(f"❌ Error updating blog content: {e}")
            return False

    # def update_blog_status(self, blog_id, status):
    #     try:
    #         doc_ref = self.db.collection(self.collection_name).document(blog_id)
    #         doc_ref.update({
    #             'status': status.upper(),
    #             'updated_at': datetime.utcnow()
    #         })
    #         return True
    #     except Exception as e:
    #         print(f"❌ Error updating status: {e}")
    #         return False
    

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
        except Exception as e:
            print(f"❌ Error fetching blogs by status {status}: {e}")
            return []

    # def get_approval_queue(self, admin_id):
    #     """Fetches blogs pending approval from users created by this specific admin."""
    #     try:
    #         sub_users = self.db.collection(self.user_collection)\
    #             .where(filter=FieldFilter('created_by', '==', admin_id)).stream()
    #         uids = [u.id for u in sub_users]
            
    #         if not uids:
    #             return []

    #         docs = self.db.collection(self.collection_name)\
    #             .where(filter=FieldFilter('status', '==', 'PENDING_APPROVAL'))\
    #             .where(filter=FieldFilter('author_id', 'in', uids)).stream()
            
    #         return [{**doc.to_dict(), 'id': doc.id} for doc in docs]
    #     except Exception as e:
    #         print(f"❌ Error fetching approval queue: {e}")
    #         return []
    
     
    # def get_approval_queue(self):
    #     try:
    #         docs = (
    #             self.db.collection("blogs")
    #             .where("status", "==", "UNDER_REVIEW")
    #             .order_by("updated_at", direction=firestore.Query.DESCENDING)
    #             .stream()
    #         )

    #         blogs = []
    #         for doc in docs:
    #             data = doc.to_dict()
    #             data["id"] = doc.id
    #             blogs.append(data)

    #         return blogs

    #     except Exception as e:
    #         print("Approval Queue Error:", e)
    #         return []
    
    
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
            print("Approval Queue Error:", e)
            return []
    def get_total_blogs_count(self, user_id):
        try:
            count_query = self.db.collection(self.collection_name)\
                                     .where(filter=FieldFilter('author_id', '==', user_id)).count()
            count_result = count_query.get()
            return count_result[0][0].value
        except Exception as e:
            print(f"❌ Error getting total blogs count: {e}")
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
        except Exception as e:
            print(f"❌ Error fetching paginated drafts: {e}")
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

                if date_from:
                    try:
                        from_date = datetime.strptime(date_from, '%Y-%m-%d')
                        updated = b.get('updated_at') or b.get('created_at')
                        if updated and hasattr(updated, 'replace'):
                            updated = updated.replace(tzinfo=None)
                        if isinstance(updated, datetime) and updated < from_date:
                            continue
                    except (ValueError, TypeError):
                        pass

                if date_to:
                    try:
                        to_date = datetime.strptime(date_to, '%Y-%m-%d').replace(hour=23, minute=59, second=59)
                        updated = b.get('updated_at') or b.get('created_at')
                        if updated and hasattr(updated, 'replace'):
                            updated = updated.replace(tzinfo=None)
                        if isinstance(updated, datetime) and updated > to_date:
                            continue
                    except (ValueError, TypeError):
                        pass

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
        except Exception as e:
            print(f"❌ Error fetching filtered blogs: {e}")
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
        except Exception as e:
            print(f"❌ Error deleting blog: {e}")
            return False

    # ---------------- CATEGORY METHODS ----------------

    # def get_all_categories(self, user_id=None):
    #     try:
    #         query = self.db.collection("categories")
    #         if user_id:
    #             query = query.where(filter=FieldFilter("created_by", "==", user_id))
                
    #         docs = query.stream()
    #         categories = []
    #         for doc in docs:
    #             data = doc.to_dict()
    #             data['id'] = doc.id
    #             categories.append(data)
    #         return categories 
    #     except Exception as e:
    #         print(f"❌ Error fetching categories: {e}")
    #         return []
    
    # Inside FirestoreService
    def get_category_names(self):
        """Fetch only category names for AI categorization."""
        try:
            docs = self.db.collection("categories").select(["name"]).stream()
            return [doc.to_dict()["name"] for doc in docs]
        except Exception as e:
            print(f"❌ FirestoreService.get_category_names Error: {e}")
            return []
        
    # def get_all_categories(self, user_id=None):
    #     """
    #     Fetches all categories, only returns 'name' field.
    #     Optional filter by user_id (maps to 'created_by').
    #     """
    #     try:
    #         query = self.db.collection("categories")
    #         if user_id:
    #             query = query.where("created_by", "==", user_id)  # FIX: was 'user_id'
    #         docs = query.select(["name"]).stream()
    #         return [{"name": doc.to_dict()["name"]} for doc in docs]
    #     except Exception as e:
    #         print(f"❌ FirestoreService.get_all_categories Error: {e}")
    #         return []
    
    
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
        except Exception as e:
            print(f"FirestoreService.get_all_categories Error: {e}")
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
        except Exception as e:
            print(f"Error fetching team categories: {e}")
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
        except Exception as e:
            print(f"Error fetching user blog categories: {e}")
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
        except Exception as e:
            print(f"Error updating category count: {e}")

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
        except Exception as e:
            print(f"Error deleting category: {e}")
            return False

    def update_category(self, category_id, update_data):
        try:
            doc_ref = self.db.collection("categories").document(category_id)
            doc_ref.update(update_data)
            return True
        except Exception as e:
            print(f"❌ Error updating category: {e}")
            return False

    # ---------------- ACTIVITY METHODS ----------------

    def log_activity(self, user_id, user_name, type, action_text, blog_title="",
                     target_type=None, target_id=None, target_name=None, metadata=None):
        try:
            doc_data = {
                "user_id": user_id,
                "user_name": user_name,
                "type": type,
                "action_text": action_text,
                "blog_title": blog_title,
                "timestamp": datetime.utcnow(),
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
        except Exception as e:
            print(f"❌ Error logging activity: {e}")
            return False

    def get_recent_activity(self, user_id, limit=10):
        try:
            docs = (self.db.collection(self.activity_collection)
                        .where(filter=FieldFilter("user_id", "==", user_id))
                        .order_by("timestamp", direction=firestore.Query.DESCENDING)
                        .limit(limit)
                        .stream())
            activities = []
            now = datetime.utcnow()
            for doc in docs:
                data = doc.to_dict()
                if 'timestamp' in data:
                    ts = data['timestamp'].replace(tzinfo=None)
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
        except Exception as e:
            print(f"❌ Error fetching activities: {e}")
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
        except Exception as e:
            print(f"❌ Error fetching admin activities: {e}")
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
        except Exception as e:
            print(f"❌ Error fetching activity stats: {e}")
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
        # Ensure timestamp is datetime for sorting
        ts = data.get('timestamp')
        if ts and hasattr(ts, 'replace'):
            try:
                data['timestamp'] = ts.replace(tzinfo=None)
            except (AttributeError, TypeError):
                pass

    # ---------------- USER METHODS ----------------

    def save_user(self, user_data):
        try:
            user_id = user_data.get('uid')
            if not user_id:
                return None
            user_ref = self.db.collection(self.user_collection).document(user_id)
            existing_user = user_ref.get()

            if not existing_user.exists:
                user_data["role"] = user_data.get("role", "ADMIN")
                user_data["profile_image"] = user_data.get("profile_image", "")
                user_data["created_at"] = firestore.SERVER_TIMESTAMP
                user_data["created_by"] = user_data.get("created_by", None)
                user_data["last_login"] = firestore.SERVER_TIMESTAMP
                user_ref.set(user_data)
                return user_data
            else:
                user_ref.update({"last_login": firestore.SERVER_TIMESTAMP})
                return existing_user.to_dict()
        except Exception as e:
            print(f"❌ Error saving user: {e}")
            return None

    def update_last_login(self, user_id):
        """Update last_login timestamp (fire-and-forget)."""
        try:
            self.db.collection(self.user_collection).document(user_id).update({
                "last_login": firestore.SERVER_TIMESTAMP
            })
        except Exception:
            pass

    @retry_on_unavailable
    def get_user_by_id(self, user_id):
        """Gets a user document by their ID."""
        try:
            if not user_id:
                return None
            doc = self.db.collection(self.user_collection).document(user_id).get()
            if doc.exists:
                return doc.to_dict()
            return None
        except Exception as e:
            print(f"❌ Error getting user: {e}")
            return None

    def update_user_profile(self, user_id, profile_data):
        try:
            if not user_id:
                return None
            user_ref = self.db.collection(self.user_collection).document(user_id)
            user_ref.update(profile_data)
            return self.get_user_by_id(user_id)
        except Exception as e:
            print(f"❌ Error updating user profile: {e}")
            return None

    def get_my_sub_users(self, admin_id):
        try:
            docs = self.db.collection(self.user_collection)\
                .where(filter=FieldFilter('created_by', '==', admin_id)).stream()
            return [{**doc.to_dict(), 'uid': doc.id} for doc in docs]
        except Exception as e:
            print(f"❌ Error fetching sub-users: {e}")
            return []

    def get_site_owner_for_user(self, user_id):
        """
        Gets the site owner for a user.
        - If user is an ADMIN or has no created_by, they are their own site owner
        - If user was created by an admin, that admin is the site owner
        """
        try:
            user = self.get_user_by_id(user_id)
            if not user:
                return user_id  # Fallback to self

            # If user is admin or wasn't created by anyone, they own their own site
            if user.get('role') == 'ADMIN' or not user.get('created_by'):
                return user_id

            # Return the admin who created this user
            return user.get('created_by')
        except Exception as e:
            print(f"❌ Error getting site owner: {e}")
            return user_id  # Fallback to self

    # ---------------- INVITATION METHODS ----------------

    def create_invitation(self, email, role, invited_by):
        email = email.lower().strip()
        try:
            existing_users = self.db.collection(self.user_collection)\
                .where(filter=FieldFilter('email', '==', email)).stream()
            if any(True for _ in existing_users):
                return {"success": False, "error": "A user with this email already exists"}

            existing_invites = self.db.collection('invitations')\
                .where(filter=FieldFilter('email', '==', email))\
                .where(filter=FieldFilter('invited_by', '==', invited_by))\
                .where(filter=FieldFilter('status', '==', 'pending')).stream()
            for doc in existing_invites:
                data = doc.to_dict()
                data['id'] = doc.id
                return {"success": True, "invitation": data, "already_existed": True}

            inv_data = {
                "email": email,
                "role": role.upper(),
                "invited_by": invited_by,
                "invited_at": firestore.SERVER_TIMESTAMP,
                "status": "pending"
            }
            doc_ref = self.db.collection('invitations').add(inv_data)
            inv_data['id'] = doc_ref[1].id
            return {"success": True, "invitation": inv_data}
        except Exception as e:
            print(f"❌ Error creating invitation: {e}")
            return {"success": False, "error": str(e)}

    def get_pending_invitation_by_email(self, email):
        email = email.lower().strip()
        try:
            docs = self.db.collection('invitations')\
                .where(filter=FieldFilter('email', '==', email))\
                .where(filter=FieldFilter('status', '==', 'pending')).stream()
            invitations = []
            for doc in docs:
                data = doc.to_dict()
                data['id'] = doc.id
                invitations.append(data)
            if not invitations:
                return None
            invitations.sort(key=lambda x: x.get('invited_at') or datetime.min, reverse=True)
            return invitations[0]
        except Exception as e:
            print(f"❌ Error checking invitation: {e}")
            return None

    def accept_invitation(self, invitation_id):
        try:
            self.db.collection('invitations').document(invitation_id).update({
                "status": "accepted",
                "accepted_at": datetime.utcnow()
            })
            return True
        except Exception as e:
            print(f"❌ Error accepting invitation: {e}")
            return False

    def get_invitations_by_admin(self, admin_id):
        try:
            docs = self.db.collection('invitations')\
                .where(filter=FieldFilter('invited_by', '==', admin_id)).stream()
            invitations = []
            for doc in docs:
                data = doc.to_dict()
                data['id'] = doc.id
                if data.get('invited_at') and hasattr(data['invited_at'], 'isoformat'):
                    data['invited_at'] = data['invited_at'].isoformat()
                if data.get('accepted_at') and hasattr(data['accepted_at'], 'isoformat'):
                    data['accepted_at'] = data['accepted_at'].isoformat()
                invitations.append(data)
            invitations.sort(key=lambda x: x.get('invited_at') or '', reverse=True)
            return invitations
        except Exception as e:
            print(f"❌ Error fetching invitations: {e}")
            return []

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
        except Exception as e:
            print(f"Error getting published blogs count: {e}")
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
        except Exception as e:
            print(f"Error getting user published count: {e}")
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
                "updated_at": datetime.utcnow()
            }

            if new_status == "SCHEDULED" and scheduled_at:
                update_data["scheduled_at"] = scheduled_at
                update_data["scheduled_by"] = scheduled_by
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
            print("Firestore Status Error:", e)
            return False

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
        except Exception as e:
            print(f"Error fetching scheduled blogs: {e}")
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
        except Exception as e:
            print(f"Error fetching due scheduled blogs: {e}")
            return []

    def get_all_scheduled_for_calendar(self, site_owner_id):
        """Returns scheduled blogs for the calendar page."""
        try:
            from datetime import timezone
            blogs_ref = self.db.collection("blogs")
            results = []

            scheduled_docs = (
                blogs_ref
                .where(filter=FieldFilter("status", "==", "SCHEDULED"))
                .stream()
            )
            for doc in scheduled_docs:
                data = doc.to_dict()
                owner = data.get("site_owner_id") or data.get("author_id")
                if owner == site_owner_id:
                    data["id"] = doc.id
                    results.append(data)

            def sort_key(x):
                dt = x.get("scheduled_at")
                if dt is None:
                    return datetime.min.replace(tzinfo=timezone.utc)
                if hasattr(dt, 'tzinfo') and dt.tzinfo is None:
                    return dt.replace(tzinfo=timezone.utc)
                return dt

            results.sort(key=sort_key)
            return results
        except Exception as e:
            print(f"Error fetching calendar blogs: {e}")
            return []
        
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
        except Exception as e:
            print(f"Error fetching category {category_id}: {e}")
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
        except Exception as e:
            print(f"❌ Error fetching blogs by category {category_id}: {e}")
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
        except Exception as e:
            print(f"Error updating category name: {e}")
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
            print(f"Error creating category: {e}")
            return False, str(e)

    # ---------------- OPTIMIZED BATCH METHODS ----------------

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
        except Exception as e:
            print(f"Error fetching dashboard data: {e}")
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
                now = datetime.utcnow()
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
                            ts = data['timestamp'].replace(tzinfo=None)
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
        except Exception as e:
            print(f"Error fetching admin dashboard data: {e}")
            return {
                "published_count": 0,
                "drafts": [],
                "pending": [],
                "published_blogs": [],
                "total_blogs": 0,
                "categories": [],
                "recent_activity": [],
            }

    # ---------------- APP SETTINGS METHODS ----------------

    def _get_app_settings_defaults(self):
        """Returns default app-level settings schema."""
        return {
            "app_name": "Scriptly",
            "tagline": "Create, Manage & Publish Beautiful Blogs",
            "app_logo": "",
            "app_favicon": "",
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
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

        except Exception as e:
            print(f"❌ Error fetching app settings: {e}")
            return self._get_app_settings_defaults()

    def update_app_settings(self, settings_data):
        """Updates app-level settings in Firestore."""
        try:
            settings_data['updated_at'] = datetime.utcnow()

            self.db.collection("app_config").document("general").set(
                settings_data,
                merge=True
            )

            # Clear cache
            cache.delete("app_settings")

            return True
        except Exception as e:
            print(f"❌ Error updating app settings: {e}")
            return False

    # ---------------- SITE SETTINGS METHODS ----------------

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
                "badge": "{niche}",
                "title": "Welcome to {site_name}",
                "subtitle": "{site_description}",
                "cta_primary": "Explore Articles",
                "cta_secondary": "Learn More",
                "stats_label_1": "Articles",
                "stats_label_2": "Categories",
                "stats_label_3": "Readers"
            },
            "hero_about": {
                "title": "About {site_name}",
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
                "cta_subtitle": "Dive into our articles and join the conversation."
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
        except Exception as e:
            print(f"❌ Error fetching site settings: {e}")
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

            return None, None

        except Exception as e:
            print(f"❌ Error resolving site identifier: {e}")
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

        except Exception as e:
            print(f"❌ Error checking slug availability: {e}")
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

        for field, max_len in string_fields.items():
            if field in settings:
                val = str(settings[field]).strip()[:max_len]
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
        bool_fields = ['show_reading_time', 'show_author']
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
            validated['updated_at'] = datetime.utcnow()

            doc_ref = self.db.collection("site_settings").document(user_id)
            doc_ref.set(validated, merge=True)

            # Invalidate cached settings and slug resolution
            cache.delete(f"site_settings:{user_id}")
            cache.delete(f"slug_resolve:{user_id}")
            new_slug = validated.get('site_slug', '')
            if new_slug:
                cache.delete(f"slug_resolve:{new_slug}")
            return True
        except Exception as e:
            print(f"❌ Error updating site settings: {e}")
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
        except Exception as e:
            print(f"❌ Error fetching published blogs: {e}")
            import traceback
            traceback.print_exc()
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
        except Exception as e:
            print(f"❌ Error fetching published blog {blog_id}: {e}")
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
        except Exception as e:
            print(f"❌ Error fetching blog by slug {slug}: {e}")
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
        except Exception as e:
            print(f"❌ Error fetching user slugs: {e}")
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
        except Exception as e:
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

        except Exception as e:
            print(f"❌ Error ensuring blog slug for {blog_id}: {e}")
            # Fallback: use the document ID as slug
            blog_data['slug'] = blog_id

        return blog_data

    # ---------------- CONTACT & NEWSLETTER METHODS ----------------

    def save_contact_submission(self, user_id, data):
        """
        Saves a contact form submission to Firestore.
        Stores in 'contact_submissions' collection.
        """
        try:
            submission = {
                'site_owner_id': user_id,
                'name': data.get('name', '').strip()[:100],
                'email': data.get('email', '').strip()[:100],
                'subject': data.get('subject', '').strip()[:200],
                'message': data.get('message', '').strip()[:5000],
                'created_at': firestore.SERVER_TIMESTAMP,
                'read': False
            }
            doc_ref = self.db.collection('contact_submissions').add(submission)
            return doc_ref[1].id
        except Exception as e:
            print(f"❌ Error saving contact submission: {e}")
            return None

    def get_contact_submissions(self, user_id, page=1, per_page=10, status_filter='all', search=''):
        """Get paginated contact submissions for a site owner."""
        try:
            query = self.db.collection('contact_submissions')\
                .where(filter=FieldFilter('site_owner_id', '==', user_id))

            docs = list(query.stream())
            docs.sort(key=lambda d: d.to_dict().get('created_at') or '', reverse=True)

            if status_filter == 'unread':
                docs = [d for d in docs if not d.to_dict().get('read', False)]
            elif status_filter == 'read':
                docs = [d for d in docs if d.to_dict().get('read', False)]

            if search:
                search_lower = search.lower()
                filtered = []
                for d in docs:
                    data = d.to_dict()
                    if (search_lower in data.get('name', '').lower() or
                        search_lower in data.get('email', '').lower() or
                        search_lower in data.get('subject', '').lower()):
                        filtered.append(d)
                docs = filtered

            total = len(docs)
            start = (page - 1) * per_page
            page_docs = docs[start:start + per_page]

            submissions = []
            for doc in page_docs:
                data = doc.to_dict()
                data['id'] = doc.id
                if data.get('created_at'):
                    data['created_at'] = data['created_at'].isoformat() if hasattr(data['created_at'], 'isoformat') else str(data['created_at'])
                submissions.append(data)

            return {
                'submissions': submissions,
                'total': total,
                'page': page,
                'per_page': per_page,
                'total_pages': (total + per_page - 1) // per_page
            }
        except Exception as e:
            print(f"Error fetching contact submissions: {e}")
            return {'submissions': [], 'total': 0, 'page': 1, 'per_page': per_page, 'total_pages': 0}

    def get_contact_stats(self, user_id):
        """Get contact submission statistics."""
        try:
            docs = list(
                self.db.collection('contact_submissions')
                .where(filter=FieldFilter('site_owner_id', '==', user_id))
                .stream()
            )
            total = len(docs)
            unread = sum(1 for d in docs if not d.to_dict().get('read', False))
            return {'total': total, 'unread': unread, 'read': total - unread}
        except Exception as e:
            print(f"Error fetching contact stats: {e}")
            return {'total': 0, 'unread': 0, 'read': 0}

    def mark_contact_read(self, submission_id):
        """Mark a contact submission as read."""
        try:
            self.db.collection('contact_submissions').document(submission_id).update({'read': True})
            return True
        except Exception as e:
            print(f"Error marking contact read: {e}")
            return False

    def delete_contact_submission(self, submission_id):
        """Delete a contact submission."""
        try:
            self.db.collection('contact_submissions').document(submission_id).delete()
            return True
        except Exception as e:
            print(f"Error deleting contact submission: {e}")
            return False

    # ---------------- COMMENT METHODS ----------------

    def create_comment(self, comment_data):
        """Save a new comment to Firestore. Returns document ID."""
        try:
            comment = {
                'site_owner_id': comment_data['site_owner_id'],
                'blog_id': comment_data['blog_id'],
                'blog_title': comment_data.get('blog_title', '')[:200],
                'commenter_name': comment_data['commenter_name'][:100],
                'commenter_email': comment_data['commenter_email'][:100],
                'original_text': comment_data['original_text'][:5000],
                'moderated_text': comment_data.get('moderated_text', '')[:5000],
                'display_text': comment_data.get('display_text', '')[:5000],
                'ai_action': comment_data.get('ai_action', 'approved'),
                'ai_reason': comment_data.get('ai_reason'),
                'ai_moderated_at': comment_data.get('ai_moderated_at'),
                'admin_edits': [],
                'status': comment_data.get('status', 'published'),
                'removed_by': comment_data.get('removed_by'),
                'removed_at': comment_data.get('removed_at'),
                'removed_reason': comment_data.get('removed_reason'),
                'created_at': firestore.SERVER_TIMESTAMP,
                'updated_at': datetime.utcnow()
            }
            doc_ref = self.db.collection('comments').add(comment)
            return doc_ref[1].id
        except Exception as e:
            print(f"❌ Error creating comment: {e}")
            return None

    def get_comments_for_blog(self, blog_id):
        """Get published comments for a blog post (public-facing)."""
        try:
            docs = list(
                self.db.collection('comments')
                .where(filter=FieldFilter('blog_id', '==', blog_id))
                .where(filter=FieldFilter('status', '==', 'published'))
                .stream()
            )
            # Sort client-side to avoid composite index requirement
            def _sort_key(doc):
                val = doc.to_dict().get('created_at')
                if val is None:
                    return 0
                if hasattr(val, 'timestamp'):
                    return val.timestamp()
                return 0
            docs.sort(key=_sort_key, reverse=True)
            comments = []
            for doc in docs:
                data = doc.to_dict()
                data['id'] = doc.id
                comments.append(data)
            return comments
        except Exception as e:
            print(f"❌ Error fetching blog comments: {e}")
            return []

    def get_comments_for_dashboard(self, site_owner_id, status_filter=None, ai_filter=None, page=1, per_page=20):
        """Get all comments for the dashboard moderation view."""
        try:
            query = self.db.collection('comments').where(
                filter=FieldFilter('site_owner_id', '==', site_owner_id)
            )

            if status_filter and status_filter != 'all':
                if status_filter == 'edited':
                    query = query.where(filter=FieldFilter('ai_action', '==', 'edited'))
                else:
                    query = query.where(filter=FieldFilter('status', '==', status_filter))

            docs = list(query.stream())
            # Sort client-side to avoid requiring composite indexes
            def _sort_key(doc):
                val = doc.to_dict().get('created_at')
                if val is None:
                    return 0
                if hasattr(val, 'timestamp'):
                    return val.timestamp()
                if hasattr(val, 'isoformat'):
                    return val.timestamp() if hasattr(val, 'timestamp') else 0
                return 0
            docs.sort(key=_sort_key, reverse=True)
            total = len(docs)

            # Paginate
            start = (page - 1) * per_page
            end = start + per_page
            page_docs = docs[start:end]

            comments = []
            for doc in page_docs:
                data = doc.to_dict()
                data['id'] = doc.id
                comments.append(data)

            return {
                'comments': comments,
                'total': total,
                'page': page,
                'per_page': per_page,
                'total_pages': (total + per_page - 1) // per_page
            }
        except Exception as e:
            print(f"❌ Error fetching dashboard comments: {e}")
            return {'comments': [], 'total': 0, 'page': 1, 'per_page': per_page, 'total_pages': 0}

    def get_comment_by_id(self, comment_id):
        """Fetch a single comment document by ID."""
        try:
            doc = self.db.collection('comments').document(comment_id).get()
            if doc.exists:
                data = doc.to_dict()
                data['id'] = doc.id
                return data
            return None
        except Exception as e:
            print(f"❌ Error fetching comment: {e}")
            return None

    def update_comment_display_text(self, comment_id, new_text, admin_id, admin_name, reason=""):
        """Admin edit: updates display_text and appends to admin_edits history."""
        try:
            doc_ref = self.db.collection('comments').document(comment_id)
            doc = doc_ref.get()
            if not doc.exists:
                return False

            current_data = doc.to_dict()
            edit_entry = {
                'admin_id': admin_id,
                'admin_name': admin_name,
                'previous_text': current_data.get('display_text', ''),
                'new_text': new_text[:5000],
                'edited_at': datetime.utcnow(),
                'reason': reason[:500] if reason else ''
            }

            doc_ref.update({
                'display_text': new_text[:5000],
                'admin_edits': firestore.ArrayUnion([edit_entry]),
                'updated_at': datetime.utcnow()
            })
            return True
        except Exception as e:
            print(f"❌ Error updating comment: {e}")
            return False

    def update_comment_status(self, comment_id, new_status, removed_by=None, reason=None):
        """Change comment status (published/removed/pending_delete)."""
        try:
            update_data = {
                'status': new_status,
                'updated_at': datetime.utcnow()
            }

            if new_status == 'removed':
                update_data['removed_by'] = removed_by or 'admin'
                update_data['removed_at'] = datetime.utcnow()
                update_data['removed_reason'] = reason
            elif new_status == 'published':
                update_data['removed_by'] = None
                update_data['removed_at'] = None
                update_data['removed_reason'] = None

            self.db.collection('comments').document(comment_id).update(update_data)
            return True
        except Exception as e:
            print(f"❌ Error updating comment status: {e}")
            return False

    def delete_comment_permanently(self, comment_id):
        """Hard delete a comment document from Firestore."""
        try:
            self.db.collection('comments').document(comment_id).delete()
            return True
        except Exception as e:
            print(f"❌ Error deleting comment: {e}")
            return False

    def get_comment_stats(self, site_owner_id):
        """Get comment counts for dashboard stats cards."""
        try:
            docs = list(
                self.db.collection('comments')
                .where(filter=FieldFilter('site_owner_id', '==', site_owner_id))
                .stream()
            )

            total = len(docs)
            published = 0
            ai_edited = 0
            removed = 0

            for doc in docs:
                data = doc.to_dict()
                if data.get('status') == 'published':
                    published += 1
                if data.get('ai_action') == 'edited':
                    ai_edited += 1
                if data.get('status') == 'removed':
                    removed += 1

            return {
                'total': total,
                'published': published,
                'ai_edited': ai_edited,
                'removed': removed
            }
        except Exception as e:
            print(f"❌ Error fetching comment stats: {e}")
            return {'total': 0, 'published': 0, 'ai_edited': 0, 'removed': 0}

    def save_newsletter_subscriber(self, user_id, email):
        """
        Saves a newsletter subscriber to Firestore.
        Uses email as part of doc ID to prevent duplicates.
        Returns tuple: (doc_id, is_new_subscriber)
        """
        try:
            email_clean = email.strip().lower()
            # Create unique doc ID to prevent duplicates
            doc_id = f"{user_id}_{email_clean.replace('@', '_at_').replace('.', '_')}"

            # Check if subscriber already exists
            doc_ref = self.db.collection('newsletter_subscribers').document(doc_id)
            existing_doc = doc_ref.get()

            if existing_doc.exists:
                existing_data = existing_doc.to_dict()
                # If already active subscriber, return as existing
                if existing_data.get('active', False):
                    return (doc_id, False)  # Already subscribed
                # If was unsubscribed, resubscribe them
                doc_ref.update({
                    'active': True,
                    'resubscribed_at': firestore.SERVER_TIMESTAMP
                })
                return (doc_id, True)  # Resubscribed

            # New subscriber
            subscriber = {
                'site_owner_id': user_id,
                'email': email_clean,
                'subscribed_at': firestore.SERVER_TIMESTAMP,
                'active': True
            }
            doc_ref.set(subscriber)
            return (doc_id, True)  # New subscriber
        except Exception as e:
            print(f"❌ Error saving newsletter subscriber: {e}")
            return (None, False)

    def get_newsletter_subscribers(self, user_id, limit=100):
        """
        Fetches newsletter subscribers for a site owner.
        """
        try:
            # Simple query without order_by to avoid composite index requirement
            docs = self.db.collection('newsletter_subscribers')\
                .where(filter=FieldFilter('site_owner_id', '==', user_id))\
                .where(filter=FieldFilter('active', '==', True))\
                .limit(limit)\
                .stream()

            subscribers = []
            for doc in docs:
                data = doc.to_dict()
                data['id'] = doc.id
                # Convert timestamp to ISO string for JSON serialization
                if data.get('subscribed_at'):
                    data['subscribed_at'] = data['subscribed_at'].isoformat()
                subscribers.append(data)

            # Sort by subscribed_at in Python (newest first)
            subscribers.sort(
                key=lambda x: x.get('subscribed_at') or '',
                reverse=True
            )
            return subscribers
        except Exception as e:
            print(f"❌ Error fetching newsletter subscribers: {e}")
            return []

    def get_subscriber_count(self, user_id):
        """Get total count of active subscribers."""
        try:
            count_query = self.db.collection('newsletter_subscribers')\
                .where(filter=FieldFilter('site_owner_id', '==', user_id))\
                .where(filter=FieldFilter('active', '==', True))\
                .count()
            result = count_query.get()
            return result[0][0].value
        except Exception as e:
            print(f"❌ Error counting subscribers: {e}")
            return 0

    def unsubscribe_newsletter(self, user_id, email):
        """Mark subscriber as inactive (unsubscribed)."""
        try:
            email_clean = email.strip().lower()
            doc_id = f"{user_id}_{email_clean.replace('@', '_at_').replace('.', '_')}"
            doc_ref = self.db.collection('newsletter_subscribers').document(doc_id)

            doc = doc_ref.get()
            if not doc.exists:
                return False

            doc_ref.update({
                'active': False,
                'unsubscribed_at': datetime.utcnow()
            })
            return True
        except Exception as e:
            print(f"❌ Error unsubscribing: {e}")
            return False

    def resubscribe_newsletter(self, user_id, email):
        """Reactivate a previously unsubscribed email."""
        try:
            email_clean = email.strip().lower()
            doc_id = f"{user_id}_{email_clean.replace('@', '_at_').replace('.', '_')}"
            doc_ref = self.db.collection('newsletter_subscribers').document(doc_id)

            doc = doc_ref.get()
            if not doc.exists:
                return False

            doc_ref.update({
                'active': True,
                'resubscribed_at': datetime.utcnow()
            })
            return True
        except Exception as e:
            print(f"❌ Error resubscribing: {e}")
            return False

    def log_newsletter_send(self, user_id, recipient_count, subject, content_preview="", html_content=""):
        """Log a newsletter send for history tracking."""
        try:
            self.db.collection('newsletter_history').add({
                'user_id': user_id,
                'recipient_count': recipient_count,
                'subject': subject,
                'content_preview': content_preview[:500],
                'html_content': html_content,
                'sent_at': firestore.SERVER_TIMESTAMP,
                'status': 'sent'
            })
            return True
        except Exception as e:
            print(f"❌ Error logging newsletter: {e}")
            return False

    def get_newsletter_history(self, user_id, limit=20):
        """Get newsletter send history."""
        try:
            # Simple query without order_by to avoid composite index requirement
            docs = self.db.collection('newsletter_history')\
                .where(filter=FieldFilter('user_id', '==', user_id))\
                .limit(limit)\
                .stream()

            history = []
            for doc in docs:
                data = doc.to_dict()
                data['id'] = doc.id
                # Convert timestamp to ISO string for JSON serialization
                if data.get('sent_at'):
                    data['sent_at'] = data['sent_at'].isoformat()
                history.append(data)

            # Sort by sent_at in Python (newest first)
            history.sort(
                key=lambda x: x.get('sent_at') or '',
                reverse=True
            )
            return history
        except Exception as e:
            print(f"❌ Error fetching newsletter history: {e}")
            return []

    def get_newsletter_by_id(self, newsletter_id, user_id):
        """Get a single newsletter by ID."""
        try:
            doc = self.db.collection('newsletter_history').document(newsletter_id).get()
            if not doc.exists:
                return None
            data = doc.to_dict()
            # Verify ownership
            if data.get('user_id') != user_id:
                return None
            data['id'] = doc.id
            # Convert timestamp to ISO string for JSON serialization
            if data.get('sent_at'):
                data['sent_at'] = data['sent_at'].isoformat()
            return data
        except Exception as e:
            print(f"❌ Error fetching newsletter by ID: {e}")
            return None

    def delete_newsletter(self, newsletter_id, user_id):
        """Delete a newsletter from history."""
        try:
            doc_ref = self.db.collection('newsletter_history').document(newsletter_id)
            doc = doc_ref.get()
            if not doc.exists:
                return False
            # Verify ownership
            if doc.to_dict().get('user_id') != user_id:
                return False
            doc_ref.delete()
            return True
        except Exception as e:
            print(f"❌ Error deleting newsletter: {e}")
            return False

    def save_newsletter_draft(self, user_id, draft_data):
        """Save a newsletter draft for later editing."""
        try:
            draft = {
                'user_id': user_id,
                'subject': draft_data.get('subject', ''),
                'intro': draft_data.get('intro', ''),
                'posts': draft_data.get('posts', []),
                'cta_text': draft_data.get('cta_text', 'Read More'),
                'closing': draft_data.get('closing', ''),
                'html_content': draft_data.get('html_content', ''),
                'created_at': firestore.SERVER_TIMESTAMP,
                'updated_at': datetime.utcnow(),
                'status': 'draft'
            }
            doc_ref = self.db.collection('newsletter_drafts').add(draft)
            return doc_ref[1].id
        except Exception as e:
            print(f"❌ Error saving newsletter draft: {e}")
            return None

    def get_newsletter_drafts(self, user_id, limit=10):
        """Get newsletter drafts."""
        try:
            # Simple query without order_by to avoid composite index requirement
            docs = self.db.collection('newsletter_drafts')\
                .where(filter=FieldFilter('user_id', '==', user_id))\
                .where(filter=FieldFilter('status', '==', 'draft'))\
                .limit(limit)\
                .stream()

            drafts = []
            for doc in docs:
                data = doc.to_dict()
                data['id'] = doc.id
                drafts.append(data)

            # Sort by updated_at in Python (newest first)
            drafts.sort(
                key=lambda x: x.get('updated_at') or '',
                reverse=True
            )
            return drafts
        except Exception as e:
            print(f"❌ Error fetching newsletter drafts: {e}")
            return []

    def delete_newsletter_draft(self, draft_id, user_id):
        """Delete a newsletter draft."""
        try:
            doc_ref = self.db.collection('newsletter_drafts').document(draft_id)
            doc = doc_ref.get()
            if not doc.exists:
                return False
            if doc.to_dict().get('user_id') != user_id:
                return False
            doc_ref.delete()
            return True
        except Exception as e:
            print(f"❌ Error deleting newsletter draft: {e}")
            return False

    # ---------------- EMBEDDING METHODS ----------------

    def update_blog_embedding(self, blog_id, embedding):
        """
        Store embedding vector for a blog post.
        Called when blog is published or updated.
        """
        try:
            doc_ref = self.db.collection(self.collection_name).document(blog_id)
            doc_ref.update({
                'embedding': embedding,
                'embedding_updated_at': datetime.utcnow()
            })
            return True
        except Exception as e:
            print(f"❌ Error storing embedding: {e}")
            return False

    def get_blogs_with_embeddings(self, user_id, limit=100):
        """
        Fetch published blogs that have embeddings stored.
        Returns blogs with embedding vectors for semantic search.
        """
        try:
            blogs = []
            blog_ids = set()

            # Query by site_owner_id
            site_owner_query = self.db.collection(self.collection_name)\
                .where(filter=FieldFilter('site_owner_id', '==', user_id))\
                .where(filter=FieldFilter('status', '==', 'PUBLISHED'))

            for doc in site_owner_query.stream():
                data = doc.to_dict()
                # Only include blogs with embeddings
                if data.get('embedding'):
                    data['id'] = doc.id
                    blog_ids.add(doc.id)
                    blogs.append(data)

            # Fallback: also fetch by author_id for older blogs
            fallback_query = self.db.collection(self.collection_name)\
                .where(filter=FieldFilter('author_id', '==', user_id))\
                .where(filter=FieldFilter('status', '==', 'PUBLISHED'))

            for doc in fallback_query.stream():
                if doc.id not in blog_ids:
                    data = doc.to_dict()
                    if data.get('embedding'):
                        data['id'] = doc.id
                        blogs.append(data)

            return blogs[:limit]
        except Exception as e:
            print(f"❌ Error fetching blogs with embeddings: {e}")
            return []

    def get_blogs_without_embeddings(self, user_id=None, limit=100):
        """
        Fetch published blogs that don't have embeddings yet.
        Used for backfilling embeddings.
        """
        try:
            blogs = []

            if user_id:
                # Query for specific user
                query = self.db.collection(self.collection_name)\
                    .where(filter=FieldFilter('site_owner_id', '==', user_id))\
                    .where(filter=FieldFilter('status', '==', 'PUBLISHED'))
            else:
                # Query all published blogs
                query = self.db.collection(self.collection_name)\
                    .where(filter=FieldFilter('status', '==', 'PUBLISHED'))

            for doc in query.stream():
                data = doc.to_dict()
                # Only include blogs without embeddings
                if not data.get('embedding'):
                    data['id'] = doc.id
                    blogs.append(data)

            return blogs[:limit]
        except Exception as e:
            print(f"❌ Error fetching blogs without embeddings: {e}")
            return []

    # ---------------- GALLERY METHODS ----------------

    def save_gallery_image(self, user_id, filename, url, size, content_type):
        try:
            doc_data = {
                'user_id': user_id,
                'filename': filename,
                'url': url,
                'size': size,
                'content_type': content_type,
                'created_at': datetime.utcnow().isoformat()
            }
            doc_ref = self.db.collection('gallery_images').add(doc_data)
            return doc_ref[1].id
        except Exception as e:
            print(f"❌ Error saving gallery image: {e}")
            return None

    def get_gallery_images(self, user_id, page=1, per_page=20):
        try:
            query = self.db.collection('gallery_images').where(
                filter=FieldFilter('user_id', '==', user_id)
            )
            docs = list(query.stream())
            docs.sort(key=lambda d: d.to_dict().get('created_at', ''), reverse=True)

            total = len(docs)
            start = (page - 1) * per_page
            end = start + per_page
            page_docs = docs[start:end]

            images = []
            for doc in page_docs:
                data = doc.to_dict()
                data['id'] = doc.id
                images.append(data)

            return {
                'images': images,
                'total': total,
                'page': page,
                'total_pages': (total + per_page - 1) // per_page
            }
        except Exception as e:
            print(f"❌ Error fetching gallery images: {e}")
            return {'images': [], 'total': 0, 'page': 1, 'total_pages': 0}

    def delete_gallery_image(self, image_id):
        try:
            doc_ref = self.db.collection('gallery_images').document(image_id)
            doc = doc_ref.get()
            if doc.exists:
                data = doc.to_dict()
                doc_ref.delete()
                return data
            return None
        except Exception as e:
            print(f"❌ Error deleting gallery image: {e}")
            return None