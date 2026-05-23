from flask import Blueprint, render_template, request, session, jsonify, redirect, url_for
from app.firebase.firestore_service import FirestoreService
from datetime import datetime

schedule_bp = Blueprint('schedule', __name__)
db_service = FirestoreService()


@schedule_bp.route('/schedule')
def schedule_page():
    if not session.get('logged_in'):
        return redirect(url_for('auth_bp.login'))

    user_role = session.get('user_role', 'USER')
    if user_role != 'ADMIN':
        return redirect(url_for('blog.home'))

    return render_template('schedule.html', user_role=user_role)


@schedule_bp.route('/api/schedule/list')
def schedule_list():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    user_role = session.get('user_role', 'USER')
    if user_role != 'ADMIN':
        return jsonify({"success": False, "error": "Admin only"}), 403

    try:
        site_owner_id = db_service.get_site_owner_for_user(user_id)
        print(f"[Schedule List] user_id={user_id}, site_owner_id={site_owner_id}")
        blogs = db_service.get_all_scheduled_for_calendar(site_owner_id)
        print(f"[Schedule List] Found {len(blogs)} blogs from Firestore")

        result = []
        for blog in blogs:
            scheduled_at = blog.get('scheduled_at')
            if not scheduled_at:
                print(f"[Schedule List] Skipping blog {blog.get('id')} - no scheduled_at")
                continue

            if hasattr(scheduled_at, 'isoformat'):
                display_date = scheduled_at.isoformat()
            else:
                display_date = str(scheduled_at)

            result.append({
                "id": blog.get("id"),
                "title": (blog.get("title") or "Untitled").replace("**", ""),
                "category": blog.get("category", "General"),
                "author": blog.get("author", "Unknown"),
                "status": blog.get("status"),
                "scheduled_at": display_date
            })

        print(f"[Schedule List] Returning {len(result)} blogs")
        return jsonify({"success": True, "blogs": result})
    except Exception as e:
        print(f"❌ Schedule list error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@schedule_bp.route('/api/schedule/best-time')
def best_publish_time():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    from app.utils.cache import cache

    # For non-admin users, use their site owner's analytics
    user_role = session.get('user_role', 'USER')
    if user_role != 'ADMIN':
        analytics_user_id = db_service.get_site_owner_for_user(user_id) or user_id
    else:
        analytics_user_id = user_id

    print(f"[BestTime] user_id={user_id}, role={user_role}, analytics_user_id={analytics_user_id}")

    cache_key = f"best_publish_time:{analytics_user_id}"
    cached = cache.get(cache_key)
    if cached:
        print(f"[BestTime] Returning cached result")
        return jsonify(cached)

    try:
        from app.routes.analytics_routes import _get_analytics_config, _get_credentials
        config = _get_analytics_config(analytics_user_id)
        print(f"[BestTime] Analytics config: {config}")

        if not config or not config.get('property_id'):
            print(f"[BestTime] NO ANALYTICS CONFIG FOUND for user {analytics_user_id}")
            result = {"success": True, "suggestions": [], "reason": "no_analytics",
                      "message": "Connect Google Analytics in Settings to get personalized suggestions."}
            return jsonify(result)

        property_id = config['property_id']
        print(f"[BestTime] Property ID: {property_id}")

        creds = _get_credentials(analytics_user_id)
        if not creds:
            print(f"[BestTime] CREDENTIALS FAILED - token refresh may have failed")
            result = {"success": True, "suggestions": [], "reason": "no_credentials",
                      "message": "Analytics credentials expired. Please reconnect in Settings."}
            return jsonify(result)

        print(f"[BestTime] Credentials OK, calling PublishTimeAgent...")

        from app.agents.publish_time_agent import PublishTimeAgent
        agent = PublishTimeAgent()
        result = agent.get_best_times(creds, property_id)

        print(f"[BestTime] Agent result: success={result.get('success')}, suggestions={len(result.get('suggestions', []))}, reason={result.get('reason', 'none')}")

        if result.get('suggestions'):
            for s in result['suggestions']:
                print(f"[BestTime]   -> {s['display_time']} (score={s['score']}, {s['reasoning']})")

        cache.set(cache_key, result, ttl=21600)
        return jsonify(result)
    except Exception as e:
        import traceback
        print(f"[BestTime] EXCEPTION: {e}")
        traceback.print_exc()
        return jsonify({"success": True, "suggestions": [], "reason": "error",
                        "message": f"Analytics error: {str(e)}"})


@schedule_bp.route('/api/schedule/available-blogs')
def available_blogs():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    user_role = session.get('user_role', 'USER')
    if user_role != 'ADMIN':
        return jsonify({"success": False, "error": "Admin only"}), 403

    try:
        users_ref = db_service.db.collection("users")
        user_docs = users_ref.where("created_by", "==", user_id).stream()
        user_ids = [u.id for u in user_docs]
        user_ids.append(user_id)

        user_name_map = {}
        for uid in user_ids:
            doc = db_service.db.collection("users").document(uid).get()
            if doc.exists:
                u = doc.to_dict()
                user_name_map[uid] = u.get('name') or u.get('email', '').split('@')[0] or 'Unknown'
            else:
                user_name_map[uid] = 'Unknown'

        blogs_ref = db_service.db.collection("blogs")
        results = []

        batch_size = 10
        for status in ["DRAFT", "UNDER_REVIEW"]:
            for i in range(0, len(user_ids), batch_size):
                batch_ids = user_ids[i:i + batch_size]
                docs = (
                    blogs_ref
                    .where("author_id", "in", batch_ids)
                    .where("status", "==", status)
                    .stream()
                )
                for doc in docs:
                    data = doc.to_dict()
                    results.append({
                        "id": doc.id,
                        "title": (data.get("title") or "Untitled").replace("**", ""),
                        "status": data.get("status"),
                        "author_name": user_name_map.get(data.get("author_id"), "Unknown"),
                        "updated_at": data.get("updated_at").isoformat() if hasattr(data.get("updated_at"), 'isoformat') else str(data.get("updated_at", ""))
                    })

        results.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        return jsonify({"success": True, "blogs": results})
    except Exception as e:
        print(f"❌ Available blogs error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@schedule_bp.route('/api/schedule/<blog_id>', methods=['POST'])
def schedule_blog(blog_id):
    user_id = session.get('user_id')
    user_role = session.get('user_role', 'USER')
    user_name = session.get('user_name', 'User')

    if not user_id:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    data = request.get_json()
    scheduled_at_str = data.get('scheduled_at')

    if not scheduled_at_str:
        return jsonify({"success": False, "error": "scheduled_at is required"}), 400

    try:
        scheduled_at = datetime.fromisoformat(scheduled_at_str.replace('Z', '+00:00'))
        scheduled_at = scheduled_at.replace(tzinfo=None)
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "Invalid date format"}), 400

    if scheduled_at <= datetime.utcnow():
        return jsonify({"success": False, "error": "Scheduled time must be in the future"}), 400

    blog_data = db_service.get_blog_by_id(blog_id)
    if not blog_data:
        return jsonify({"success": False, "error": "Blog not found"}), 404

    if user_role == 'ADMIN':
        # Admin can schedule directly - apply formatting first
        try:
            from app.agents.formatting_agent import FormattingAgent

            content = blog_data.get('content', '')
            if isinstance(content, dict):
                markdown_content = content.get('markdown') or content.get('body') or content.get('text', '')
            else:
                markdown_content = str(content)

            title = blog_data.get('title', '')
            formatter = FormattingAgent()
            formatted = formatter.format_blog(markdown_content, title)

            formatted_content = {
                'body': markdown_content,
                'markdown': markdown_content,
                'html': formatted['html'],
                'toc': formatted['toc'],
                'toc_html': formatted['toc_html'],
                'reading_time': formatted['reading_time_text'],
                'statistics': formatted['statistics']
            }

            db_service.update_blog_content(blog_id, title, formatted_content)
        except Exception as e:
            print(f"⚠ Formatting warning during schedule (continuing): {e}")

        # Generate embedding
        try:
            from app.agents.semantic_search_agent import SemanticSearchAgent
            search_agent = SemanticSearchAgent()
            search_agent.generate_and_store_embedding(blog_id)
        except Exception as e:
            print(f"⚠ Embedding warning during schedule (continuing): {e}")

        success = db_service.update_blog_status(blog_id, "SCHEDULED", scheduled_at=scheduled_at, scheduled_by=user_id)

        if success:
            db_service.log_activity(
                user_id=user_id,
                user_name=user_name,
                type="status_change",
                action_text=f"scheduled for {scheduled_at.strftime('%b %d, %Y at %I:%M %p')}",
                blog_title=blog_data.get('title', 'Untitled')
            )
            return jsonify({"success": True, "message": "Blog scheduled successfully"})
        return jsonify({"success": False, "error": "Failed to schedule blog"}), 500

    else:
        # Non-admin: set requested_schedule_at and submit for review
        doc_ref = db_service.db.collection("blogs").document(blog_id)
        doc_ref.update({
            "requested_schedule_at": scheduled_at,
            "status": "UNDER_REVIEW",
            "updated_at": datetime.utcnow()
        })

        db_service.log_activity(
            user_id=user_id,
            user_name=user_name,
            type="status_change",
            action_text=f"submitted for scheduled publishing on {scheduled_at.strftime('%b %d, %Y at %I:%M %p')}",
            blog_title=blog_data.get('title', 'Untitled')
        )

        return jsonify({"success": True, "message": "Blog submitted for approval with schedule request"})


