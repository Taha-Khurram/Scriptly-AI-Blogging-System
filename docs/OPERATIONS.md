# Operations Guide

How to run, deploy, monitor and debug Scriptly in production. Written for
whoever is on call, including the version of you that has forgotten all of this.

---

## Architecture at a glance

```
                         ┌──────────────────────────┐
   visitor / author ───▶ │  gunicorn (gthread)      │
                         │  gunicorn.conf.py        │
                         └────────────┬─────────────┘
                                      │
                    ┌─────────────────┼──────────────────┐
                    ▼                 ▼                  ▼
            ┌───────────────┐  ┌─────────────┐   ┌──────────────┐
            │  Flask app    │  │ task pool   │   │ APScheduler  │
            │  14 blueprints│  │ (in-process)│   │ (leased)     │
            └───────┬───────┘  └──────┬──────┘   └──────┬───────┘
                    │                 │                  │
        ┌───────────┼─────────┬───────┴────────┬─────────┘
        ▼           ▼         ▼                ▼
   ┌─────────┐ ┌────────┐ ┌───────┐    ┌──────────────┐
   │Firestore│ │ Redis  │ │Gemini │    │Firebase      │
   │(data)   │ │(cache, │ │(AI)   │    │Storage       │
   │         │ │ limits,│ │       │    │(uploads)     │
   │         │ │ lease) │ │       │    │              │
   └─────────┘ └────────┘ └───────┘    └──────────────┘
```

**Layers**, outermost first:

| Layer | Location | Responsibility |
|---|---|---|
| Entrypoint | `main.py`, `gunicorn.conf.py` | Process and server configuration |
| Factory | `app/__init__.py` | Composition order; nothing else |
| Core | `app/core/` | Logging, errors, security, extensions, sanitisation |
| Routes | `app/routes/` | HTTP shape and access control only |
| Agents | `app/agents/` | AI pipelines |
| Services | `app/services/` | External systems (Gemini, storage, email, Sheets) |
| Repositories | `app/repositories/` | Firestore access, one module per domain |
| Utils | `app/utils/` | Cache, dates, slugs, task pool, validators |

---

## Running locally

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows;  source .venv/bin/activate elsewhere
pip install -r requirements-dev.txt

