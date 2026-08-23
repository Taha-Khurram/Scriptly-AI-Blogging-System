"""Cache, background task pool, upload validation and the AI client.

These are the pieces whose failure modes only appear under concurrency or load,
which is exactly why they need tests rather than manual checking: a cache that
does not invalidate, a queue that accepts work it cannot start, and an upload
validator that trusts a filename all look fine in a single-user click-through.
"""
import time

import pytest

from app.core.errors import CapacityError, NotFoundError, PayloadTooLargeError, ValidationError


# =========================================================================
# Cache
# =========================================================================

class TestMemoryBackend:
    def test_stores_and_returns(self):
        from app.utils.cache import MemoryBackend, _MISS

        backend = MemoryBackend()
        backend.set('k', {'a': 1})
        assert backend.get('k') == {'a': 1}
        assert backend.get('absent') is _MISS

    def test_honours_ttl(self):
        from app.utils.cache import MemoryBackend, _MISS

        backend = MemoryBackend()
        backend.set('k', 'v', ttl=1)
        assert backend.get('k') == 'v'
        time.sleep(1.05)
        assert backend.get('k') is _MISS

    def test_entry_count_is_bounded(self):
        """A long-lived worker must not grow without limit on a key pattern
        driven by user input."""
        from app.utils.cache import MemoryBackend

        backend = MemoryBackend(max_entries=50)
        for index in range(500):
            backend.set(f'k{index}', index, ttl=300)
        assert backend.stats()['entries'] <= 50

    def test_eviction_prefers_expired_entries(self):
        from app.utils.cache import MemoryBackend

        backend = MemoryBackend(max_entries=20)
        for index in range(10):
            backend.set(f'stale{index}', index, ttl=1)
        time.sleep(1.05)
        for index in range(15):
            backend.set(f'fresh{index}', index, ttl=300)
        # The fresh keys survived; the expired ones were reclaimed first.
        assert backend.get('fresh14') == 14


class TestCacheApi:
    @pytest.fixture(autouse=True)
    def fresh_cache(self):
        from app.utils.cache import cache

        cache.configure(redis_url=None, default_ttl=60)
        cache.clear()
        yield cache
        cache.clear()

    def test_miss_returns_none(self, fresh_cache):
        assert fresh_cache.get('nothing-here') is None

    def test_get_or_miss_distinguishes_a_stored_none(self, fresh_cache):
        """`get()` conflates a cached None with a miss, which is the contract
        every existing call site was written against. `get_or_miss` exists for
        the cases where the difference matters."""
        sentinel = object()
        fresh_cache.set('none-key', None)
        assert fresh_cache.get('none-key') is None
        assert fresh_cache.get_or_miss('none-key', sentinel) is None
        assert fresh_cache.get_or_miss('absent', sentinel) is sentinel

    def test_clear_prefix_is_the_invalidation_primitive(self, fresh_cache):
        """Publishing a post clears published_blogs:<owner> regardless of the
        :limit suffix each cached variant carries."""
        fresh_cache.set('published_blogs:owner1:10', ['a'])
        fresh_cache.set('published_blogs:owner1:100', ['a', 'b'])
        fresh_cache.set('published_blogs:owner2:10', ['c'])

        fresh_cache.clear_prefix('published_blogs:owner1')

        assert fresh_cache.get('published_blogs:owner1:10') is None
        assert fresh_cache.get('published_blogs:owner1:100') is None
        assert fresh_cache.get('published_blogs:owner2:10') == ['c']

    def test_reports_it_is_not_shared_without_redis(self, fresh_cache):
        """The health endpoint surfaces this: an in-process cache with several
        workers means invalidation does not propagate."""
        assert fresh_cache.is_shared is False
        assert fresh_cache.stats()['backend'] == 'memory'

    def test_tracks_hit_rate(self, fresh_cache):
        fresh_cache.set('x', 1)
        fresh_cache.get('x')
        fresh_cache.get('y')
        stats = fresh_cache.stats()
        assert stats['hits'] >= 1 and stats['misses'] >= 1