@schedule_bp.route('/api/schedule/<blog_id>/reschedule', methods=['POST'])
def reschedule_blog(blog_id):
    user_id = session.get('user_id')
    user_role = session.get('user_role', 'USER')

    if not user_id:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    if user_role != 'ADMIN':
        return jsonify({"success": False, "error": "Only admin can reschedule"}), 403

    data = request.get_json()
    scheduled_at_str = data.get('scheduled_at')

    if not scheduled_at_str:
        return jsonify({"success": False, "error": "scheduled_at is required"}), 400

    try:
        scheduled_at = datetime.fromisoformat(scheduled_at_str.replace('Z', '+00:00'))
        scheduled_at = scheduled_at.replace(tzinfo=None)
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "Invalid date format"}), 400

    if scheduled_at <= datetime.utcnow():
        return jsonify({"success": False, "error": "Scheduled time must be in the future"}), 400

    blog_data = db_service.get_blog_by_id(blog_id)
    if not blog_data:
        return jsonify({"success": False, "error": "Blog not found"}), 404

    if blog_data.get('status') != 'SCHEDULED':
        return jsonify({"success": False, "error": "Blog is not currently scheduled"}), 400

    success = db_service.update_blog_status(blog_id, "SCHEDULED", scheduled_at=scheduled_at, scheduled_by=user_id)

    if success:
        db_service.log_activity(
            user_id=user_id,
            user_name=session.get('user_name', 'User'),
            type="status_change",
            action_text=f"rescheduled to {scheduled_at.strftime('%b %d, %Y at %I:%M %p')}",
            blog_title=blog_data.get('title', 'Untitled')
        )
        return jsonify({"success": True, "message": "Blog rescheduled successfully"})
    return jsonify({"success": False, "error": "Failed to reschedule"}), 500


