# Production Readiness Audit

**Project:** Scriptly (FYP)
**Date:** 2026-08-15
**Scope:** ~14,478 lines Python · 13 blueprints · 14 AI agents · 24 templates · 63 static assets
**Target deployment:** Render (free plan) + Firebase/Firestore + Google Gemini

---

## Verdict

| Dimension | Rating | Notes |
|---|---|---|
| Code structure & organisation | **Good** | Clean app-factory, blueprints, layered separation |
| Secret hygiene | **Good** | `.env` + `serviceAccountKey.json` gitignored, absent from history |
| Security posture | **Poor** | No rate limiting, no CSRF, insecure session cookie |
| Scalability | **Poor** | Single-instance by design; cannot scale horizontally |
| Operability | **Poor** | No structured logging, no error tracking, no metrics |
| Test coverage | **Poor** | ~3% (475 test lines / 14,478 app lines) |
| **Overall: production-grade?** | **No** | 7 × P0 blockers, three of which cause data loss or outage |

Good enough for an FYP demo. Not ready for real users without the P0 list below.

---

## Current Capacity

Ceiling set by `render.yaml:24` — `gunicorn --workers 1 --threads 8` on Render free (0.1 shared CPU, 512 MB RAM).

| Workload | Realistic concurrent users |
|---|---|
| Public blog reading (`/<site>/post/...`) | 10–20 simultaneous; ~40–60 casual browsers |
| Logged-in dashboard | 5–8 simultaneous |
| AI generation / humanize | **2 — globally, across all users** |
| Hard connection wall | ~8 in-flight; #9+ queues against a 300s timeout |

Three independent limits produce those numbers:

1. **8 threads on 1 worker.** Most work is I/O-bound (Firestore, Gemini) so threads help, but Jinja rendering, Markdown conversion, and the numpy cosine loop all contend for the GIL on 0.1 CPU.
2. **`TaskManager(max_workers=2)`** — two AI generations platform-wide. Everyone else waits in an unbounded, invisible queue.
3. **Firestore free tier: 50,000 reads/day.** Zero caching on public site routes. At ~10 reads per page view that is ~5,000 page views/day — a hard billing wall reached long before CPU saturation.

Additionally, the free plan **spins down after 15 minutes idle**, giving the next visitor a ~50 second cold start.

**After the full remediation roadmap:** ~200–500 concurrent users on a 2 GB paid instance, with AI throughput bounded only by Gemini quota.

---

## Priority Legend

| Level | Meaning | Timeline |
|---|---|---|
| **P0** | Causes data loss, outage, or exploitable security hole. Blocks launch. | Before any real user |
| **P1** | Degrades reliability, security, or the ability to operate the system. | Within first sprint |
| **P2** | Maintainability and correctness debt. Compounds over time. | Next 1–2 months |
| **P3** | Polish and hardening. | Backlog |

---

## P0 — Launch Blockers

