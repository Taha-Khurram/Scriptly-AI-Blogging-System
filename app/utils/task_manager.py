import uuid
import time
from threading import Lock
from concurrent.futures import ThreadPoolExecutor


class TaskManager:
    def __init__(self, max_workers=2):
        self._tasks = {}
        self._lock = Lock()
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

    def create_task(self, user_id):
        task_id = str(uuid.uuid4())
        with self._lock:
            self._tasks[task_id] = {
                'user_id': user_id,
                'status': 'pending',
                'progress': 0,
                'stage': 'starting',
                'result': None,
                'error': None,
                'created_at': time.time(),
                'updated_at': time.time()
            }
        return task_id

    def submit(self, task_id, fn, *args, **kwargs):
        self._executor.submit(fn, task_id, *args, **kwargs)

    def update_task(self, task_id, stage, progress):
        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id].update({
                    'status': 'running',
                    'stage': stage,
                    'progress': progress,
                    'updated_at': time.time()
                })

    def complete_task(self, task_id, result):
        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id].update({
                    'status': 'completed',
                    'progress': 100,
                    'stage': 'completed',
                    'result': result,
                    'updated_at': time.time()
                })

    def fail_task(self, task_id, error):
        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id].update({
                    'status': 'failed',
                    'error': error,
                    'updated_at': time.time()
                })

    def get_task(self, task_id):
        with self._lock:
            return self._tasks.get(task_id)

    def cleanup_expired(self, max_age=600):
        now = time.time()
        with self._lock:
            expired = [
                tid for tid, t in self._tasks.items()
                if now - t['created_at'] > max_age
            ]
            for tid in expired:
                del self._tasks[tid]


task_manager = TaskManager()
