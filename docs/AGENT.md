# The conversational blog agent

Scriptly's `/chat` studio: an ongoing conversation that researches topics, plans
posts, writes them, revises them and manages the library — with a human
approving the plan before anything is written.

This document covers the architecture, the guarantees and how it was built on
top of the existing single-shot generator rather than beside it.

---

## Table of contents

1. [What changed, and what did not](#what-changed-and-what-did-not)
2. [The flow](#the-flow)
3. [The two guarantees](#the-two-guarantees)
4. [Architecture](#architecture)
5. [The tools](#the-tools)
6. [Turns, streaming and reattachment](#turns-streaming-and-reattachment)
7. [Conversational memory](#conversational-memory)
8. [Runaway protection](#runaway-protection)
9. [Data model](#data-model)
10. [Configuration](#configuration)
11. [Observability](#observability)
12. [Migration path](#migration-path)
13. [Known limits](#known-limits)

---

## What changed, and what did not

The single-shot generator (`/create` → `/api/generate` → `BlogAgent.run_pipeline`)
**still works exactly as it did**. It is the right tool when you know what you
want and want it now: one prompt, one model call, a draft in your library.

The conversational agent is a second front door onto the same machinery. It does
not replace the pipeline; it *drives* it, one step at a time, with you in the
loop between the steps.

Concretely, of the nine specialist agents in `app/agents/`, the chat agent reuses
five unchanged (content, formatting, category, SEO, humanize — the last two via
existing routes) and adds two:

| Component | Status | Note |
|---|---|---|
| `content_agent.py` | **extended** | New `stream_from_outline()` entry point. The writing brief is now a shared `WRITING_RULES` constant so all three entry points compose one copy. The existing topic-driven prompt is **byte-identical** — there is a test that proves it. |
| `outline_agent.py` | **new** | Real structured outlines. `BlogAgent` derives an "outline" by regexing `##` headings out of a finished post; that is fine when nobody reads it, and useless when a human has to approve it. |
| `edit_agent.py` | **new** | Targeted edits to an existing post. Previously only possible by hand in TinyMCE, or by regenerating and losing every edit since. |
| `blog_agent.py` | untouched | Still the one-shot orchestrator. |
| `formatting_agent.py`, `category_agent.py`, `seo_agent.py`, `humanize_agent.py` | untouched | Called by the chat tools exactly as the create screen calls them. |

The document a chat-written post produces is **the same shape** `/api/generate`
writes. That is deliberate and load-bearing: Drafts, the approval queue, the
public site, the SEO tools and the scheduler all read that shape, and a
chat-written post that differed subtly would be a bug in every one of those
screens at once. There is a test asserting the shape.

---

## The flow

```
  You: "Research the latest thinking on SaaS pricing pages and outline a post."
       │
       ├─ search_web ×N          ← only if the topic needs current facts
       └─ create_outline         ← stored as `pending_approval`
       │
   ┌───┴─────────────────────────────────────────────────────────┐
   │  THE AGENT STOPS HERE. It cannot write the post yet.        │
   │  Not "will not" — create_blog refuses an unapproved outline.│
   └───┬─────────────────────────────────────────────────────────┘
       │
  You: ─── "Approve" button ────────► approve_outline (route, `via=ui`)
       └── "yes, go ahead" ─────────► submit_outline_approval (verifies your words)
       │
       └─ create_blog             ← streams the post, saves it as a DRAFT
       │
  You: "Make the intro punchier."      → edit_blog        (resolves to that post)
       "Shorten section 2."            → edit_blog
       "Delete my old draft on this."  → delete_blog      (asks; deletes nothing)
       "What's in my drafts?"          → list_blogs
```

Everything after the first post keeps going in the same conversation. The
session persists, so "that one" keeps meaning the right thing.

---

## The two guarantees

Almost all of the design follows from these. Both are enforced in **data and
routing**, not in the prompt.

### 1. No post is written without an approved outline

The prompt asks for it. The prompt is not the guarantee — one ambiguous turn,
one summarised history, one model update, and an agent that was asked nicely
writes anyway. So:

- `create_outline` stores its result with `status: 'pending_approval'`. There is
  no argument that makes it store anything else.
- `create_blog` loads the outline and **refuses** unless `status == 'approved'`,
  returning a tool result the agent must explain rather than an exception.
- `ChatRepository.approve_outline` is the only writer of `'approved'`, and the
  only things that call it are `POST /api/chat/outlines/<id>/approve` (a click)
  and `submit_outline_approval` (which re-reads your actual last message —
  see below).
- `create_blog` takes **no** `force` / `approved` / `skip_approval` parameter.
  A test passes each of those anyway and asserts nothing is written.

**Approval by typing.** When you reply "yes, go ahead", the model may call
`submit_outline_approval` — but that tool has no `approved` argument. It asks the
*server* to check, and the server reads `ToolContext.last_user_message`, your
verbatim text, stored before the model saw it. `app/agent/approval.py` decides,
with deliberately narrow rules: too long, any word of contrast or change, any
hedge, and it is not an approval. "Yes but make section 2 shorter" is a revision
request. A false negative costs one clarifying question; a false positive spends
minutes of model time writing something you did not ask for.

### 2. Destructive actions are two-phase, and the agent cannot complete them

`delete_blog` **deletes nothing**. It records what *would* be deleted, returns a
single-use token, and puts a confirmation card in the conversation. The deletion
happens in `execute_confirmed_delete`, reachable only from
`POST /api/chat/confirm` — a request your browser makes because you clicked.

- The token is consumed inside a Firestore transaction, so a double-click or a
  replayed request deletes once.
- Tokens are time-boxed (10 minutes) and scoped to one action and one user.
- Ownership is re-checked at the moment of deletion, not just when the token was
  minted: those are separated in time, and a role can change in between.
- Tokens live in Firestore rather than memory because the task pool is
  per-process — an in-memory token issued by one worker would be invisible to
  the worker handling the click.

---

## Architecture

```
app/agent/                    the orchestrator (singular)
├── loop.py         the tool-calling loop: model → tools → results → repeat
├── registry.py     the tool table: declarations + dispatch + guards + audit
├── context.py      ToolContext: what a tool may know, and may spend
├── events.py       TurnLog / TurnRegistry: a turn as a cursor-sliced event log
├── prompts.py      the system prompt and the per-turn state block
├── approval.py     reading approval out of what the user actually typed
└── tools/
    ├── research.py  search_web
    ├── outlines.py  create_outline, revise_outline, submit_outline_approval
    └── blogs.py     create_blog, edit_blog, delete_blog, list_blogs, get_blog

app/agents/                   the specialists (plural) — unchanged convention
├── outline_agent.py   NEW  structured, reviewable plans
├── edit_agent.py      NEW  one instruction applied to one post
└── content_agent.py   EXT  + stream_from_outline()

app/services/search_service.py   NEW  pluggable web search (tavily/serper/brave)
app/repositories/chat.py         NEW  sessions, messages, outlines, confirmations
app/routes/chat_routes.py        NEW  the page, SSE, poll, approve, confirm
app/templates/chat.html          NEW
app/static/js/pages/chat.js      NEW
app/static/css/pages/chat.css    NEW
```

`app/agent/` (singular) is the orchestrator; `app/agents/` (plural) holds the
single-purpose LLM workers this app has always had. The specialists know how to
do one thing to a piece of text. The orchestrator decides what to do, when, and
whether you have agreed to it yet.

### Layer responsibilities

| Layer | Owns | Never does |
|---|---|---|
| `routes/chat_routes.py` | HTTP, auth, rate limits, SSE transport, approval and confirmation endpoints | Call the model or a tool directly |
| `agent/loop.py` | One turn: model round trips, tool dispatch, guards, persistence | Decide what a tool means |
| `agent/registry.py` | Tool declarations, dispatch, budgets, dedup, audit logging | Know about HTTP or Firestore |
| `agent/tools/*` | One capability each, independently callable | Read `session`, `request`, or `current_user()` |
| `agents/*` | One LLM operation each | Know a conversation exists |
| `repositories/chat.py` | Persistence and its ownership rules | Know about the model |

---

## The tools

Nine, declared once in `app/agent/registry.py` — the same table produces the
JSON schemas the model sees and the dispatch map, so a tool cannot be offered
without being callable.

| Tool | Does | Notes |
|---|---|---|
| `search_web(query, max_results)` | Live web search | Degrades to "no sources, say so" when unconfigured |
| `create_outline(topic, research_notes, tone, length, keywords)` | Structured plan, stored `pending_approval` | Always the first step toward a new post |
| `revise_outline(feedback, outline_id)` | Reworks the plan | Supersedes the old one; approval never carries over |
| `submit_outline_approval(outline_id)` | Asks the server to verify your words | No `approved` argument, by design |
| `create_blog(outline_id, tone, length, keywords)` | Writes the post, saves a DRAFT | Refuses an unapproved outline |
| `edit_blog(instructions, blog_id, apply_title)` | One targeted change | Returns the whole post; reports what actually moved |
| `delete_blog(blog_id)` | **Asks** for confirmation | Deletes nothing |
| `list_blogs(status, search, category, limit)` | Finds posts | Focuses a single result |
| `get_blog(blog_id)` | Reads one post in full | Puts it in focus |

### Every tool follows the same contract

```python
def tool(ctx, **params) -> dict:      # a plain JSON-serialisable dict
```

- **Authority comes from `ctx`, never from a parameter.** No tool takes a
  `user_id`. There is no argument a model could fill in wrongly, and every read
  and write re-checks `ctx.user_id`.
- **`**_ignored` on every tool.** Models invent plausible extra arguments
  (`region`, `recency`, `lang`). A `TypeError` from a hallucinated keyword would
  fail a turn over something the tool could simply disregard. A *missing
  required* argument is still an error.
- **No exception for anything the conversation can recover from.** A failed
  search returns `ok: True, available: True, failed: True` with a message,
  because the right response is "I couldn't reach the web — want me to write it
  anyway?" and an exception takes that choice away.
- **`ok` is about whether the tool ran**, not about whether it found anything.
  An empty result set with `ok: True` is a good answer to an obscure search, and
  the model must be able to tell it apart from "search is down" and from "search
  is not configured here".

Because authority and dependencies arrive as an argument, every tool is callable
from a test with a fake repository, no Flask request and no model. See
`tests/test_agent_tools.py`.

### Access control

Stricter than the dashboard, deliberately. A blog is reachable if
`author_id == ctx.user_id`, or if the caller is an `ADMIN` and the post's
`site_owner_id` matches their own site. Missing and not-yours return the same
`not_found`, so a natural-language interface cannot be used to enumerate another
tenant's post ids.

> **Pre-existing gap, flagged not fixed.** `/api/update_blog/<id>` and
> `/api/delete_blog/<id>` in `blog_routes.py` perform **no ownership check** —
> any signed-in user can edit or delete any post by id. That is untouched here
> because tightening it affects the approval-queue flow where an admin edits
> another author's post, and that is a separate decision with its own testing.
> What matters for this feature is that the agent does not inherit the gap.

---

## Turns, streaming and reattachment

A turn is not a request. It can search the web, plan a post, write 1,100 words
and file it — minutes of work, far longer than any sensible HTTP timeout, and
the browser may close halfway through.

```
POST /api/chat/sessions/<id>/messages   →  202 { turn_id }
GET  /api/chat/turns/<id>/stream        →  SSE, bounded lifetime
GET  /api/chat/turns/<id>?cursor=N      →  the same log, by poll
```

The turn runs in the shared task pool (`app/utils/task_manager.py`) and writes to
a `TurnLog` — an ordered, cursor-sliced event log. The browser *attaches*.

**Why an event log rather than writing tokens into an SSE response.** The obvious
design loses the turn whenever the connection blinks, and on a several-minute
turn a blink is the normal case. The log gives three things a raw stream cannot:

- **Reattachment.** "Everything after event 41." A browser that slept, navigated
  away or dropped its connection replays the turn instead of showing a blank
  pane while the agent is mid-post.
- **Two transports over one truth.** SSE tails it, polling slices it. They cannot
  disagree, because there is one record of what happened.
- **A bounded SSE lifetime.** This app runs gthread with a small fixed thread
  count, and `task_manager.py` documents why it rejected SSE for generation: a
  response held open for a whole turn pins a worker thread for minutes. Because
  the log is the source of truth, the SSE response can expire after 90s and hand
  back a `reconnect` event — using the same resume mechanism reattachment needs
  anyway, so it costs no extra client code and no pinned threads.

Every SSE frame carries `id: <cursor>`, so the browser's own `Last-Event-ID`
handling resumes an interrupted stream for free.

### Event types

`status`, `thought`, `tool_start`, `tool_end`, `token`, `draft`, `card`,
`message`, `done`, `error`.

Two things stream, for two different reasons. The agent's **prose** streams
because it is conversation and latency is felt — including text it produces
*before* deciding to call a tool, so "let me look that up" is on screen before
the search runs. The **blog draft** streams as `draft` events because it takes
minutes and a progress bar would be a lie. They are different event types so the
client can put prose in the bubble and the draft in a card without guessing.

### Cards

Structured attachments — an outline awaiting approval, a finished draft, a delete
confirmation, a source list. They are emitted to the live log *and* persisted
with the message, and `chat.js` renders both with one function. A card that
appears during a turn and vanishes on reload is worse than one that never
appeared: the reader learns not to trust the pane.

---

## Conversational memory

Two mechanisms, and neither is "let the model infer it from the transcript".

**Focus pointers.** `chat_sessions` carries `focus_blog_id`, `focus_blog_title`
and `focus_outline_id`. Tools update them whenever they touch something specific
(including `get_blog` — looking something up is exactly how you signal what you
are about to talk about), and the loop writes them back at the end of the turn.
Tools accept `blog_id=None` meaning "the one in focus", so "make the intro
punchier" resolves to a lookup rather than an inference.

**The state block.** `prompts.state_block()` renders the current focus, the
pending outline and its approval status into the system prompt on every turn.
Inference drifts — a model reading twenty messages will sometimes decide "the
post" means the first one discussed — and you would have no way to see that it
had. A rendered block is the same state the tools resolve against, so what the
agent believes and what the tools will do cannot come apart silently.

History is replayed as prose only, not as tool calls and results. The model does
not need to re-derive *how* it found something out; it needs to know what was
said, and the state block carries the rest authoritatively. Replaying every tool
call would multiply the context of a long conversation for information already
summarised in the reply.

---

## Runaway protection

Four guards, each catching a different failure:

| Guard | Where | Default | Catches |
|---|---|---|---|
| Iteration cap | `loop.py` | `AGENT_MAX_ITERATIONS=7` | A model that never stops calling tools |
| Wall clock | `loop.py` | `AGENT_TURN_DEADLINE_SECONDS=420` | A turn that stalls holding a worker thread |
| Per-tool budget | `context.py` | 3 searches, 2 writes, 4 edits… | Thirty different reasonable-looking calls |
| Duplicate suppression | `registry.py` | exact `(name, args)` match | The same call thirty times |

The last is the most common in practice: a model that dislikes a tool result
re-issues the identical call rather than changing approach. It is answered with
a result telling it to stop, not by running the tool again — re-running is at
best wasted work and at worst a second write.

Seven iterations is the longest legitimate chain plus one spare: search →
search → outline → (approval) → approve → write → reply.

Beyond the guards: a tool that raises becomes a tool *result*, so a turn never
dies because one step did. Every exit path from `AgentLoop.run` persists a reply
and closes the turn log — including the path where the model produced no prose
at all, where `_fallback_text` writes the sentence it should have from what
actually happened. A turn that ended without either would leave a browser
attached to nothing and a conversation whose last message is yours.

Rate limits: `RATELIMIT_CHAT` (default `90 per hour`) keyed per **user**, not per
IP — a turn can spend minutes of model time, so the budget belongs to the
account, and keying by address would let one user multiply it by changing
networks. One turn at a time per conversation (`409 agent_busy`), because two
concurrent turns would both write the focus pointer and interleave two answers.

---

## Data model

Four Firestore collections. `firestore.indexes.json` declares the composite
indexes; deploy them with `firebase deploy --only firestore:indexes`. Every read
has a bounded unindexed fallback so a missing index degrades rather than showing
"no conversations yet" to someone with fifty.

### `chat_sessions`
One document per conversation. `title` (derived from the first message, or
renamed), `preview`, `message_count` (also the sequence counter), `blog_count`,
and the three focus pointers. Ordered by `updated_at` — the sidebar lists things
to go back to, so the conversation you were in five minutes ago belongs at the
top however old it is.

### `chat_messages`
One document per message: `session_id`, `seq`, `role` (`user`/`agent`/`system`),
`text`, `tool_calls` (the audit trail), `cards`, `status`, `turn_id`.

Not an array on the session: Firestore caps a document at 1 MiB and rewrites the
whole document on every array append, so a long conversation would get
quadratically more expensive to extend and would eventually stop being
extendable. `seq` is assigned inside a transaction against the session, so two
messages written in the same millisecond still have a total order —
`created_at` alone does not guarantee one, and a chat log that occasionally
renders out of order is indistinguishable from a broken agent.

### `blog_outlines`
The approval gate, made durable. `status` (`pending_approval` / `approved` /
`superseded`), `approved_at`, `approved_via`, `revision`, `revision_of`,
`blog_id`, plus the plan itself. A revision supersedes rather than mutates, so an
approval arriving after a revision request cannot land on the version you
rejected.

### `agent_confirmations`
Pending destructive actions: `action`, `target_id`, `summary`, `payload`,
`expires_at`, `consumed_at`. Single-use, time-boxed, consumed transactionally.
Swept half-hourly under the scheduler lease.

### Moving to Postgres

The repository mixin is the seam. `ChatRepository` is ~40 methods behind a
narrow interface (`create_chat_session`, `append_chat_message`,
`approve_outline`, `consume_confirmation`, …) and nothing above it knows
Firestore exists — the tools take a `db` on their context, the loop takes one in
its constructor, and the tests pass a plain Python fake. Two things need real
thought in a port, and both are called out in the code:

1. `append_chat_message` needs its transaction (or a Postgres sequence) to keep
   `seq` a total order.
2. `consume_confirmation` needs its transaction, or a double-click deletes twice.

---

## Configuration

```bash
# Guards on one user message
AGENT_MAX_ITERATIONS=7
AGENT_TURN_DEADLINE_SECONDS=420

# Web search: tavily | serper | brave. Empty is a supported state.
SEARCH_PROVIDER=
SEARCH_API_KEY=
SEARCH_MAX_RESULTS=5
SEARCH_TIMEOUT_SECONDS=12

RATELIMIT_CHAT=90 per hour
```

**Search is optional and its absence is first-class.** With no provider the
agent still works: it says once, briefly, that it is writing from its own
knowledge rather than live sources, and it is instructed never to invent a
citation to fill the gap. The empty state on `/chat` says so too, because a user
who asked for research and got none deserves to know it is a deployment setting
and not the agent ignoring them. A *misspelled* provider is refused at boot with
an error rather than silently ignored — a typo would otherwise present as an
agent that quietly stopped citing sources.

Per-tool call budgets live in `app/agent/context.py` (`DEFAULT_BUDGETS`), not in
env: they are a property of the tool, not of the deployment.

**Keep `WEB_CONCURRENCY=1`.** A turn's live event log is per-process, like the
task pool. An SSE attach that landed on another worker would find no turn — the
reply still arrives, since it is written to Firestore either way, but the live
view would be lost at random in proportion to worker count. Raise
`GUNICORN_THREADS`, not workers.

---

## Observability

**Every tool call is logged**, structured, in `registry.dispatch`:

```json
{"tool": "create_outline", "tool_args": {"topic": "pricing pages"},
 "tool_ok": true, "tool_summary": "outline_id=abc123",
 "duration_ms": 2140.3, "user_id": "…", "session_id": "…"}
```

Arguments are included: they are the user's own topics and instructions, they go
to the model anyway, and a tool log without arguments cannot answer the only
question anyone ever asks it. No tool takes an identity argument, so there is
nothing else about the user in them.

Per turn: `iterations`, `tool_calls`, `tool_usage` (per-tool counts),
`blogs_created`, `duration_ms`. The audit trail is also persisted on the message
it belongs to, and shown in the UI as a collapsible step list — so "what did it
do when it said that?" is one click away, not a log query.

`/healthz` gains two checks:

- `search` — which provider is live. **Degraded** when none is, so a deployment
  that meant to have research can see that it does not.
- `agent` — live turns held in this process, so a leak in the turn logs shows up
  on a dashboard rather than only as growing RSS.

---

## Migration path

Nothing was replaced. The order this was built in, and the order to review it:

1. **`search_service.py`** — the one capability the app had no path to.
2. **`repositories/chat.py`** — a new mixin on the existing `FirestoreService`.
   Composed in; no existing call site changed.
3. **`agents/outline_agent.py`**, **`agents/edit_agent.py`** — two new
   specialists following the existing convention.
4. **`agents/content_agent.py`** — extended with a third entry point. The shared
   brief was extracted to `WRITING_RULES`; `tests/test_agent_tools.py` proves the
   topic-driven prompt is byte-identical to before, so `/create` is unaffected.
5. **`gemini_client.py`** — one new method, `stream_with_tools`. Existing methods
   untouched.
6. **`agent/`** — the new orchestrator package.
7. **`routes/chat_routes.py`** — a new blueprint, registered in `_BLUEPRINTS`.
8. **UI** — a new page reusing `components/thread.css`, which Create and History
   already share. A live turn, a finished run and a conversation from last week
   are the same exchange at different times, and they look it.

### If you want to go further

- **Retire `/create`.** Not recommended. One prompt to a finished draft is
  genuinely the faster path when you know what you want, and the chat flow
  deliberately costs you an approval step.
- **Publish from chat.** Add a `publish_blog` tool behind the same two-phase
  confirmation as delete. Publishing is currently a human action on the Drafts
  or Approval screen, which is the right default.
- **Humanize and SEO as tools.** Both already exist as agents with routes; a
  thin tool wrapper each. `humanize_agent` is the interesting one — it takes
  minutes, so it wants the same `draft` event streaming `create_blog` uses.
- **A shared broker.** Celery/RQ for the task pool would let turns survive a
  deploy and make `WEB_CONCURRENCY>1` safe. `TurnRegistry` would move to Redis;
  the cursor interface is already the right shape for it.

---

## Known limits

- **Per-process turn logs.** See `WEB_CONCURRENCY=1` above. The durable record is
  always written; only the live view is process-local.
- **A turn cannot be cancelled.** The UI's "Stop watching" stops watching, and
  says so. Python has no safe thread cancellation, and `task_manager` documents
  the same limitation for generation. A user who stops watching still gets the
  post.
- **No full-text fetch.** `search_web` uses provider snippets and does not fetch
  page bodies. Fetching N arbitrary URLs from a server is a different problem
  with a different threat model (SSRF, redirect chains, unbounded bodies) and
  belongs behind its own allowlisted fetcher.
- **Approval detection is conservative.** Unusual phrasings of "yes" fall through
  to a clarifying question. That is the intended direction to be wrong in.
- **One conversation, one turn.** Concurrent turns in one session are refused
  with `409`. Two conversations at once are fine.
- **History window.** 20 messages of prose reach the model per turn
  (`prompts.HISTORY_MESSAGES`). Beyond that, older context is carried by the
  state block and the focus pointers rather than verbatim.
