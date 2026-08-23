# Production Readiness Audit

**Project:** Scriptly (FYP)
**Audited:** 2026-08-15 · **Remediated:** 2026-08-24
**Scope:** ~14,500 lines Python · 14 blueprints · 11 AI agents · 26 templates
**Target deployment:** Render + Firebase/Firestore + Google Gemini + Redis

---

## Verdict

| Dimension | Was | Now | Notes |
|---|---|---|---|
| Code structure & organisation | Good | **Strong** | God object split into 13 repositories; single definition of each control |
| Secret hygiene | Good | **Good** | Unchanged and verified; CI now fails if a secret file is tracked |
| Security posture | **Poor** | **Good** | CSRF, rate limiting, secure cookies, CSP/HSTS, write-time sanitisation |
| Scalability | **Poor** | **Adequate** | Redis-shared cache and limits, leased scheduler; task pool still per-process |
| Operability | **Poor** | **Strong** | Structured logs with request ids, real health probes, runbook |
| Test coverage | **Poor** (3%) | **Adequate** (34%) | 275 tests; infrastructure and security paths well covered |
| **Production-grade?** | **No** — 7 × P0 | **Yes, with one caveat** | See [Remaining work](#remaining-work) |

The one caveat: **`WEB_CONCURRENCY` must stay at 1** until background AI task
state moves to a shared broker. Everything else that forced single-worker
operation has been fixed; this one has not. `gunicorn.conf.py` warns at boot if
it is raised, and `/healthz` reports the pool as process-local.

---

## Measured behaviour

`python scripts/loadtest.py` — real WSGI server (waitress, 8 threads), real
request pipeline, Firestore mocked so the numbers describe the application
layer rather than the network.

| Scenario | Throughput | p50 | p95 | p99 | Errors |
|---|---|---|---|---|---|
| Liveness, 16 concurrent | 651 rps | 24 ms | 28 ms | 30 ms | 0 |
| Liveness, 64 concurrent | 533 rps | 119 ms | 139 ms | 143 ms | 0 |
| Login page, 16 concurrent | 366 rps | 41 ms | 76 ms | 95 ms | 0 |
| Login page, 64 concurrent | 413 rps | 141 ms | 165 ms | 183 ms | 0 |
| API 401, 32 concurrent | 431 rps | 73 ms | 87 ms | 93 ms | 0 |
| HTML 404, 32 concurrent | 299 rps | 86 ms | 156 ms | 165 ms | 0 |
| **Mixed, 2000 req @ 48** | **349 rps** | **128 ms** | **218 ms** | **270 ms** | **0** |

**Stability under sustained load** — the property that matters most:

- 0 transport failures, 0 5xx across 2,000 requests
- Latency **fell** over the run (first-tenth p50 217 ms → last-tenth p50 125 ms,
  drift 0.57×) as caches warmed. Degradation would show as drift > 1
- Cache hit rate 99.96%
- Thread count flat at 14 and bounded — the regression check for the
  per-request-executor leak
- At 64 concurrent against 8 threads, requests queue and latency rises
  proportionally with **no errors**, which is correct backpressure rather than
  collapse

---

## P0 — Launch blockers · all resolved

### P0-1 · User uploads deleted on every deploy — **FIXED**
Images went to the container filesystem, which is wiped on deploy while the
Firestore metadata survives, leaving permanently broken links with no recovery
path — and making horizontal scaling impossible.

`app/services/storage_service.py` puts storage behind one interface: Firebase
Storage in production, local disk for offline development with a startup
warning so it cannot be mistaken for a production config.
`scripts/migrate_uploads_to_storage.py` moves existing rows — idempotent,
uploads before rewriting metadata, and reports rows whose files are already gone
rather than pretending to repair them. `/healthz` reports `storage.durable`.

### P0-2 · Unauthenticated AI endpoints, no rate limiting — **FIXED**
`flask-limiter` with Redis storage. Per-IP limits on all four public endpoints;
per-*user* limits on authenticated generation, because one call is minutes of
model work and IP keying lets an account multiply its allowance by rotating
networks. Visitor input is bounded before it reaches a model, so prompt length
cannot be used to inflate token spend.

### P0-3 · Unbounded upload body → OOM — **FIXED**
`MAX_CONTENT_LENGTH` is set, so Werkzeug rejects an oversized body at the WSGI
layer before any view runs. Type detection now reads magic bytes rather than
trusting the filename, and the stored `Content-Type` comes from what was
actually found.

### P0-4 · Session cookie over plaintext HTTP — **FIXED**
`SESSION_COOKIE_SECURE` is on by default and only relaxed in
`DevelopmentConfig`, where localhost is http. Production additionally validates
that `SECRET_KEY` is neither a placeholder nor under 32 characters, and refuses
to boot otherwise.

### P0-5 · Cannot scale horizontally — **MOSTLY FIXED**
Two of the three blockers are gone: the cache is shared through Redis (with an
in-process fallback that degrades rather than fails), and the scheduler takes a
single-runner lease (`SET NX EX`, or an OS file lock without Redis) so it can no
longer double-publish.

The third — the AI task pool — remains process-local. See
[Remaining work](#remaining-work).

### P0-6 · Background task state RAM-only — **PARTIALLY FIXED**
Still in memory, but no longer silently lossy: admission is bounded so a full
queue returns 503 with `Retry-After` instead of accepting work it cannot start;
an exception escaping a job now marks it failed instead of vanishing into an
unread future; polling reports queue position; and SIGTERM drains in-flight work
so a deploy cannot truncate a Firestore write mid-flight.

### P0-7 · Free plan not a production tier — **FIXED (config)**
`render.yaml` moves to `starter` and declares a managed Redis, with the reason
recorded inline. A billing decision, now a documented one.

---

## P1 — First sprint · all resolved

| ID | Issue | Resolution |
|---|---|---|
| **P1-1** | No CSRF protection | `flask-wtf` `CSRFProtect`; token published to the fetch layer via a readable cookie; every exemption carries a written reason |
| **P1-2** | User-enumeration oracle | `/api/auth/check-email` never consults Firebase, answers uniformly, and is rate-limited |
| **P1-3** | Raw exception strings to clients | Exception hierarchy with status codes; unexpected failures logged whole and answered with a generic message plus the request id |
| **P1-4** | Not debuggable in production | JSON logs, per-request correlation ids in `X-Request-ID` and every error body, access logging with duration, secret redaction, optional Sentry |
| **P1-5** | No caching on public routes | Data layer caches; the blanket `no-store` is now scoped to authenticated responses, so public pages are cacheable by the browser and any CDN |
| **P1-6** | AI concurrency hardcoded to 2 | Configurable pool, bounded queue, 503 with `Retry-After` when full, queue position in the poll response |
| **P1-7** | Health check verified nothing | `/livez`, `/readyz`, `/healthz` with per-component checks, each time-boxed and run concurrently; `render.yaml` points at `/readyz` |

---

## P2 — Maintainability debt · all resolved

| ID | Issue | Resolution |
|---|---|---|
| **P2-1** | 3,132-line `FirestoreService` god object | Split into 13 repository mixins (largest 716 lines, median 194). Public surface verified identical by AST comparison: same 99 methods, same signatures, same 9 retry decorators |
| **P2-2** | Oversized modules | `firestore_service.py` 3,300 → 86. `blog_routes.py` and `seo_agent.py` remain large; see below |
| **P2-3** | Three competing entrypoints | `app.py` and `wsgi.py` deleted; `main.py` plus a commented `gunicorn.conf.py` |
| **P2-4** | Test coverage ~3% | 275 tests, 34%. Infrastructure and security paths 79–90% |
| **P2-5** | O(n) query per draft creation | One indexed query, bounded at 25 probes. 500 reads → 1 for a 500-post site |
| **P2-6** | Raw HTML rendered via `\|safe` | Sanitised at **write** time in the data layer, so every write path is covered and nothing depends on a template remembering |
| **P2-7** | Pinned to Python 3.11.0 | `render.yaml` moves to 3.11.9. All 48 `datetime.utcnow()` calls migrated, which was the blocker for 3.12 |
| **P2-8** | 15-minute session timeout | 8-hour sliding window, configurable |

---

## P3 — Hardening backlog

| ID | Issue | Status |
|---|---|---|
| P3-1 | No security headers | **Done.** CSP, HSTS, frame, MIME, referrer, permissions, COOP |
| P3-2 | No dependency scanning | **Done.** `pip-audit` in CI, advisory |
| P3-3 | No backup strategy | **Open.** Schedule a Firestore export |
| P3-4 | No metrics or uptime monitoring | **Partial.** `/healthz` exposes component state; no external monitor configured |
| P3-5 | Global `no-store` on all responses | **Done.** Scoped to authenticated responses |
| P3-6 | Warm-up verifies a dummy token | **Documented.** The failing call *is* the mechanism — it populates the certificate cache before the signature check fails. The comment now says so |
| P3-7 | No API versioning | **Open.** Introduce `/api/v1/` before any external consumer |
| P3-8 | No graceful shutdown | **Done.** SIGTERM drains the scheduler and task pool |

---

## Also fixed, not in the original audit

Found while working, mostly by the new tests:

1. **Timezone bug in scheduled publishing.** `.replace(tzinfo=None)` discarded
   the offset instead of converting, so a post scheduled for `10:00+05:00` was
   stored as 10:00 UTC and published five hours late.
2. **Reflected XSS in `/unsubscribe`.** The `email` query parameter was
   interpolated raw into HTML, including into an attribute value.
3. **`/unsubscribe` was entirely non-functional.** `(request.json or {})` raises
   on a non-JSON body, so the emailed link *and* its own confirm button both
   returned 415. Nobody could unsubscribe.
4. **Session role defaulted to `ADMIN`.** A user record missing its role granted
   full administrative access. Authorization must fail closed.
5. **Thread leak on every login.** Two `ThreadPoolExecutor(max_workers=1)`
   constructions per request, never shut down.
6. **Session timeout trusted an unparsable timestamp.** A corrupted or tampered
   `last_activity` made a session effectively immortal — the opposite of what
   the code's own comment claimed.
7. **Task-status polling confirmed other users' task ids** by answering 403
   rather than 404.
8. **`humanize` had no ownership check.** Any signed-in account could spend AI
   budget rewriting another user's draft and overwrite it with the result.
9. **Latent `NameError`** in `drafts_agent.update_draft_content` (dead code,
   deleted).
10. **`optimization_routes` had a private cache instance**, invisible to the
    rest of the app and to other workers, so the same paid upstream lookup was
    repeated per worker and could not be invalidated.
11. **`'@' in email` accepted `a@b`** for values later used as mail recipients.
12. **Comment sanitisation used a regex tag-stripper** that an unclosed
    `<img src=x onerror=...` walks straight through.

---

## Remaining work

### Must be done before raising `WEB_CONCURRENCY` above 1
**Move the AI task pool to a shared broker** (Celery or RQ, Redis-backed).
Task state lives in the worker that accepted the job, so with N workers a
status poll has an (N−1)/N chance of landing on a worker that has never heard of
that task id. Roughly a day's work. Sticky sessions are a workaround, not a fix.

### Worth doing next
1. **Migrate off `google-generativeai`** — end-of-life, warns on import.
   Confined to `app/services/gemini_client.py` by design.
2. **Split `blog_routes.py`** (1,880 lines) into CRUD / generation / publishing.
   Deferred deliberately: it is the least-tested large module, and splitting it
   before its tests exist trades a maintainability problem for a correctness
   risk.
3. **Split `seo_agent.py`** (1,450 lines) into keyword research / on-page
   analysis / audit.
4. **Firestore backup** — scheduled export to Cloud Storage (P3-3).
5. **External uptime monitoring** against `/readyz` (P3-4).
6. **Remove `'unsafe-inline'` from the CSP** — needs a nonce pass over ~40
   templates.
7. **Raise repository test coverage.** The 13 repository modules sit at 7–21%;
   they are the layer where a wrong query silently returns the wrong data.

### Accepted, documented risks
- **CSP allows `'unsafe-inline'` and `'unsafe-eval'`** for scripts. Required by
  the current templates and editor. Stated in `app/core/security.py`.
- **`grpcio-status` pinned to 1.71.x.** 1.75+ needs protobuf ≥ 6.31.1, which
  conflicts with the protobuf 5.x the `google-*` libraries require.
- **The in-process cache fallback** when `REDIS_URL` is unset is correct for one
  worker only. Reported as degraded by `/healthz` rather than assumed.

---

## What was already good

Worth preserving through any future refactor, and largely why this work went
smoothly:

- **Clean app-factory pattern** with proper blueprint registration
- **Layered separation** — `routes` / `agents` / `services` / `utils` /
  `firebase` was consistently respected, which is what made the repository split
  a pure move
- **Secret hygiene** — `.env` and `serviceAccountKey.json` gitignored and
  verified absent from history
- **Deliberate, documented dependency pinning**, including the
  `grpcio-status`/protobuf conflict explained inline
- **Infrastructure as code** via `render.yaml`
- **`ProxyFix`, `WhiteNoise`, `flask-compress`** all correctly configured
- **Multi-tenant isolation** via a consistent `site_owner_id` scoping key
- **Server-side validation** of the Gmail-only and password rules, using the
  cryptographically verified token email rather than the client payload
- **Genuinely explanatory comments.** Several documented *why* a non-obvious
  choice was made — rare, and the reason a number of decisions here could be
  preserved rather than guessed at