cp .env.example .env              # then fill in the four required values
python main.py                    # http://localhost:5000
```

`.env` needs, at minimum: `SECRET_KEY`, `FIREBASE_SERVICE_ACCOUNT`,
`GEMINI_API_KEY`, and the `FB_*` web-SDK block. Everything else has a
documented default.

`FLASK_ENV=development` gives you the reloader, console logs, rate limiting off,
and a non-Secure session cookie (localhost is http, so a Secure-only cookie
would never come back and every login would appear to fail).

### Tests

```bash
pytest                            # 275 tests, ~20s
pytest -q --cov=app --cov-report=term-missing
pytest -m "not slow"
pyflakes app/ config.py main.py scripts/ tests/
```

Tests build the **real** application through `create_app(TestingConfig)`.
`TestingConfig` makes the factory inert -- no scheduler thread, no warm-up, no
Redis, no rate limiting -- which is what lets the suite exercise the shipping
request pipeline instead of a stand-in.

---

## Deploying

```bash
gunicorn --config gunicorn.conf.py main:app
```

`render.yaml` declares the whole thing: the web service, a managed Redis, the
health-check path, and every environment variable. Push, then point a Render
Blueprint at the repo.

### Before the first production deploy

1. **Generate a real `SECRET_KEY`.**
   `python -c "import secrets; print(secrets.token_urlsafe(64))"`
   Production refuses to boot on a placeholder or anything under 32 characters.
2. **Deploy the Firestore indexes.** `firebase deploy --only firestore:indexes`
   A missing composite index surfaces as `FAILED_PRECONDITION` at request time,
   not at boot.
3. **Set `FB_STORAGE_BUCKET`** and confirm `/healthz` reports
   `storage.durable: true`. If it reports `false`, uploads are going to a disk
   that is wiped on every deploy.
4. **Migrate existing uploads.**
   ```bash
   python scripts/migrate_uploads_to_storage.py --dry-run   # inspect first
   python scripts/migrate_uploads_to_storage.py
   ```
5. **Provision Redis.** Without it the cache, the rate-limit counters and the
   scheduler lease are all per-process. `/healthz` reports this as degraded.

---

## Health endpoints

| Endpoint | Question | Checks | Use for |
|---|---|---|---|
| `/livez` | Is the process alive? | none | Liveness probe / restart policy |
| `/readyz` | Should it get traffic? | Firestore | Load-balancer health check |
| `/healthz` | What is the state of everything? | all five | Dashboards, on-call |

`/livez` deliberately checks nothing: restarting does not fix Firestore being
down, so a liveness probe that checks dependencies produces a restart loop.

`/healthz` returns `200` for `healthy` and `degraded`, `503` only for
`unhealthy` (Firestore unreachable). A `degraded` result means the app serves
correctly but something needs attention:

```json
{
  "status": "degraded",
  "checks": {
    "firestore": {"status": "ok", "duration_ms": 24.1},
    "cache":     {"status": "ok", "backend": "memory",
                  "shared_across_workers": false},
    "ai":        {"status": "ok", "model": "gemini-flash-lite-latest"},
    "storage":   {"status": "degraded", "backend": "local", "durable": false},
    "tasks":     {"status": "ok", "running": 1, "queued": 0, "max_workers": 4}
  }
}
```

### Reading a degraded result

| Reported | Means | Do |
|---|---|---|
| `cache.backend: memory` | No Redis. Invalidation does not cross workers. | Set `REDIS_URL` |
| `cache.degraded: true` | Redis was reachable and now is not | Check the Redis instance; it retries every 30s |
| `storage.durable: false` | Uploads go to a disk wiped on deploy | Set `UPLOAD_BACKEND=firebase` + `FB_STORAGE_BUCKET` |
| `ai.status: fail` | `GEMINI_API_KEY` unset | Set it; AI features are down until then |
| `tasks.status: degraded` | Queue full; new jobs get 503 | Raise `TASK_MAX_WORKERS`, or wait |

---

## Logs

JSON, one object per line, on stdout.

```json
{"timestamp":"2026-08-24T09:14:22.481Z","level":"ERROR",
 "logger":"app.repositories.blogs","message":"Error updating blog content",
 "request_id":"7f3a9c2b8e1d4a6f","service":"scriptly","env":"production",
 "exception":{"type":"DeadlineExceeded","stack":"Traceback..."},
 "context":{"user_id":"abc123","blog_id":"xyz789"}}
```

**`request_id` is the handle on everything.** It is returned in the
`X-Request-ID` response header and included in every error body, so a user can
quote it:

```bash
# every log line for one failing request
grep '"request_id":"7f3a9c2b8e1d4a6f"' app.log | jq .

# slow requests
jq 'select(.context.duration_ms > 2000)' app.log

# errors grouped by logger
jq -r 'select(.level=="ERROR") | .logger' app.log | sort | uniq -c | sort -rn
```

Values that look like secrets are redacted before the record is written, so an
API key that ends up inside an exception message does not persist in the log
store.

### Levels

| Level | Used for |
|---|---|
| `DEBUG` | Cache hits, connection warm-up. Off in production. |
| `INFO` | Requests, sign-ins, publishes, queued jobs |
| `WARNING` | Rate limits hit, retries, slow requests, degraded fallbacks |
| `ERROR` | A failed operation with a real cause |
| `CRITICAL` | An unhandled exception -- always a bug |

---

## Runbook

### "Users report being logged out constantly"

`SESSION_TIMEOUT_MINUTES` is an *inactivity* window (default 480). If people are
losing sessions faster than that, the likely cause is `SECRET_KEY` differing
between instances -- each signs cookies the other rejects. Confirm every
instance has the same value.

### "AI features return 429"

Gemini quota. `grep '"code":"ai_quota_exceeded"' app.log | wc -l` to size it.
The client already retries with jittered backoff; if it persists, either the
quota needs raising or `RATELIMIT_AI_GENERATE` needs lowering to spread demand.

### "Blog generation never finishes"

1. `/healthz` → `tasks`. If `queued` is at `max_queue_depth`, the pool is
   saturated and new submissions are correctly getting 503.
2. If `running` is stuck above zero with nothing progressing, a job is wedged on
   an upstream call. It will hit `GEMINI_TIMEOUT_SECONDS` and fail.
3. **If `WEB_CONCURRENCY > 1`, suspect this first.** Task state lives in the
   worker that accepted the job, so a status poll landing on a different worker
   reports "not found" for a job that is running fine. Set it back to 1 or
   enable sticky sessions.

### "Images are broken"

Almost certainly uploads on ephemeral disk. Check
`/healthz` → `storage.durable`. If `false`, every upload since the last deploy
is gone. Fix the backend, then run the migration script -- it reports rows whose
files no longer exist rather than pretending to repair them.

### "The site is slow"

```bash
jq -r 'select(.context.duration_ms > 1000) | .context.path' app.log \
  | sort | uniq -c | sort -rn | head