class TestCachedDecorator:
    def test_memoises(self):
        from app.utils.cache import cache, cached

        cache.configure(redis_url=None)
        calls = []

        @cached(ttl=60, key_prefix='t1:')
        def double(n):
            calls.append(n)
            return n * 2

        assert double(4) == 8
        assert double(4) == 8
        assert calls == [4]

    def test_does_not_cache_none(self):
        """None is almost always the lookup-failed path here; caching it would
        pin a transient Firestore error in place for the whole TTL."""
        from app.utils.cache import cache, cached

        cache.configure(redis_url=None)
        calls = []

        @cached(ttl=60, key_prefix='t2:')
        def lookup(key):
            calls.append(key)
            return None

        lookup('a')
        lookup('a')
        assert len(calls) == 2


# =========================================================================
# Background task pool
# =========================================================================

class TestTaskManager:
    @pytest.fixture
    def pool(self):
        from app.utils.task_manager import TaskManager

        manager = TaskManager(max_workers=2, max_queue_depth=3, retention_seconds=1)
        yield manager
        manager.shutdown(wait=False)

    def test_runs_a_task_to_completion(self, pool):
        def work(task_id, value):
            pool.update_task(task_id, 'working', 50)
            pool.complete_task(task_id, {'value': value})

        task_id = pool.create_task('u1')
        pool.submit(task_id, work, 7)

        for _ in range(50):
            if pool.get_task(task_id)['status'] == 'completed':
                break
            time.sleep(0.02)

        task = pool.get_task(task_id)
        assert task['status'] == 'completed'
        assert task['result'] == {'value': 7}
        assert task['progress'] == 100

    def test_a_crash_marks_the_task_failed_without_leaking_details(self, pool):
        """The executor's future was never read, so an exception escaping the
        job used to vanish -- the browser polled a task that never changed."""
        secret = 'gRPC transport error: /etc/secrets/key.json'

        def explode(task_id):
            raise RuntimeError(secret)

        task_id = pool.create_task('u1')
        pool.submit(task_id, explode)

        for _ in range(50):
            if pool.get_task(task_id)['status'] == 'failed':
                break
            time.sleep(0.02)

        task = pool.get_task(task_id)
        assert task['status'] == 'failed'
        assert secret not in (task['error'] or '')

    def test_queue_depth_is_enforced(self, pool):
        """Admission is refused instead of silently queueing a user behind
        multi-minute jobs with no feedback."""
        for _ in range(3):
            pool.create_task('u1')

        with pytest.raises(CapacityError) as excinfo:
            pool.create_task('u1')
        assert excinfo.value.status_code == 503

    def test_queue_position_is_reported(self, pool):
        first = pool.create_task('u1')
        second = pool.create_task('u1')
        assert pool.queue_position(first) == 1
        assert pool.queue_position(second) == 2

    def test_ownership_is_enforced_on_reads(self, pool):
        """Both 'no such task' and 'not yours' answer NotFound, so polling
        cannot be used to discover another user's task id."""
        task_id = pool.create_task('owner')

        assert pool.get_task_for_user(task_id, 'owner')['id'] == task_id
        with pytest.raises(NotFoundError):
            pool.get_task_for_user(task_id, 'someone-else')
        with pytest.raises(NotFoundError):
            pool.get_task_for_user('made-up-id', 'owner')

    @pytest.mark.parametrize('progress,expected', [(-10, 0), (0, 0), (50, 50), (150, 100)])
    def test_progress_is_clamped(self, pool, progress, expected):
        """An out-of-range value would render a progress bar past its end."""
        task_id = pool.create_task('u1')
        pool.update_task(task_id, 'stage', progress)
        assert pool.get_task(task_id)['progress'] == expected

    def test_completed_task_is_not_reopened_by_a_late_update(self, pool):
        """A worker writing progress after finishing must not resurrect it."""
        task_id = pool.create_task('u1')
        pool.complete_task(task_id, {'done': True})
        pool.update_task(task_id, 'late', 10)
        assert pool.get_task(task_id)['status'] == 'completed'

    def test_cleanup_only_removes_finished_tasks(self, pool):
        """Deleting a running task's record would leave the browser polling an
        id that no longer exists anywhere."""
        finished = pool.create_task('u1')
        pool.complete_task(finished, {})
        running = pool.create_task('u1')
        pool.update_task(running, 'working', 10)

        time.sleep(1.05)
        pool.cleanup_expired()

        assert pool.get_task(finished) is None
        assert pool.get_task(running) is not None

    def test_stats_admit_the_pool_is_per_process(self, pool):
        """Honest reporting: with several workers a status poll can land on one
        that has never heard of the task."""
        assert pool.stats()['shared_across_workers'] is False

    def test_returned_task_is_a_copy(self, pool):
        """The caller serialises it outside the lock; handing out the live dict
        lets a worker mutate it mid-serialisation."""
        task_id = pool.create_task('u1')
        task = pool.get_task(task_id)
        task['status'] = 'tampered'
        assert pool.get_task(task_id)['status'] == 'pending'


