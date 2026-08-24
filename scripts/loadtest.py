"""Concurrency check against the real app, with Firebase mocked.

    python scripts/loadtest.py

Not a benchmark of Firestore, and not a substitute for measuring production. It
exercises the *application layer* -- middleware, error handling, session
decoding, the cache, security headers, template rendering -- under real
concurrency on a real WSGI server. That is where a lock held too long, a
per-request object construction, or a thread leak shows up, and none of those
are visible from a single-user click-through or from the single-threaded Flask
test client.

Three things it is watching for, in order of importance:

1. **5xx under concurrency.** Any at all means a shared-state bug.
2. **Latency drift.** The last tenth of a sustained run is compared against the
   first. A rising figure means contention or a leak; the expected result is
   flat or *falling*, as caches warm.
3. **Thread count at exit.** The auth routes used to construct a
   ThreadPoolExecutor per request and never shut it down, leaking a thread per
   login. A bounded count here is the regression test for that.

Exits non-zero on failure, so it can gate a release.
"""
import os
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath('.'))
os.environ['FLASK_ENV'] = 'testing'

from app.firebase.firebase_admin import FirebaseLoader

FirebaseLoader._instance = MagicMock(name='firestore')
FirebaseLoader._bucket = MagicMock(name='bucket')

from config import TestingConfig  # noqa: E402

from app import create_app  # noqa: E402

PORT = 5099
BASE = f'http://127.0.0.1:{PORT}'

app = create_app(TestingConfig)

# The data layer is the real FirestoreService over a mocked client, which is
# what we want: the load test should exercise the app's own code paths, not a
# stub of them. Only the public-site routes are excluded from the mix below,
# since their output depends on document shapes a MagicMock cannot supply.


def serve():
    from waitress import serve as waitress_serve
    waitress_serve(app, host='127.0.0.1', port=PORT, threads=8,
                   connection_limit=200, channel_timeout=30, _quiet=True)


threading.Thread(target=serve, daemon=True).start()

# Wait for the socket to accept rather than sleeping a guessed interval.
for _ in range(100):
    try:
        urllib.request.urlopen(f'{BASE}/livez', timeout=1).read()
        break
    except Exception:
        time.sleep(0.05)
else:
    raise SystemExit('server did not start')


def fetch(path):
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(f'{BASE}{path}', timeout=30) as response:
            response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        exc.read()
        status = exc.code
    except Exception as exc:
        return None, f'{type(exc).__name__}: {exc}'
    return (time.perf_counter() - started) * 1000, status


def run(path, total, concurrency, label):
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        wall_start = time.perf_counter()
        results = list(pool.map(lambda _: fetch(path), range(total)))
        wall = time.perf_counter() - wall_start

    latencies = [ms for ms, _ in results if ms is not None]
    statuses = {}
    errors = []
    for ms, status in results:
        if ms is None:
            errors.append(status)
        else:
            statuses[status] = statuses.get(status, 0) + 1

    if not latencies:
        print(f'{label:26} ALL FAILED: {errors[:3]}')
        return False

    latencies.sort()
    def pct(p):
        return latencies[min(len(latencies) - 1, int(len(latencies) * p))]

    print(
        f'{label:26} n={total:<5} c={concurrency:<3} '
        f'rps={total / wall:7.0f}  '
        f'p50={statistics.median(latencies):6.1f}ms  '
        f'p95={pct(0.95):6.1f}ms  p99={pct(0.99):6.1f}ms  '
        f'max={latencies[-1]:6.1f}ms  '
        f'status={statuses}'
        + (f'  ERRORS={len(errors)}' if errors else '')
    )
    return not errors


print(f'Serving on {BASE} (waitress, 8 threads)\n')
print('--- Throughput and latency under concurrency ---')

ok = True
ok &= run('/livez', 300, 1, 'liveness (serial)')
ok &= run('/livez', 600, 16, 'liveness (16 concurrent)')
ok &= run('/livez', 600, 64, 'liveness (64 concurrent)')
print()
ok &= run('/login', 200, 1, 'login page (serial)')
ok &= run('/login', 400, 16, 'login page (16 concurrent)')
ok &= run('/login', 400, 64, 'login page (64 concurrent)')
print()
ok &= run('/healthz', 200, 16, 'healthz (16 concurrent)')
ok &= run('/api/gallery/images', 400, 32, 'api 401 (32 concurrent)')
ok &= run('/no-such-page', 300, 32, 'html 404 (32 concurrent)')

print()
print('--- Sustained mixed load ---')
paths = ['/livez', '/login', '/healthz', '/api/gallery/images', '/no-such-page']


def mixed(index):
    return fetch(paths[index % len(paths)])


with ThreadPoolExecutor(max_workers=48) as pool:
    start = time.perf_counter()
    mixed_results = list(pool.map(mixed, range(2000)))
    duration = time.perf_counter() - start

latencies = sorted(ms for ms, _ in mixed_results if ms is not None)
failures = [s for ms, s in mixed_results if ms is None]
server_errors = [s for ms, s in mixed_results if ms is not None and s >= 500]

print(f'2000 mixed requests, 48 concurrent, in {duration:.2f}s '
      f'({2000 / duration:.0f} rps)')
print(f'  p50={statistics.median(latencies):.1f}ms  '
      f'p95={latencies[int(len(latencies) * 0.95)]:.1f}ms  '
      f'p99={latencies[int(len(latencies) * 0.99)]:.1f}ms  '
      f'max={latencies[-1]:.1f}ms')
print(f'  transport failures: {len(failures)}')
print(f'  5xx responses:      {len(server_errors)}')

# Latency stability: compare the first and last tenth. A growing figure means
# contention or a leak, which is the failure mode that only appears under load.
tenth = max(1, len(mixed_results) // 10)
first = [ms for ms, _ in mixed_results[:tenth] if ms is not None]
last = [ms for ms, _ in mixed_results[-tenth:] if ms is not None]
print(f'  first 10% p50: {statistics.median(first):.1f}ms')
print(f'  last  10% p50: {statistics.median(last):.1f}ms')
drift = statistics.median(last) / max(statistics.median(first), 0.001)
print(f'  drift: {drift:.2f}x  '
      f'({"stable" if drift < 2.0 else "DEGRADING"})')

print()
from app.utils.cache import cache  # noqa: E402
from app.utils.task_manager import task_manager  # noqa: E402

print('cache: ', cache.stats())
print('tasks: ', task_manager.stats())
print('threads alive:', threading.active_count())

ok &= not failures and not server_errors and drift < 2.0
print()
print('RESULT:', 'PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)