```

Then check `/healthz` → `cache.hit_rate`. Below ~0.5 on a warm instance usually
means either no Redis (so every worker cold-starts its own cache) or a
too-aggressive invalidation.

### "A scheduled post published twice"

Should be impossible now -- the publisher takes a lease before running. Confirm
`Scheduler started ... single-runner scope: cluster (Redis lease)` in the boot
logs. If it says `single machine (file lock)` and you run more than one
instance, that is the gap: set `REDIS_URL`.

### "A worker was killed"

```
Worker 42 exceeded the 300s timeout and is being killed
```

An inline request blocked longer than the timeout -- nearly always an AI call
that should be running in the background pool. Find it by request id.

---

## Scaling

Current safe ceiling: **one worker, eight threads.** The limit is not CPU; it is
that the AI task pool lives inside the web process.

To scale out:

1. **Redis** (`REDIS_URL`) — required first. Shares the cache, the rate-limit
   counters and the scheduler lease.
2. **Move the task pool to a broker** (Celery or RQ). Until this is done,
   `WEB_CONCURRENCY > 1` breaks status polling. `gunicorn.conf.py` warns at boot
   if it is raised.
3. **Then raise `WEB_CONCURRENCY`.** Budget ~350 MB per worker for this
   dependency set.

Threads before processes: nearly every request waits on Firestore or Gemini, so
a thread costs a stack and buys a concurrent I/O wait, while a process costs a
whole interpreter.

### Firestore quota

The Spark tier allows 50k reads/day. The heaviest consumers, and what was done
about each:

| Path | Before | Now |
|---|---|---|
| Slug uniqueness on save | one read per existing post | one indexed query |
| Public blog lists | uncached per view | cached 120s, invalidated on publish |
| Site settings resolution | per request | cached 300s |
| Public page views | `no-store` on every response | `Cache-Control: public, max-age=60` |

---

## Security controls

| Control | Where | Notes |
|---|---|---|
| Session cookie | `config.py` | `Secure` + `HttpOnly` + `SameSite=Lax`, 8h sliding |
| CSRF | `app/core/extensions.py` | All state-changing requests; exemptions each carry a reason |
| Rate limiting | per route | Public AI endpoints tightest; per-user where authenticated |
| Access control | `app/core/security.py` | One definition; admin pages 404 rather than 403 |
| HTML sanitisation | `app/core/sanitize.py` | At **write** time, in the data layer |
| Upload validation | `app/services/storage_service.py` | Magic bytes, not the filename |
| Security headers | `app/core/security.py` | CSP, HSTS, frame, MIME, referrer |
| Body size limit | `config.py` | Enforced by Werkzeug before any view runs |
| Secret validation | `config.py` | Production will not boot on a weak key |

### Known accepted risk

The CSP includes `'unsafe-inline'` and `'unsafe-eval'` for scripts, because the
templates contain inline `<script>` blocks and the editor needs `eval`.
Removing it requires a nonce pass over roughly 40 templates. It is stated in
`app/core/security.py` rather than left implicit.

---

## Dependencies worth knowing about

- **`google-generativeai` is end-of-life** and emits a `FutureWarning` on
  import. Migrating to `google-genai` is an API change, not a version bump.
  Everything AI-related goes through `app/services/gemini_client.py`, so the
  migration is confined to that one module.
- **`grpcio-status` is pinned to the 1.71.x line.** 1.75+ requires
  protobuf ≥ 6.31.1, which conflicts with the protobuf 5.x the pinned `google-*`
  libraries need. The reason is recorded in `requirements.txt`; do not bump it
  without resolving that.
- **Everything is pinned.** Unpinned dependencies made pip's resolver backtrack
  through hundreds of transitive versions and fail with
  `resolution-too-deep`.