# =========================================================================
# Upload validation
# =========================================================================

PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 64
JPEG = b'\xff\xd8\xff\xe0' + b'\x00' * 64
GIF = b'GIF89a' + b'\x00' * 64
WEBP = b'RIFF' + b'\x00\x00\x00\x00' + b'WEBP' + b'\x00' * 64


class TestUploadValidation:
    @pytest.mark.parametrize('data,expected', [
        (PNG, 'png'), (JPEG, 'jpg'), (GIF, 'gif'), (WEBP, 'webp'),
    ])
    def test_detects_real_image_types(self, data, expected):
        from app.services.storage_service import detect_image_type

        assert detect_image_type(data) == expected

    @pytest.mark.parametrize('data,label', [
        (b'<html><script>alert(1)</script></html>', 'html'),
        (b'<?php system($_GET["c"]); ?>', 'php'),
        (b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>', 'svg'),
        (b'RIFF\x00\x00\x00\x00WAVEfmt ', 'wav pretending to be webp'),
        (b'MZ\x90\x00', 'windows executable'),
        (b'', 'empty'),
        (b'ab', 'too short'),
    ])
    def test_rejects_non_images(self, data, label):
        """Extension checks are not enough -- the extension is chosen by the
        uploader. A file served from our own origin that a browser sniffs as
        HTML is script execution on our domain."""
        from app.services.storage_service import detect_image_type

        assert detect_image_type(data) is None, label

    def test_content_wins_over_a_mislabelled_extension(self):
        from app.services.storage_service import validate_image

        extension, content_type = validate_image(PNG, 'photo.jpg', 5_000_000)
        assert (extension, content_type) == ('png', 'image/png')

    def test_html_named_png_is_refused(self):
        from app.services.storage_service import validate_image

        with pytest.raises(ValidationError):
            validate_image(b'<html>not an image</html>', 'x.png', 5_000_000)

    def test_oversize_is_refused_with_a_readable_size(self):
        from app.services.storage_service import validate_image

        with pytest.raises(PayloadTooLargeError) as excinfo:
            validate_image(PNG * 5000, 'big.png', 1000)
        # The naive bytes//1MB form reported a sub-MB limit as "0 MB".
        assert '0 MB' not in excinfo.value.message

    def test_disallowed_extension_is_refused(self):
        from app.services.storage_service import validate_image

        with pytest.raises(ValidationError):
            validate_image(PNG, 'x.svg', 5_000_000)

    def test_object_names_are_scoped_and_unguessable(self):
        """A predictable key would let anyone enumerate another user's uploads
        from the public bucket URL."""
        from app.services.storage_service import build_object_name

        first = build_object_name('user-1', 'png')
        second = build_object_name('user-1', 'png')
        assert first.startswith('gallery/user-1/')
        assert first != second


class TestLocalStorageBackend:
    def test_refuses_to_write_or_delete_outside_its_root(self, tmp_path):
        """The URL is read back from Firestore before being joined onto the
        uploads folder, so it is untrusted input."""
        from app.services.storage_service import LocalStorageBackend

        backend = LocalStorageBackend(root=str(tmp_path))
        assert backend.delete('/static/uploads/../../../../etc/passwd') is False
        with pytest.raises(ValidationError):
            backend.save('../../escape.png', PNG, 'image/png')

    def test_round_trips_a_file(self, tmp_path):
        from app.services.storage_service import LocalStorageBackend

        backend = LocalStorageBackend(root=str(tmp_path))
        url = backend.save('gallery/u1/a.png', PNG, 'image/png')
        assert url == '/static/uploads/gallery/u1/a.png'
        assert (tmp_path / 'gallery' / 'u1' / 'a.png').read_bytes() == PNG
        assert backend.delete(url) is True
        assert backend.delete(url) is False       # already gone, not an error


# =========================================================================
# AI client
# =========================================================================

class TestJsonExtraction:
    @pytest.mark.parametrize('raw,expected', [
        ('{"a": 1}', {'a': 1}),
        ('```json\n{"a": 2}\n```', {'a': 2}),
        ('Here you go:\n```json\n{"b": [1, 2]}\n```\nHope that helps.', {'b': [1, 2]}),
        ('Sure! {"c": 3} done', {'c': 3}),
        ('{"d": 1,}', {'d': 1}),
        ('[{"e": 1}, {"e": 2},]', [{'e': 1}, {'e': 2}]),
        ({'already': 'parsed'}, {'already': 'parsed'}),
        ('no json here', None),
        ('', None),
        (None, None),
    ])
    def test_repairs_the_shapes_models_actually_return(self, raw, expected):
        """A model asked for JSON variously returns it clean, fenced, after a
        sentence of preamble, or with a trailing comma."""
        from app.services.gemini_client import extract_json

        assert extract_json(raw) == expected


class TestErrorClassification:
    @pytest.mark.parametrize('message,expected_class,retryable', [
        ('429 Resource has been exhausted (quota)', 'GeminiQuotaError', True),
        ('Response blocked due to safety settings', 'GeminiSafetyError', False),
        ('503 Service Unavailable', 'GeminiError', True),
        ('504 deadline exceeded', 'GeminiError', True),
        ('400 API key not valid', 'GeminiError', False),
        ('403 permission denied', 'GeminiError', False),
    ])
    def test_distinguishes_failure_kinds(self, message, expected_class, retryable):
        """Quota exhaustion, a safety block and a bad key are three different
        problems with three different correct responses; `except Exception`
        cannot tell them apart."""
        from app.services.gemini_client import _classify

        is_retryable, error_class = _classify(Exception(message))
        assert error_class.__name__ == expected_class
        assert is_retryable is retryable

    def test_safety_block_is_a_client_error_not_an_upstream_one(self):
        """Retrying the same prompt will be blocked again, so 4xx is correct
        and the caller must change the input."""
        from app.services.gemini_client import GeminiSafetyError

        assert GeminiSafetyError().status_code == 400

    def test_quota_error_surfaces_as_429(self):
        from app.services.gemini_client import GeminiQuotaError

        assert GeminiQuotaError().status_code == 429

    def test_unconfigured_client_raises_a_configuration_error(self):
        """Not a crash, and not a silent empty response that could be published
        as blog content."""
        from app.core.errors import ConfigurationError
        from app.services.gemini_client import GeminiClient

        client = GeminiClient()
        with pytest.raises(ConfigurationError):
            client.generate_text('hello')