### P0-1 · User uploads are deleted on every deploy
**Location:** [`app/routes/gallery_routes.py:67-76`](../app/routes/gallery_routes.py#L67-L76)
**Category:** Data loss

Images are written to `app/static/uploads/gallery/<user_id>/` on the container's local filesystem. Render's disk is ephemeral — every deploy, restart, or scale event wipes it. Firestore metadata survives, so users are left with permanently broken image links and no recovery path. The untracked file currently in `git status` is an instance of this.

**Fix:** Upload to Firebase Storage (already a project dependency) or S3; store the returned public URL instead of a local path. Migrate existing rows.
**Effort:** ~4 hours

---

### P0-2 · Unauthenticated AI endpoints with no rate limiting
**Location:** [`app/routes/site_routes.py:387`](../app/routes/site_routes.py#L387) (`/semantic-search`), [`:791`](../app/routes/site_routes.py#L791) (`/comment`), [`:348`](../app/routes/site_routes.py#L348) (`/contact`), [`:367`](../app/routes/site_routes.py#L367) (`/subscribe`)
**Category:** Cost / availability

No rate-limiting dependency exists in `requirements.txt`. `POST /<site>/semantic-search` is public and issues a Gemini embedding call plus an LLM call per request. `POST /<site>/post/<slug>/comment` invokes `CommentAgent.moderate_comment()` — another Gemini call — per anonymous submission. A trivial loop drains the API quota and runs up billing in minutes.

This is not theoretical: the project already had to downgrade to `gemini-flash-lite-latest` because of 429 rate-limit errors under **single-developer** load.

**Fix:** Add `flask-limiter` with a Redis (or in-memory, single-worker) backend. Suggested limits: `/semantic-search` 10/min per IP, `/comment` 5/min per IP, `/contact` and `/subscribe` 3/min per IP. Consider a CAPTCHA on comment and contact.
**Effort:** ~3 hours

---

### P0-3 · Unbounded upload body → out-of-memory DoS
**Location:** [`app/routes/gallery_routes.py:59-61`](../app/routes/gallery_routes.py#L59-L61)
**Category:** Availability

```python
file_data = file.read()                    # entire body into RAM first
if len(file_data) > MAX_FILE_SIZE:         # size check happens after
```

`MAX_CONTENT_LENGTH` is not configured anywhere. A single 500 MB POST exhausts the 512 MB instance before the guard is ever evaluated. One request, whole app down.

**Fix:** Set `MAX_CONTENT_LENGTH = 5 * 1024 * 1024` in `Config` so Werkzeug rejects oversized bodies at the WSGI layer. Also validate real content type via magic bytes, not just the file extension.
**Effort:** ~30 minutes

---

### P0-4 · Session cookie transmitted over plaintext HTTP
**Location:** [`config.py:12-16`](../config.py#L12-L16)
**Category:** Security

`SESSION_COOKIE_HTTPONLY` and `SESSION_COOKIE_SAMESITE` are set, but `SESSION_COOKIE_SECURE` is not — it defaults to `False`. The session cookie carries `logged_in`, `user_id`, and `user_role`, and will be sent over any non-TLS request.

**Fix:**
```python
SESSION_COOKIE_SECURE = os.getenv('FLASK_ENV') == 'production'
```
**Effort:** ~5 minutes

---

### P0-5 · Architecture cannot scale horizontally
**Location:** [`render.yaml:5-8`](../render.yaml#L5-L8), [`app/scheduler.py:78`](../app/scheduler.py#L78), [`app/utils/cache.py:54`](../app/utils/cache.py#L54), [`app/utils/task_manager.py:76`](../app/utils/task_manager.py#L76)
**Category:** Scalability

Three pieces of mutable state live inside the web process:

- **APScheduler** — the auto-publisher, started in-process
- **`SimpleCache`** — a module-level dict
- **`TaskManager`** — a module-level `ThreadPoolExecutor` plus task dict

Adding a second worker therefore produces duplicate scheduled publishes and a split cache. The `render.yaml` comment documents this constraint accurately. Consequences: throughput is capped at one process, and there is no zero-downtime deploy path.

**Fix:** Redis for cache and sessions; Celery or RQ for background tasks; move APScheduler into a dedicated worker service (or replace with Render Cron / Cloud Scheduler). Only then raise `--workers`.
**Effort:** ~1 week

---

### P0-6 · Background task state is RAM-only and silently lost
**Location:** [`app/utils/task_manager.py:9-29`](../app/utils/task_manager.py#L9-L29)
**Category:** Data loss

Task status lives in `self._tasks = {}`. A deploy, crash, or free-plan spin-down destroys every in-flight blog generation. There is no persistence, no retry, and no notification — the user's polling request simply starts returning "task not found" after a job that may have run for two minutes.

**Fix:** Persist task records to Firestore (or Redis) so status survives a restart and can be resumed or reported as failed.
**Effort:** ~1 day

---

### P0-7 · Free plan is not a production hosting tier
**Location:** [`render.yaml:18`](../render.yaml#L18)
**Category:** Availability

- Spins down after 15 minutes idle → ~50 second cold start
- 0.1 shared CPU, 512 MB RAM (baseline import footprint of Flask + firebase-admin + grpc + numpy + google-generativeai is roughly 250–350 MB, leaving little headroom)
- Firestore Spark tier: 50k reads/day, 20k writes/day

**Fix:** Move to Render Starter or above; move Firebase to Blaze with a budget alert configured.
**Effort:** Configuration + billing decision

---

## P1 — Fix in the First Sprint

### P1-1 · No CSRF protection
**Location:** Application-wide; no `flask-wtf` in [`requirements.txt`](../requirements.txt)

Authentication is cookie-based. `SameSite=Lax` blocks cross-site *form* POSTs in modern browsers, which covers the common case, but every state-changing endpoint — `/api/profile/update`, `/api/admin/create-user`, blog delete, settings — is otherwise untokenised. Defence in depth is missing.

**Fix:** `flask-wtf` `CSRFProtect`, with the token injected into the base template and sent by the JS fetch layer.
**Effort:** ~4 hours

---

### P1-2 · User-enumeration oracle
**Location:** [`app/routes/auth.py:151-163`](../app/routes/auth.py#L151-L163)

`POST /api/auth/check-email` is unauthenticated and unthrottled, returning `{"exists": true}` / `404` for any address. This confirms account existence for arbitrary emails at scale.

**Fix:** Rate-limit hard (3/min per IP) and return a uniform response; let the password-reset flow itself report failure generically.
**Effort:** ~1 hour

---

### P1-3 · Raw exception strings returned to clients
**Location:** [`app/routes/auth.py:104-105`](../app/routes/auth.py#L104-L105), and similar patterns elsewhere

```python
except Exception as e:
    return jsonify({"success": False, "error": str(e)}), 401
```

Leaks internal state — Firebase error codes, stack context, sometimes paths — to unauthenticated callers.

**Fix:** Log the exception server-side with a correlation ID; return a generic message plus that ID to the client.
**Effort:** ~3 hours

---

### P1-4 · Production is not debuggable
**Location:** Application-wide — 307 `print()` calls vs 15 `logger` calls

No log levels, no timestamps, no request IDs, no structured output, no error tracking. On Render this is an undifferentiated stdout firehose with no way to correlate a user report to an event.

**Fix:** Replace `print()` with the stdlib `logging` module configured for JSON output; add Sentry (free tier is sufficient); attach a request ID in `before_request` and include it in every log line and error response.
**Effort:** ~1 day

---

### P1-5 · Public routes have zero caching
**Location:** [`app/routes/site_routes.py`](../app/routes/site_routes.py) — no `cache.get` / `@cached` anywhere in the file

Every anonymous page view re-queries Firestore: site settings, blog, categories, recent posts. This is the single largest driver of the 50k reads/day ceiling and of p95 latency.

**Fix:** Cache resolved site settings (TTL 300s) and published-blog lists (TTL 60s, invalidated on publish — the scheduler already calls `cache.clear_prefix`). Add HTTP `Cache-Control` headers on public pages; they are currently forced to `no-store` by the global `after_request` hook in [`app/__init__.py:203-210`](../app/__init__.py#L203-L210).
**Effort:** ~1 day
**Impact:** Plausibly a 5–10× reduction in Firestore reads

---

### P1-6 · AI concurrency hardcoded to 2, queue unbounded
**Location:** [`app/utils/task_manager.py:8`](../app/utils/task_manager.py#L8), [`app/routes/blog_routes.py:393`](../app/routes/blog_routes.py#L393)

`max_workers=2` is a magic number with no configuration hook and no queue-depth limit. The third concurrent user gets no feedback that they are queued behind two multi-minute jobs.

**Fix:** Make the pool size an environment variable; cap queue depth and return `429` with an estimated wait when full; surface queue position in the polling response.
**Effort:** ~4 hours

---

### P1-7 · Health check does not verify dependencies
**Location:** [`render.yaml:25`](../render.yaml#L25) — `healthCheckPath: /`

`/` redirects to the login page. It returns a healthy status code even when Firestore or Gemini is unreachable, so Render will keep routing traffic to a broken instance.

**Fix:** Add `GET /healthz` that performs a cheap Firestore read and returns `200`/`503` with a component breakdown. Point `healthCheckPath` at it.
**Effort:** ~1 hour

---

## P2 — Maintainability Debt

### P2-1 · `FirestoreService` is a 3,132-line god object
**Location:** [`app/firebase/firestore_service.py`](../app/firebase/firestore_service.py)

Every blueprint imports it, so every data-layer change risks the whole app, and the class is effectively untestable.

**Fix:** Split by domain — `BlogRepository`, `UserRepository`, `AnalyticsRepository`, `NewsletterRepository`, `GalleryRepository` — behind a thin facade to keep call sites working during migration.
**Effort:** ~3 days

---

### P2-2 · Oversized modules
| File | Lines |
|---|---|
| [`app/firebase/firestore_service.py`](../app/firebase/firestore_service.py) | 3,132 |
| [`app/routes/blog_routes.py`](../app/routes/blog_routes.py) | 1,814 |
| [`app/agents/seo_agent.py`](../app/agents/seo_agent.py) | 1,450 |
| [`app/routes/site_routes.py`](../app/routes/site_routes.py) | 965 |

**Fix:** Split `blog_routes` into CRUD / generation / publishing blueprints; split `seo_agent` into keyword research, on-page analysis, and audit modules.
**Effort:** ~2 days

---

### P2-3 · Three competing entrypoints
**Location:** [`app.py`](../app.py), [`wsgi.py`](../wsgi.py), [`main.py`](../main.py)

Three files create the app with three different server configurations (waitress 16 threads, waitress 12 threads, gunicorn 8 threads). Only `main.py` is used in production. Divergent configs mean local behaviour does not predict production behaviour.

**Fix:** Keep `main.py`; delete the other two; document local startup in the README.
**Effort:** ~30 minutes

---

### P2-4 · Test coverage ~3%
**Location:** [`tests/`](../tests/) — 475 lines covering only validators and auth token verification

No route tests, no Firestore tests, no agent tests, no CI pipeline.

**Fix:** Add route smoke tests for every blueprint against a mocked `FirestoreService`; add contract tests for agent JSON parsing; wire GitHub Actions to run `pytest` on push.
**Effort:** ~1 week for a meaningful baseline

---

### P2-5 · O(n) query on every draft creation
**Location:** [`app/firebase/firestore_service.py:2260`](../app/firebase/firestore_service.py#L2260) (`_get_user_slugs`), [`:2279`](../app/firebase/firestore_service.py#L2279) (`_get_next_numeric_id`)

Both stream the user's entire blog collection to compute a unique slug and the next numeric ID. Read cost and latency grow linearly with the user's post count — a user with 500 posts pays 1,000 reads per draft created.

**Fix:** Store a `slugs` set and a `next_numeric_id` counter on the site-owner document; update transactionally.
**Effort:** ~4 hours

---

### P2-6 · Raw HTML rendered from stored content
**Location:** [`app/templates/site/site_post.html:117`](../app/templates/site/site_post.html#L117), [`site_about.html:32`](../app/templates/site/site_about.html#L32), [`site_legal.html:29`](../app/templates/site/site_legal.html#L29)

`{{ blog.html_content|safe }}` bypasses Jinja autoescaping. Content is AI-generated and author-edited, so the risk is bounded — but a `USER`-role account can inject script that then executes on the site owner's public domain for every visitor.

**Fix:** Sanitise with `bleach` against an allowlist at write time, not render time.
**Effort:** ~3 hours

---

### P2-7 · Pinned to Python 3.11.0
**Location:** [`runtime.txt`](../runtime.txt), [`render.yaml:29`](../render.yaml#L29)

3.11.0 is the initial release of that line and is missing several years of security patches.

**Fix:** Move to the latest 3.11.x. Note `datetime.utcnow()` is used throughout and is deprecated from 3.12 — replace with `datetime.now(timezone.utc)` before any major-version upgrade.
**Effort:** ~2 hours

---

### P2-8 · Aggressive 15-minute session timeout
**Location:** [`config.py:13`](../config.py#L13)

For a content-authoring tool where a user may draft for 20+ minutes without issuing a request, this logs people out mid-work.

**Fix:** Raise to 8 hours with a sliding refresh (already implemented via `SESSION_REFRESH_EACH_REQUEST`), or add a client-side keepalive ping plus a warning modal.
**Effort:** ~2 hours

---

## P3 — Hardening Backlog

| ID | Issue | Fix |
|---|---|---|
| P3-1 | No security headers (CSP, HSTS, X-Frame-Options) | Add `flask-talisman` |
| P3-2 | No dependency vulnerability scanning | Enable Dependabot + `pip-audit` in CI |
| P3-3 | No database backup strategy | Schedule Firestore export to Cloud Storage |
| P3-4 | No metrics or uptime monitoring | Add UptimeRobot; consider Prometheus metrics |
| P3-5 | Global `no-store` on all dynamic responses ([`app/__init__.py:203`](../app/__init__.py#L203)) | Scope to authenticated routes only |
| P3-6 | `_warm_firebase` calls `verify_id_token("dummy")` at boot ([`app/__init__.py:221`](../app/__init__.py#L221)) | Warm via a real cert fetch instead of a deliberately failing call |
| P3-7 | No API versioning on `/api/*` | Introduce `/api/v1/` before any external consumer |
| P3-8 | No graceful shutdown for in-flight AI tasks | Handle `SIGTERM`; drain the executor |

---

## Remediation Roadmap

### Phase 1 — Stop the bleeding (2–3 days)
> P0-1, P0-2, P0-3, P0-4, P1-2

Uploads moved off local disk · rate limiting on public AI endpoints · `MAX_CONTENT_LENGTH` set · secure session cookie · email-check throttled.

**Result:** No data loss, no trivially exploitable cost or availability holes.

---

### Phase 2 — Make it operable (1 week)
> P1-1, P1-3, P1-4, P1-5, P1-7, P0-7

CSRF protection · generic error responses · structured logging + Sentry · public-route caching · real health endpoint · paid hosting tier.

**Result:** Failures are diagnosable; Firestore reads drop by roughly 5–10×.

---

### Phase 3 — Make it scale (1–2 weeks)
> P0-5, P0-6, P1-6

Redis for cache and sessions · Celery/RQ for AI tasks · APScheduler moved to its own service · `--workers 4` · persistent task state · queue-depth limits.

**Result:** ~200–500 concurrent users on a 2 GB instance. AI throughput bounded by Gemini quota rather than a hardcoded `2`.

---

### Phase 4 — Pay down debt (ongoing)
> All P2, then P3

Split `FirestoreService` · split oversized modules · single entrypoint · test baseline + CI · fix O(n) queries · sanitise HTML.

---

## Issue Index

| ID | Priority | Issue | Effort |
|---|---|---|---|
| P0-1 | P0 | Uploads deleted on every deploy | 4h |
| P0-2 | P0 | Unauthenticated AI endpoints, no rate limiting | 3h |
| P0-3 | P0 | Unbounded upload body → OOM | 30m |
| P0-4 | P0 | Session cookie not `Secure` | 5m |
| P0-5 | P0 | Cannot scale horizontally | 1w |
| P0-6 | P0 | Task state RAM-only, silently lost | 1d |
| P0-7 | P0 | Free tier not production-viable | config |
| P1-1 | P1 | No CSRF protection | 4h |
| P1-2 | P1 | User-enumeration oracle | 1h |
| P1-3 | P1 | Raw exception strings to clients | 3h |
| P1-4 | P1 | Not debuggable in production | 1d |
| P1-5 | P1 | No caching on public routes | 1d |
| P1-6 | P1 | AI concurrency hardcoded, queue unbounded | 4h |
| P1-7 | P1 | Health check does not verify dependencies | 1h |
| P2-1 | P2 | `FirestoreService` god object (3,132 lines) | 3d |
| P2-2 | P2 | Oversized modules | 2d |
| P2-3 | P2 | Three competing entrypoints | 30m |
| P2-4 | P2 | Test coverage ~3% | 1w |
| P2-5 | P2 | O(n) query per draft creation | 4h |
| P2-6 | P2 | Raw HTML rendered via `\|safe` | 3h |
| P2-7 | P2 | Pinned to Python 3.11.0 | 2h |
| P2-8 | P2 | 15-minute session timeout | 2h |
| P3-1…8 | P3 | Hardening backlog | varies |

---

## What Is Already Done Well

Worth preserving through any refactor:

- **Clean app-factory pattern** with proper blueprint registration ([`app/__init__.py:39`](../app/__init__.py#L39))
- **Layered separation** — `routes` / `agents` / `services` / `utils` / `firebase` is consistently respected
- **Secret hygiene** — `.env` and `serviceAccountKey.json` gitignored and verified absent from git history
- **Deliberate, documented dependency pinning** ([`requirements.txt:1-5`](../requirements.txt#L1-L5)) with the `grpcio-status` / `protobuf` conflict explained inline
- **Infrastructure as code** — `render.yaml` blueprint with all env vars declared
- **`ProxyFix`, `WhiteNoise`, `flask-compress`** all correctly configured
- **Retry decorator** for transient Firestore unavailability ([`app/utils/retry.py`](../app/utils/retry.py))
- **Server-side validation** of the Gmail-only and password rules, using the cryptographically verified token email rather than the client-supplied payload ([`app/routes/auth.py:64-78`](../app/routes/auth.py#L64-L78))
- **Multi-tenant isolation** via a consistent `site_owner_id` scoping key
- **Genuinely explanatory comments** — several document *why* a non-obvious choice was made, which is rare and valuable

The architecture is sound. The gap between this and production is infrastructure decisions and operational tooling, not code quality.
