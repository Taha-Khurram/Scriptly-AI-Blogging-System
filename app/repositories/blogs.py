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
from app.repositories._helpers import (
    BLOG_ARTICLE_FIELDS,
    BLOG_CARD_FIELDS,
    BLOG_LIST_FIELDS,
    BLOG_QUEUE_FIELDS,
    _parse_filter_date,
    _sanitize_blog_content,
    apply_projection,
)
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
            from app.utils.slug_utils import generate_slug

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
            blog_data['slug'] = self._unique_slug_for(site_owner, base_slug)
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
            from app.utils.slug_utils import generate_slug

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
                    slug_to_set = self._unique_slug_for(
                        user_id, base_slug, exclude_blog_id=blog_id
                    )
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
    def get_blogs_by_status(self, status, user_id, fields=BLOG_LIST_FIELDS):
        """Filters blogs by status AND author.

        Projected by default, because every caller is a list or a count. Pass
        ``fields=None`` for the whole document if a caller ever needs the body.
        """
        try:
            query = apply_projection(
                self.db.collection(self.collection_name)
                .where(filter=FieldFilter('author_id', '==', user_id))
                .where(filter=FieldFilter('status', '==', status.upper())),
                fields,
            )

            blogs = []
            for doc in query.stream():
                data = doc.to_dict()
                data['id'] = doc.id
                blogs.append(data)
            return blogs
        except Exception:
            logger.exception(f"Error fetching blogs by status {status}")
            return []

    @retry_on_unavailable
    def count_blogs_by_status(self, status, user_id):
        """How many of this author's blogs are in ``status``.

        A ``count()`` aggregation, not ``len(get_blogs_by_status(...))``. Both
        are one round trip, but the aggregation is evaluated in Firestore and
        returns a single integer instead of the documents, and it is billed per
        1000 documents scanned rather than per document read. Use this wherever
        only the number is displayed.
        """
        try:
            query = (self.db.collection(self.collection_name)
                     .where(filter=FieldFilter('author_id', '==', user_id))
                     .where(filter=FieldFilter('status', '==', status.upper()))
                     .count())
            return query.get()[0][0].value
        except Exception:
            logger.exception(f"Error counting blogs by status {status}")
            return 0

    def get_approval_queue(self, admin_id):
        """
        Returns pending blogs for an admin's approval queue:
        - Blogs submitted by the admin themselves
        - Blogs submitted by users created by this admin

        Projected to :data:`BLOG_QUEUE_FIELDS`. The queue screen lists a title,
        author, category and the two timestamps; ``approval.js`` loads the body
        through ``/api/get_blog/<id>`` when a reviewer opens a submission, so
        the list has never needed it. The team id list comes from the memoised
        ``get_team_user_ids`` rather than a second users query of its own.
        """
        try:
            user_ids = self.get_team_user_ids(admin_id)

            # Firestore allows 30 values in an `in` filter; batching at 30
            # rather than 10 is a third of the round trips for a large team.
            pending_blogs = []
            for i in range(0, len(user_ids), 30):
                batch_ids = user_ids[i:i + 30]
                query = apply_projection(
                    self.db.collection(self.collection_name)
                    .where(filter=FieldFilter("author_id", "in", batch_ids))
                    .where(filter=FieldFilter("status", "==", "UNDER_REVIEW"))
                    .order_by("updated_at", direction=firestore.Query.DESCENDING),
                    BLOG_QUEUE_FIELDS,
                )
                for doc in query.stream():
                    data = doc.to_dict()
                    data["id"] = doc.id
                    pending_blogs.append(data)

            return pending_blogs

        except Exception:
            logger.exception("Approval Queue Error")
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
        """One page of the author's drafts, plus the exact total.

        Already paged server-side, but it was still fetching whole documents for
        the ten rows it returns -- and a draft is the largest kind of blog
        document, carrying the markdown source, the rendered body and the
        embedding vector. ``drafts.js`` loads the body from
        ``/api/get_blog/<id>`` when a row is opened, so the list never needed
        it. The page query and the count are independent, so they go out
        together instead of one after the other.
        """
        try:
            base = (self.db.collection(self.collection_name)
                    .where(filter=FieldFilter('author_id', '==', user_id))
                    .where(filter=FieldFilter('status', '==', 'DRAFT')))

            def fetch_page():
                query = apply_projection(
                    base.order_by('updated_at', direction=firestore.Query.DESCENDING),
                    BLOG_LIST_FIELDS,
                ).offset((page - 1) * per_page).limit(per_page)
                rows = []
                for doc in query.stream():
                    data = doc.to_dict()
                    data['id'] = doc.id
                    rows.append(data)
                return rows

            def fetch_count():
                return base.count().get()[0][0].value

            from app.utils.parallel import run_parallel_simple

            drafts, total_count = run_parallel_simple(
                [(fetch_page, ()), (fetch_count, ())], max_workers=2
            )
            return drafts or [], total_count or 0
        except Exception:
            logger.exception("Error fetching paginated drafts")
            return [], 0

    def get_all_blogs_filtered(self, user_ids, status_filter='all', category_filter='all',
                                search='', date_from='', date_to='', page=1, per_page=10,
                                author_names=None):
        """The all-blogs table: every team post, filtered and paged.

        ``author_names`` is an optional ``{uid: display name}`` map. Callers
        that have already loaded the team -- and every caller has, because the
        team is where ``user_ids`` came from -- should pass it. Without it this
        method re-reads one user document per team member to recover names those
        documents already contained, which is a round trip each for data the
        caller was holding.

        Projected to :data:`BLOG_LIST_FIELDS`. The table renders a title,
        author, category, status and date; the edit dialog fetches the body
        separately through ``/api/get_blog/<id>`` when a row is opened. Before
        the projection this streamed every full document -- including each
        post's rendered body and its semantic-search embedding vector -- to
        render ten rows of five fields, measured at 6.6 s for 58 documents.

        The filtering, sorting and paging still happen in Python rather than in
        Firestore. That is deliberate and not the bottleneck: free-text search
        across title/category/author, an optional category filter and a date
        range cannot be expressed as one Firestore query without a composite
        index per combination, and with the projection applied the whole
        working set is a few tens of kilobytes.
        """
        try:
            from app.utils.parallel import run_parallel_simple

            # Batch-fetch all user names in parallel (instead of N sequential calls)
            def fetch_user_name(uid):
                doc = self.db.collection(self.user_collection).document(uid).get()
                if doc.exists:
                    u = doc.to_dict()
                    return (uid, u.get('name') or u.get('email', '').split('@')[0] or 'Unknown')
                return (uid, 'Unknown')

            def fetch_blogs():
                found = []
                for i in range(0, len(user_ids), 30):
                    batch_ids = user_ids[i:i + 30]
                    query = apply_projection(
                        self.db.collection(self.collection_name)
                        .where(filter=FieldFilter("author_id", "in", batch_ids)),
                        BLOG_LIST_FIELDS,
                    )
                    for doc in query.stream():
                        data = doc.to_dict()
                        data['id'] = doc.id
                        found.append(data)
                return found

            # Only look up names the caller could not supply. When it supplied
            # all of them this is a single query; otherwise the remaining
            # lookups run alongside the scan rather than before it.
            supplied = dict(author_names or {})
            missing = [uid for uid in user_ids if uid not in supplied]

            tasks = [(fetch_blogs, ())]
            tasks += [(fetch_user_name, (uid,)) for uid in missing]
            results = run_parallel_simple(tasks, max_workers=min(len(tasks), 10))

            all_blogs = results[0] or []
            for pair in results[1:]:
                if pair and pair[0]:
                    supplied[pair[0]] = pair[1]
            for data in all_blogs:
                data['author_name'] = supplied.get(data.get('author_id'), 'Unknown')

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

    @retry_on_unavailable
    def count_team_blogs_by_status(self, user_ids, status):
        """Exact number of the team's blogs in ``status``.

        The dashboard shows these as headline figures, and it used to derive
        them from ``len()`` of the list it had just streamed -- which is why
        those lists could not be bounded without the numbers going wrong. A
        ``count()`` aggregation separates the two concerns: the number is exact
        and costs one round trip that transfers a single integer, so the list
        beside it is free to be capped at the handful of rows actually rendered.
        """
        try:
            total = 0
            for i in range(0, len(user_ids), 30):
                query = (self.db.collection(self.collection_name)
                         .where(filter=FieldFilter('author_id', 'in', user_ids[i:i + 30]))
                         .where(filter=FieldFilter('status', '==', status))
                         .count())
                total += query.get()[0][0].value
            return total
        except Exception:
            logger.exception("Error counting team blogs by status %s", status)
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
    def get_published_blogs(self, user_id, limit=20, fields=None):
        """
        Fetches published blogs for the public site.
        Returns blogs ordered by updated_at descending.
        Filters by site_owner_id to include blogs from all team members.
        Falls back to author_id for backwards compatibility with older blogs.
        Uses in-memory cache with 2-minute TTL to reduce Firestore queries.
        Runs both queries in parallel for faster response times.

        ``fields`` defaults to ``None``, meaning the whole document, because the
        public article pages render the body and must have it. Callers that only
        list posts -- the published-post picker in site settings, the newsletter
        composer, the sitemap -- should pass :data:`BLOG_LIST_FIELDS`, which
        leaves the body and the semantic-search embedding vector on the server.
        The projection is part of the cache key, so a list-shaped result can
        never be served to a caller that asked for full documents.
        """
        projection = tuple(fields) if fields else None
        cache_key = 'published_blogs:%s:%s:%s' % (
            user_id, limit, ','.join(projection) if projection else 'full',
        )
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            from app.utils.parallel import run_parallel_simple

            # One implementation, two owner fields. The two branches were
            # byte-identical apart from the field they filter on, which meant
            # any change to the content coercion or the slug backfill had to be
            # made twice and stayed correct only by luck.
            def _fetch_by(owner_field):
                results = []
                query = self.db.collection(self.collection_name)\
                    .where(filter=FieldFilter(owner_field, '==', user_id))\
                    .where(filter=FieldFilter('status', '==', 'PUBLISHED'))
                query = apply_projection(query, projection)
                # Deliberately *not* ordered or limited server-side, even
                # though both branches are merged and then truncated to
                # `limit`. Adding `.order_by('updated_at').limit(limit)` needs a
                # (site_owner_id, status, updated_at) composite index that does
                # not exist -- the author_id equivalent does, which is what
                # makes the mistake easy. Firestore answers the unindexed query
                # with FAILED_PRECONDITION, the caller swallows it, and the
                # site_owner_id branch silently returns nothing: measured here,
                # 21 published posts disappeared from the public site while the
                # page still rendered a 200. The projection below is where the
                # win is anyway (750-820 ms against 4.6-8.0 s), and it needs no
                # index. Ordering stays in Python, as it was.
                for doc in query.stream():
                    data = doc.to_dict()
                    data['id'] = doc.id
                    # Only coerce content when it was actually requested: a
                    # projection without it would otherwise manufacture an
                    # empty body and hide the fact that it was never fetched.
                    if projection is None or 'content' in projection:
                        raw_content = data.get('content', '')
                        if isinstance(raw_content, dict):
                            data['content'] = raw_content
                        else:
                            data['content'] = {'body': str(raw_content) if raw_content else ''}
                    data = self._ensure_blog_slug(data, doc.id)
                    results.append(data)
                return results

            parallel_results = run_parallel_simple([
                (_fetch_by, ('site_owner_id',)),
                (_fetch_by, ('author_id',)),
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

    def get_published_blog_by_id(self, blog_id, fields=None):
        """
        Fetches a single published blog by ID.
        Returns None if blog doesn't exist or is not published.
        Auto-generates slug if missing.
        """
        try:
            doc_ref = self.db.collection(self.collection_name).document(blog_id)
            # DocumentReference.get takes field_paths, which is the single-document
            # equivalent of Query.select. A caller that only needs a teaser card
            # should pass BLOG_CARD_FIELDS: this one document read measured
            # 3986 ms unprojected on the public homepage, because a blog document
            # carries the whole post plus its semantic-search embedding vector.
            # `status` must be in any projection -- the publish check below reads
            # it -- and BLOG_LIST_FIELDS includes it.
            doc = doc_ref.get(field_paths=list(fields)) if fields else doc_ref.get()
            if doc.exists:
                data = doc.to_dict()
                # Only return if published
                if data.get('status') != 'PUBLISHED':
                    return None
                data['id'] = doc.id
                # Only synthesise a content map when content was requested; a
                # projection that omitted it must not be handed back an empty
                # body that looks like a post with nothing in it.
                if fields is None or 'content' in fields:
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

    def get_published_blog_by_slug(self, user_id, slug, fields=BLOG_ARTICLE_FIELDS):
        """
        Fetches a published blog by slug.
        Also checks old_slugs for 301 redirect handling.
        Returns dict with 'blog', 'redirect' (bool), and 'new_slug' (if redirect).

        Projected to :data:`BLOG_ARTICLE_FIELDS` by default. This is the public
        article page -- the most-requested page on the site -- and it was reading
        whole blog documents, 59% of which is the semantic-search embedding
        vector plus the formatting and outline maps that no template touches.
        ``old_slugs`` stays in the projection because the redirect branch below
        filters on it. Pass ``fields=None`` for the whole document.
        """
        try:
            # Try current slug first
            query = apply_projection(
                self.db.collection(self.collection_name)
                .where(filter=FieldFilter('site_owner_id', '==', user_id))
                .where(filter=FieldFilter('slug', '==', slug))
                .where(filter=FieldFilter('status', '==', 'PUBLISHED')),
                fields,
            ).limit(1)
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
            query = apply_projection(
                self.db.collection(self.collection_name)
                .where(filter=FieldFilter('site_owner_id', '==', user_id))
                .where(filter=FieldFilter('old_slugs', 'array_contains', slug))
                .where(filter=FieldFilter('status', '==', 'PUBLISHED')),
                fields,
            ).limit(1)
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

    def _slug_taken(self, user_id, slug, exclude_blog_id=None):
        """Whether ``slug`` is already used by this site owner.

        One indexed equality query. ``exclude_blog_id`` lets a post keep its own
        slug while its title is edited -- without it, saving a post without
        renaming it would see its own slug as taken and append "-2" on every
        save.
        """
        try:
            query = (
                self.db.collection(self.collection_name)
                .where(filter=FieldFilter('site_owner_id', '==', user_id))
                .where(filter=FieldFilter('slug', '==', slug))
                .limit(2 if exclude_blog_id else 1)
                .select([])
            )
            for doc in query.stream():
                if exclude_blog_id and doc.id == exclude_blog_id:
                    continue
                return True
            return False
        except Exception:
            # Failing closed here would block publishing entirely on a
            # transient error. Treating the slug as free risks a duplicate,
            # which is a cosmetic URL collision rather than lost work -- and
            # _ensure_blog_slug already tolerates one.
            logger.exception('Slug availability check failed')
            return False

    def _unique_slug_for(self, user_id, base_slug, exclude_blog_id=None):
        """A slug not already used by this owner, suffixing only if needed.

        Replaces the previous approach of streaming every one of the owner's
        blog documents to build a set of slugs -- 500 reads for a 500-post site,
        on every draft creation and every title change. Candidates are probed in
        order, so the common case (a free slug) costs exactly one read.

        The probe count is bounded: after 25 collisions it falls back to a
        random suffix rather than continuing to query. Twenty-five posts sharing
        one title means the numbering is not meaningful anyway, and an unbounded
        loop here would be a read-amplification hazard driven by user input.
        """
        if not self._slug_taken(user_id, base_slug, exclude_blog_id):
            return base_slug

        for counter in range(2, 26):
            candidate = f'{base_slug}-{counter}'
            if not self._slug_taken(user_id, candidate, exclude_blog_id):
                return candidate

        import uuid
        fallback = f'{base_slug}-{uuid.uuid4().hex[:6]}'
        logger.info(
            'Slug base exhausted after 24 probes; using a random suffix',
            extra={'base_slug': base_slug},
        )
        return fallback

    def _get_user_slugs(self, user_id):
        """Every slug this owner uses.

        Retained only for callers that genuinely need the whole set (there are
        none in the application today; a reporting script might). Prefer
        :meth:`_unique_slug_for`, which answers the uniqueness question in one
        read instead of one per post.
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
            from app.utils.slug_utils import generate_slug

            title = blog_data.get('title', 'Untitled')
            base_slug = generate_slug(title)

            # Get existing slugs for this user
            user_id = blog_data.get('site_owner_id') or blog_data.get('author_id')
            if user_id:
                slug = self._unique_slug_for(
                    user_id, base_slug, exclude_blog_id=blog_id
                )
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
