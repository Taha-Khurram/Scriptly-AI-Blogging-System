"""Cache, background task pool, upload validation and the AI client.

These are the pieces whose failure modes only appear under concurrency or load,
which is exactly why they need tests rather than manual checking: a cache that
does not invalidate, a queue that accepts work it cannot start, and an upload
validator that trusts a filename all look fine in a single-user click-through.
"""
import time
from unittest.mock import MagicMock

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

    def test_without_redis_it_is_still_shared_between_workers(self, fresh_cache):
        """The default backend is SQLite, not an in-process dict.

        This used to assert the opposite -- that without Redis the cache was
        per-process and therefore not shared. That was the bug, not the
        contract: with several workers an in-process cache means invalidation
        does not propagate, so publishing a post cleared the cached list in one
        worker and left every other one serving the pre-publish version until
        its TTL ran out. The local SQLite store is shared by every worker on the
        host, so a clear in one is a clear in all.
        """
        assert fresh_cache.stats()['backend'] == 'sqlite'
        assert fresh_cache.is_shared is True

    def test_only_redis_claims_to_be_shared_across_instances(self, fresh_cache):
        """SQLite is shared per host, so it must not over-claim.

        A second instance keeps its own file. Every entry has a TTL so nothing
        is served wrong indefinitely, but invalidation is per instance -- and
        the health endpoint has to say so rather than report a guarantee that
        is not there.
        """
        assert fresh_cache.is_shared_across_instances is False

    def test_the_memory_backend_reports_that_it_is_not_shared(self):
        """The fallback used outside an application context."""
        from app.utils.cache import Cache, MemoryBackend

        standalone = Cache()
        standalone._backend = MemoryBackend()
        assert standalone.is_shared is False
        assert standalone.is_shared_across_instances is False

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
# Live task output (the create screen's reasoning panel and streamed draft)
# =========================================================================