@schedule_bp.route('/api/schedule/<blog_id>/cancel', methods=['POST'])
def cancel_schedule(blog_id):
    user_id = session.get('user_id')
    user_role = session.get('user_role', 'USER')

    if not user_id:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    if user_role != 'ADMIN':
        return jsonify({"success": False, "error": "Only admin can cancel schedule"}), 403

    blog_data = db_service.get_blog_by_id(blog_id)
    if not blog_data:
        return jsonify({"success": False, "error": "Blog not found"}), 404

    if blog_data.get('status') != 'SCHEDULED':
        return jsonify({"success": False, "error": "Blog is not currently scheduled"}), 400

    success = db_service.update_blog_status(blog_id, "DRAFT")

    if success:
        db_service.log_activity(
            user_id=user_id,
            user_name=session.get('user_name', 'User'),
            type="status_change",
            action_text="cancelled schedule and moved to draft",
            blog_title=blog_data.get('title', 'Untitled')
        )
        return jsonify({"success": True, "message": "Schedule cancelled"})
    return jsonify({"success": False, "error": "Failed to cancel schedule"}), 500


@schedule_bp.route('/api/schedule/<blog_id>/publish-now', methods=['POST'])
def publish_now(blog_id):
    user_id = session.get('user_id')
    user_role = session.get('user_role', 'USER')

    if not user_id:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    if user_role != 'ADMIN':
        return jsonify({"success": False, "error": "Only admin can publish"}), 403

    blog_data = db_service.get_blog_by_id(blog_id)
    if not blog_data:
        return jsonify({"success": False, "error": "Blog not found"}), 404

    if blog_data.get('status') != 'SCHEDULED':
        return jsonify({"success": False, "error": "Blog is not currently scheduled"}), 400

    success = db_service.update_blog_status(blog_id, "PUBLISHED")

    if success:
        from app.utils.cache import cache
        site_owner_id = blog_data.get('site_owner_id') or blog_data.get('author_id') or user_id
        cache.clear_prefix(f"published_blogs:{site_owner_id}")

        db_service.log_activity(
            user_id=user_id,
            user_name=session.get('user_name', 'User'),
            type="status_change",
            action_text="published immediately (was scheduled)",
            blog_title=blog_data.get('title', 'Untitled')
        )
        return jsonify({"success": True, "message": "Blog published"})
    return jsonify({"success": False, "error": "Failed to publish"}), 500
