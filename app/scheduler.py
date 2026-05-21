import os
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

scheduler = BackgroundScheduler(daemon=True)


def publish_due_blogs():
    """Check for blogs past their scheduled_at and publish them."""
    try:
        from app.firebase.firestore_service import FirestoreService
        from app.utils.cache import cache

        db = FirestoreService()
        due_blogs = db.get_due_scheduled_blogs()

        if not due_blogs:
            return

        for blog in due_blogs:
            blog_id = blog.get('id')
            title = blog.get('title', 'Untitled')

            try:
                success = db.update_blog_status(blog_id, "PUBLISHED")

                if success:
                    site_owner_id = blog.get('site_owner_id') or blog.get('author_id')
                    if site_owner_id:
                        cache.clear_prefix(f"published_blogs:{site_owner_id}")

                    scheduled_by = blog.get('scheduled_by', 'system')
                    db.log_activity(
                        user_id=scheduled_by,
                        user_name="Scheduler",
                        type="status_change",
                        action_text="auto-published (scheduled)",
                        blog_title=title
                    )
                    print(f"[Scheduler] Published: {title}")

            except Exception as e:
                print(f"[Scheduler] Error publishing {blog_id}: {e}")

    except Exception as e:
        print(f"[Scheduler] Error: {e}")


def cleanup_expired_tasks():
    """Remove completed/failed tasks older than 10 minutes."""
    try:
        from app.utils.task_manager import task_manager
        task_manager.cleanup_expired(max_age=600)
    except Exception as e:
        print(f"[Scheduler] Task cleanup error: {e}")


def init_scheduler(app):
    """Initialize the background scheduler."""
    # Prevent double-start in Flask debug reloader
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or not app.debug:
        scheduler.add_job(
            func=publish_due_blogs,
            trigger=IntervalTrigger(seconds=60),
            id='publish_scheduled_blogs',
            name='Publish scheduled blogs',
            replace_existing=True
        )
        scheduler.add_job(
            func=cleanup_expired_tasks,
            trigger=IntervalTrigger(seconds=300),
            id='cleanup_expired_tasks',
            name='Cleanup expired generation tasks',
            replace_existing=True
        )
        scheduler.start()
        print("[Scheduler] Blog scheduler started (checking every 60s)")