class TestTaskStream:
    @pytest.fixture
    def pool(self):
        from app.utils.task_manager import TaskManager

        manager = TaskManager(max_workers=2, max_queue_depth=5, retention_seconds=1)
        yield manager
        manager.shutdown(wait=False)

    def test_a_poll_receives_only_what_it_has_not_seen(self, pool):
        """The point of the cursors: a poll every second through a 7 KB draft
        must not re-send the whole draft each time."""
        task_id = pool.create_task('u1')
        pool.add_thought(task_id, 'Angle: cost per lead')
        pool.append_content(task_id, 'First half. ')

        first = pool.stream_since(task_id)
        assert [t['text'] for t in first['thoughts']] == ['Angle: cost per lead']
        assert first['content'] == 'First half. '

        pool.add_thought(task_id, 'Reader: a founder')
        pool.append_content(task_id, 'Second half.')

        second = pool.stream_since(
            task_id,
            since_thought=first['thought_cursor'],
            since_char=first['char_cursor'],
        )
        assert [t['text'] for t in second['thoughts']] == ['Reader: a founder']
        assert second['content'] == 'Second half.'
        assert second['total_chars'] == len('First half. Second half.')

    def test_a_late_client_replays_from_the_start(self, pool):
        """Navigating away and back re-attaches to the run, so cursor 0 has to
        mean 'everything' rather than 'nothing new'."""
        task_id = pool.create_task('u1')
        pool.append_content(task_id, 'abc')
        pool.append_content(task_id, 'def')

        assert pool.stream_since(task_id)['content'] == 'abcdef'

    def test_a_slice_is_capped_and_resumable(self, pool):
        task_id = pool.create_task('u1')
        pool.append_content(task_id, 'x' * 100)

        first = pool.stream_since(task_id, max_chars=40)
        assert len(first['content']) == 40
        assert first['char_cursor'] == 40

        rest = pool.stream_since(task_id, since_char=first['char_cursor'], max_chars=1000)
        assert len(rest['content']) == 60
        assert rest['char_cursor'] == 100

    def test_a_cursor_past_the_end_does_not_raise(self, pool):
        """A stale cursor from a previous run is worth one repeated slice, not a
        500 in the middle of a generation that is otherwise fine."""
        task_id = pool.create_task('u1')
        pool.append_content(task_id, 'short')

        out = pool.stream_since(task_id, since_thought=99, since_char=9999)
        assert out['content'] == ''
        assert out['thoughts'] == []

    def test_the_buffer_is_bounded(self, pool):
        """A model that will not stop cannot grow the process without limit."""
        from app.utils.task_manager import _MAX_STREAM_CHARS

        task_id = pool.create_task('u1')
        for _ in range(12):
            pool.append_content(task_id, 'y' * 10_000)

        out = pool.stream_since(task_id, max_chars=_MAX_STREAM_CHARS * 2)
        assert out['total_chars'] == _MAX_STREAM_CHARS
        assert out['truncated'] is True

    def test_output_is_dropped_with_the_task(self, pool):
        """The live output is the larger of the two records; leaving it behind
        is how a bounded table leaks megabytes."""
        task_id = pool.create_task('u1')
        pool.append_content(task_id, 'z' * 500)
        pool.complete_task(task_id, {})

        time.sleep(1.05)
        pool.cleanup_expired()

        assert pool.stats()['stream_chars'] == 0
        assert pool.stream_since(task_id)['total_chars'] == 0

    def test_thoughts_carry_their_kind_and_elapsed_time(self, pool):
        """The panel separates what the model said about its own approach from
        what the pipeline observed afterwards."""
        task_id = pool.create_task('u1')
        pool.add_thought(task_id, 'Angle: cost per lead', kind='plan')
        pool.add_thought(task_id, '1043 words, 5 min read')

        kinds = [t['kind'] for t in pool.stream_since(task_id)['thoughts']]
        assert kinds == ['plan', 'note']
        assert all(t['at'] >= 0 for t in pool.stream_since(task_id)['thoughts'])

    def test_writes_to_an_unknown_task_are_ignored(self, pool):
        """A worker whose task was evicted mid-run must not resurrect it."""
        pool.add_thought('made-up-id', 'hello')
        assert pool.append_content('made-up-id', 'text') == 0
        assert pool.stream_since('made-up-id')['total_chars'] == 0


# =========================================================================
# Blog stream splitting (plan vs post)
# =========================================================================

class TestStreamSplitter:
    """The plan and the post arrive in one stream, split on a marker the model
    is asked to emit. Every test here is a shape a model has actually returned,
    and in all of them the post itself must survive intact -- a tidy reasoning
    panel is never worth a lost draft."""

    @staticmethod
    def _run(text, chunk=7):
        from app.agents.content_agent import StreamSplitter

        thoughts, content = [], []
        splitter = StreamSplitter(on_thought=thoughts.append,
                                  on_content=content.append)
        for i in range(0, len(text), chunk):
            splitter.feed(text[i:i + chunk])
        return thoughts, ''.join(content), splitter.close()

    def test_splits_the_plan_from_the_post(self):
        thoughts, streamed, markdown = self._run(
            '=== PLAN ===\n'
            '- Angle: cost per lead, not vanity metrics\n'
            '- Reader: a founder who already runs ads\n'
            '=== BLOG ===\n'
            'The opening line.\n\n## Setup\n\nBody.\n',
            chunk=5,
        )
        assert thoughts == ['Angle: cost per lead, not vanity metrics',
                            'Reader: a founder who already runs ads']
        assert markdown.startswith('The opening line.')
        assert '=== BLOG ===' not in markdown
        assert 'Angle:' not in markdown
        # What was streamed to the screen is what was saved.
        assert streamed.strip() == markdown

    def test_strips_the_code_fence_the_prompt_forbids(self):
        _, _, markdown = self._run(
            '=== PLAN ===\n- One note\n=== BLOG ===\n'
            '```markdown\n## Real heading\n\ntext\n```\n',
            chunk=3,
        )
        assert markdown.startswith('## Real heading')
        assert '```' not in markdown

    def test_tolerates_a_decorated_marker(self):
        thoughts, _, markdown = self._run(
            '**=== PLAN ===**\n1. First note\n2) Second note\n'
            '**==== BLOG ====**\nBody starts.\n',
            chunk=4,
        )
        assert thoughts == ['First note', 'Second note']
        assert markdown == 'Body starts.'

    def test_a_response_with_no_markers_is_still_a_whole_post(self):
        """The failure that matters: if the model ignores the format, the post
        must arrive complete and nothing may be shown as reasoning."""
        body = '## A heading\n\n' + ('Sentence about the topic. ' * 40)
        thoughts, _, markdown = self._run(body, chunk=11)

        assert thoughts == []
        assert markdown.startswith('## A heading')
        assert len(markdown) >= len(body) - 2

    def test_a_plan_with_no_header_is_flushed_at_the_blog_marker(self):
        thoughts, _, markdown = self._run(
            '- Angle: the boring one works\n- Reader: impatient\n'
            '=== BLOG ===\nBody.\n',
            chunk=6,
        )
        assert thoughts == ['Angle: the boring one works', 'Reader: impatient']
        assert markdown == 'Body.'

    def test_the_marker_and_the_first_words_can_share_a_chunk(self):
        _, _, markdown = self._run(
            '=== PLAN ===\n- n\n=== BLOG ===\nX', chunk=4096
        )
        assert markdown == 'X'


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


class TestStorageBackendVerification:
    """A Bucket handle is not proof the bucket exists.

    The SDK constructs a handle without contacting the network, so a name for a
    non-existent bucket builds fine and then 404s on the first upload. That is
    how a real project reported `backend: firebase, durable: true` while every
    upload would have failed.
    """

    def test_nonexistent_bucket_is_refused_at_construction(self, monkeypatch):
        from app.core.errors import ConfigurationError
        from app.services import storage_service

        missing = MagicMock(name='bucket')
        missing.exists.return_value = False
        missing.name = 'project.appspot.com'
        monkeypatch.setattr(
            'app.firebase.firebase_admin.FirebaseLoader.get_bucket',
            classmethod(lambda cls: missing),
        )

        with pytest.raises(ConfigurationError) as excinfo:
            storage_service.FirebaseStorageBackend()
        assert 'does not exist' in excinfo.value.message

    def test_configure_falls_back_to_local_rather_than_failing_to_boot(self, monkeypatch):
        """The gallery being unavailable must not take the whole app down."""
        from app.services import storage_service

        missing = MagicMock(name='bucket')
        missing.exists.return_value = False
        missing.name = 'project.appspot.com'
        monkeypatch.setattr(
            'app.firebase.firebase_admin.FirebaseLoader.get_bucket',
            classmethod(lambda cls: missing),
        )

        service = storage_service.StorageService()
        service.configure(backend='firebase', max_bytes=1024)

        assert service.backend_name == 'local'
        # And it must admit the degradation rather than claiming durability.
        assert service.is_durable is False

    def test_existing_bucket_is_accepted(self, monkeypatch):
        from app.services import storage_service

        present = MagicMock(name='bucket')
        present.exists.return_value = True
        present.name = 'project.appspot.com'
        monkeypatch.setattr(
            'app.firebase.firebase_admin.FirebaseLoader.get_bucket',
            classmethod(lambda cls: present),
        )

        service = storage_service.StorageService()
        service.configure(backend='firebase', max_bytes=1024)

        assert service.backend_name == 'firebase'
        assert service.is_durable is True

    def test_verification_can_be_skipped(self, monkeypatch):
        """For a caller that has already established the bucket is present and
        does not want to spend a second round trip on it."""
        from app.services import storage_service

        unchecked = MagicMock(name='bucket')
        unchecked.name = 'project.appspot.com'
        monkeypatch.setattr(
            'app.firebase.firebase_admin.FirebaseLoader.get_bucket',
            classmethod(lambda cls: unchecked),
        )

        storage_service.FirebaseStorageBackend(verify=False)
        unchecked.exists.assert_not_called()
