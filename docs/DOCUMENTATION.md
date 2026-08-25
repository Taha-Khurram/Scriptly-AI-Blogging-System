# Scriptly Documentation

Complete documentation for Scriptly - an AI-powered blog content generation platform.

---

## Table of Contents

1. [Overview](#overview)
2. [Quick Start](#quick-start)
3. [Configuration](#configuration)
4. [Features](#features)
5. [AI Agents](#ai-agents)
5b. [The conversational agent](AGENT.md) — the `/chat` studio, in its own document
6. [Public Site](#public-site)
7. [API Reference](#api-reference)
8. [Database Schema](#database-schema)
9. [Project Structure](#project-structure)
10. [Deployment](#deployment)
11. [Architecture](#architecture)
12. [Troubleshooting](#troubleshooting)

---

## Overview

Scriptly is a full-stack blog platform that automates the complete content creation lifecycle using AI. Built with Flask and Google Gemini, it provides:

- **AI Content Generation**: Blog posts, outlines, and newsletters using Google Gemini
- **AI Humanizer**: Bypass AI detectors with section-based rewriting and 5-pass post-processing
- **Comment System**: Public comments with AI moderation (auto-approve, edit, remove)
- **SEO Optimization**: Keyword analysis, readability scoring, meta tag generation
- **Public Blog Sites**: Each user gets a customizable public-facing blog with SEO-friendly URLs
- **Semantic Search**: AI-powered search using embeddings and agentic patterns
- **Newsletter System**: Generate and send newsletters to subscribers via Resend API
- **Team Collaboration**: Multi-user support with role-based access and approval workflows
- **Google Analytics Integration**: Real-time analytics dashboard with configurable date periods
- **Blog Scheduling**: Schedule posts for future publishing with AI-recommended times
- **Activity Log**: Paginated admin activity tracking with full audit trail
- **Google Sheets Sync**: Export blog and user data to Google Sheets
- **Category Management**: Organize content with auto-categorization

---

## Quick Start

### Prerequisites

- Python 3.9+
- Firebase project with Firestore and Authentication enabled
- Google Gemini API key
- (Optional) Resend API key for newsletters
- (Optional) RapidAPI key for SEO keyword research
- (Optional) Google OAuth credentials for Analytics

### Installation

```bash
# Clone repository
git clone https://github.com/Taha-Khurram/Final_Year_Project.git
cd Final_Year_Project

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Create environment file
cp .env.example .env
# Edit .env with your credentials

# Run application
python app.py
```

Access at `http://localhost:5000`

### First-Time Setup

1. Open `http://localhost:5000/signup`
2. Create your admin account (first user is automatically admin)
3. Configure your public site in **Dashboard > Site Settings**
4. Set your site slug for SEO-friendly public URLs
5. Start creating content!

---

## Configuration

### Environment Variables

Create a `.env` file in the project root:

```env
# === Required ===
FLASK_APP=app.py
FLASK_ENV=development
SECRET_KEY=your_32_byte_hex_secret_key

# Firebase Admin SDK
FIREBASE_SERVICE_ACCOUNT=serviceAccountKey.json

# Google Gemini AI
GEMINI_API_KEY=your_gemini_api_key

# Firebase Client SDK (for frontend authentication)
FB_API_KEY=your_firebase_api_key
FB_AUTH_DOMAIN=your-project.firebaseapp.com
FB_PROJECT_ID=your-project-id
FB_STORAGE_BUCKET=your-project.appspot.com
FB_SENDER_ID=your_sender_id
FB_APP_ID=your_app_id
FB_MEASUREMENT_ID=G-XXXXXXXXXX

# === Optional ===

# Newsletter (Resend API - free: 3,000 emails/month)
RESEND_API_KEY=re_xxxxxxxxxxxx
FROM_EMAIL=newsletter@yourdomain.com
FROM_NAME=Your Newsletter Name

# SEO Keyword Research (RapidAPI)
RAPIDAPI_KEY=your_rapidapi_key

# Google Analytics OAuth
GOOGLE_OAUTH_CLIENT_ID=your_oauth_client_id
GOOGLE_OAUTH_CLIENT_SECRET=your_oauth_client_secret

# Google Sheets Sync
GOOGLE_SHEETS_SPREADSHEET_ID=your_spreadsheet_id
```

### Firebase Setup

1. Create a project at [Firebase Console](https://console.firebase.google.com/)
2. Enable **Firestore Database** (start in test mode for development)
3. Enable **Authentication** providers:
   - Email/Password
   - Google (for OAuth login)
4. Generate a service account key:
   - Project Settings > Service Accounts > Generate new private key
   - Save as `serviceAccountKey.json` in project root
5. Copy Firebase web app config values to `.env`

### Firestore Security Rules

For production, configure Firestore rules to restrict access. Collections are created automatically on first use:

- `blogs` - Blog posts and drafts
- `users` - User accounts and roles
- `categories` - Blog categories
- `site_settings` - Per-user public site configuration
- `app_settings` - Global application settings
- `newsletter_subscribers` - Email subscribers
- `newsletter_history` - Sent newsletters
- `contact_submissions` - Contact form entries
- `activities` - Activity audit log
- `generations` - One transcript per AI generation run (prompt, plan, outcome)
- `comments` - Blog comments

---

## Features

### Content Generation

| Feature | Description |
|---------|-------------|
| **Topic to Blog** | Enter a topic, get a complete blog post with headings, intro, conclusion |
| **Outline Generation** | AI creates structured outlines before content expansion |
| **Content Expansion** | Expand outlines into full-length articles |
| **Auto-Formatting** | TOC generation, reading time calculation, heading structure |
| **Streaming Generation** | Real-time content streaming during generation |

### AI Humanization

| Feature | Description |
|---------|-------------|
| **Multi-Chunk Rewriting** | Content split at `##` headings, each chunk rewritten separately |
| **Rotating Prompts** | 4 style variants to break statistical fingerprints |
| **E-E-A-T Compliance** | Google's quality standards enforced in every rewrite |
| **5-Pass Post-Processing** | Zero-cost deterministic processing after API calls |
| **Fail-Open Design** | Original content preserved if any step fails |

### SEO Tools

| Feature | Description |
|---------|-------------|
| **Keyword Analysis** | Research and optimize target keywords |
| **Readability Score** | Flesch-Kincaid readability scoring |
| **Meta Generation** | Auto-generate SEO titles and meta descriptions |
| **Heading Structure** | Validate and optimize H1-H6 hierarchy |
| **Content Analysis** | Word count, keyword density, recommendations |

### Newsletter System

| Feature | Description |
|---------|-------------|
| **AI Generation** | Create newsletters from selected published blogs |
| **Email Sending** | Send via Resend API (3,000 free/month) |
| **Subscriber Management** | Track subscribers per site |
| **Batch Sending** | Send to all subscribers with personalization |
| **Unsubscribe Links** | Each email includes unsubscribe functionality |
| **Send History** | View all sent newsletters |

### Blog Scheduling

| Feature | Description |
|---------|-------------|
| **Future Publishing** | Schedule posts for a specific date and time |
| **AI Recommendations** | Get AI-suggested optimal publish times |
| **Background Processing** | APScheduler checks every 60 seconds for due posts |
| **Auto-Publish** | Scheduled blogs automatically publish at their time |
| **Activity Logging** | Auto-published posts are logged in the activity feed |

### Team Collaboration

| Feature | Description |
|---------|-------------|
| **User Invitation** | Send email invitations with signup links |
| **Role Management** | Admin and User roles with different permissions |
| **Approval Workflows** | Content goes through review before publishing |
| **Activity Tracking** | All team actions are logged |
| **Self-Delete Protection** | Admins cannot delete their own account |
| **Hierarchical Users** | `created_by` tracking for team organization |

### Google Analytics

| Feature | Description |
|---------|-------------|
| **OAuth2 Flow** | Secure Google OAuth authentication |
| **Real-time Data** | Active users right now |
| **Period Filters** | Today, 7 Days, 30 Days views |
| **Page Views** | Track which content performs best |
| **Traffic Sources** | Understand where visitors come from |

### Google Sheets Integration

| Feature | Description |
|---------|-------------|
| **Blog Export** | Sync blog metadata to Google Sheets |
| **User Export** | Export user data to spreadsheets |
| **Spreadsheet Configuration** | Configure target spreadsheet in site settings |

---

## AI Agents

There are two ways into the pipeline, and they share every specialist below.

**Single-shot** (`/create`) — one prompt in, a finished draft out. Fastest path
when you already know what you want.

**Conversational** (`/chat`) — an ongoing session that researches, plans, writes,
revises and manages the library, with you approving the plan before anything is
written. It does not replace the pipeline; it drives it one step at a time.
Its architecture, guarantees and migration notes are in **[docs/AGENT.md](AGENT.md)**.

### Architecture Overview

Scriptly implements a multi-agent system of specialized agents. Each can operate
independently or as part of an orchestrated pipeline.

```
Blog Agent (single-shot orchestrator)      Chat Agent (conversational orchestrator)
├── Content Agent → full article           ├── search_web        → live research
├── Formatting Agent → TOC, reading time   ├── Outline Agent     → plan, awaiting approval
├── SEO Agent → optimization pass          ├── Content Agent     → writes from the approved plan
└── Category Agent → auto-categorization   ├── Edit Agent        → one targeted change
                                           ├── Formatting/Category Agents
                                           └── list / get / delete (two-phase)
```

The specialists (`app/agents/`) know how to do one thing to a piece of text. The
orchestrators decide what to do and when — and in the conversational case,
whether you have agreed to it yet (`app/agent/`).

### Blog Agent (`blog_agent.py`)

The orchestrator that coordinates the full generation pipeline. Manages the flow between outline, content, formatting, and SEO agents with parallel execution where dependencies allow.

### Outline Agent (`outline_agent.py`)

Produces the structured plan a human approves before anything is written: a
working title, the angle, the named audience, and sections with the concrete
claim each will make. Returns JSON rather than prose, because an outline that
gets approved, revised and diffed has to be storable — see
[docs/AGENT.md](AGENT.md#the-two-guarantees).

Never invents sources. Research notes are passed in from `search_web` results or
not at all; a model asked to "include sources" with nothing to cite produces
plausible URLs that do not exist, and an outline approved *because* of its
sources is the worst place for that.

### Content Agent (`content_agent.py`)

Writes the post. Three entry points over **one** writing brief (the shared
`WRITING_RULES` constant — the AEO rules, the E-E-A-T rules, the banned AI tells,
the formatting requirements):

- `generate_blog(topic)` — waits for the whole post. For callers that only use
  the finished text.
- `stream_blog(topic, ...)` — streams the post, planning out loud first. What
  `/create` uses; the plan appears as the agent's reasoning.
- `stream_from_outline(outline, ...)` — writes against an outline a human already
  approved. **No planning block**: the plan exists and was agreed to, and asking
  the model to plan again is how an approved outline becomes a post that does not
  follow it.

One brief, not three copies, because three would drift and the difference would
only ever surface as "why does the chat version keep saying 'delve'".

### Edit Agent (`edit_agent.py`)

Applies one natural-language instruction to a post that already exists — rewrite
a section, change the tone, fix the intro. Returns the **whole** post with only
that change made, and reports what actually moved (word delta, section list) so
an over-edit is visible in the same turn rather than a week later.

Full document rather than a patch, deliberately: markdown has no stable line
identity, a model miscounts, and a mis-applied patch corrupts a post silently.
A full return is checkable.

### SEO Agent (`seo_agent.py`)

Analyzes and optimizes content for search engines:
- **Lightweight mode** - Quick analysis during generation pipeline
- **Full mode** - Comprehensive optimization for dedicated SEO work
- **RapidAPI integration** - External keyword research when API key configured

### Formatting Agent (`formatting_agent.py`)

Post-processes content with:
- Table of Contents generation from headings
- Reading time calculation
- Heading hierarchy validation
- Consistent styling and structure

### Humanize Agent (`humanize_agent.py`)

Multi-layered AI detection bypass system:

**Pipeline:**
1. Split content into 2 chunks at `##` headings
2. Assign different prompt variants to each chunk
3. Rewrite with Gemini (max 2 API calls)
4. Apply 5-pass deterministic post-processing

**4 Prompt Variants (rotated per chunk):**
- Direct - Straightforward rewriting
- Conversational - Casual, approachable tone
- Punchy - Short, impactful sentences
- Relaxed - Natural, flowing prose

**5-Pass Post-Processing (zero API cost):**
1. **AI Word Replacement** - 35+ high-probability AI words → human alternatives
2. **Sentence Splitting** - Sentences >20 words broken at conjunctions/commas
3. **Contraction Mixing** - 15% of paragraphs get one contraction expanded
4. **Paragraph Variation** - Merge consecutive short paragraphs, split long ones
5. **Imperfection Injection** - Filler words and parenthetical asides

**Design Principles:**
- E-E-A-T compliance in every rewrite
- Information gain (adds examples, not just rephrasing)
- Fail-open: original content preserved if any step fails
- Console logging for full pipeline visibility

### Comment Agent (`comment_agent.py`)

AI-powered comment moderation with a single Gemini API call:

| Decision | Condition | User Experience |
|----------|-----------|-----------------|
| Approve | Clean comment | Published immediately |
| Edit | Grammar/formatting issues | Cleaned version published |
| Remove | Spam, toxic, irrelevant | User sees "Thank you!" (rejection hidden) |
| Approve (fallback) | AI moderation fails | Comment approved as-is |

### Newsletter Agent (`newsletter_agent.py`)

Generates newsletter content from selected published blogs:
- Summarizes key points from each blog
- Creates engaging subject lines
- Formats for email delivery
- Supports personalization tokens

### Semantic Search Agent (`semantic_search_agent.py`)

Industry-standard agentic search implementation:

**Agentic Loop:**
```
Query → Understand → Plan → Execute Tools → Evaluate → Refine → Explain
```

**Components:**
- **AgentState** dataclass for tracking reasoning across steps
- **Rule-based intent classification** (no LLM cost):
  - Informational (questions: "how does React work?")
  - Navigational (content search: "python tutorial")
  - Exploratory (topic browse: "machine learning")
- **Synonym expansion** via static dictionary (no LLM cost)
- **Tool execution**: keyword search, vector search, category search
- **Quality evaluation** with automatic refinement loop
- **Insights API** returning agent reasoning to frontend

**Search Strategy by Intent:**
| Intent | Keyword | Vector | Category |
|--------|---------|--------|----------|
| Informational | 30% | 50% | 20% |
| Navigational | 60% | 30% | 10% |
| Exploratory | 40% | 40% | 20% |

### Category Agent (`category_agent.py`)

Auto-categorizes blog content based on topic analysis.

### Approval Agent (`approval_agent.py`)

Assists with content review workflows and quality assessment.

### Drafts Agent (`drafts_agent.py`)

Manages draft lifecycle and provides draft improvement suggestions.

### Publish Time Agent (`publish_time_agent.py`)

Recommends optimal publish times based on content type and audience patterns.

---

## Public Site

Each user gets a public blog at `/site/<site_slug>` where `site_slug` is a custom SEO-friendly identifier (e.g., `/site/my-awesome-blog`). Backwards compatible with `/site/<user_id>` for existing links.

### Pages

| Page | URL | Description |
|------|-----|-------------|
| Home | `/site/<slug>` | Landing page with featured posts |
| Blog | `/site/<slug>/blog` | Paginated post listing |
| Post | `/site/<slug>/blog/<post-slug>` | Individual article with TOC and comments |
| About | `/site/<slug>/about` | Customizable about page |
| Contact | `/site/<slug>/contact` | Contact form (stored in Firestore) |
| Category | `/site/<slug>/category/<name>` | Posts filtered by category |
| Privacy | `/site/<slug>/privacy-policy` | Privacy policy (customizable) |
| Terms | `/site/<slug>/terms-of-service` | Terms of service (customizable) |
| RSS | `/site/<slug>/feed.xml` | RSS feed |
| Sitemap | `/site/<slug>/sitemap.xml` | XML sitemap for SEO |
| Robots | `/site/<slug>/robots.txt` | Robots.txt |

### Site Settings

Configurable via Dashboard > Site Settings:

| Setting | Description |
|---------|-------------|
| `site_slug` | SEO-friendly URL slug |
| `site_name` | Blog display name |
| `site_description` | Tagline / description |
| `logo_url` | Logo image URL |
| `primary_color` | Brand color (hex) |
| `posts_per_page` | Pagination size |
| `social_links` | Twitter, LinkedIn, GitHub URLs |
| `contact_email` | Contact email address |
| `analytics_id` | Google Analytics measurement ID |
| `timezone` | Display timezone (e.g., `America/New_York`) |
| `date_format` | Date display format |
| `privacy_policy` | Privacy policy content (HTML) |
| `terms_of_service` | Terms of service content (HTML) |
| `spreadsheet_id` | Google Sheets spreadsheet ID for sync |

### Site Features

- **Responsive Design** - Mobile-first with hamburger menu
- **Newsletter Signup** - Subscription forms on multiple pages
- **Contact Form** - Submissions stored in Firestore
- **Social Sharing** - Twitter, LinkedIn, Facebook buttons
- **Table of Contents** - Auto-generated from post headings
- **Related Posts** - Same-category recommendations
- **Semantic Search** - Floating search button with agent insights panel
- **Comments** - Public commenting with AI moderation
- **Old Slug Redirects** - 301 redirects for changed URLs

### Performance Optimizations

| Optimization | Implementation |
|-------------|----------------|
| Compression | Gzip via Flask-Compress |
| Static Caching | 7-day cache via WhiteNoise |
| Query Caching | 2-minute in-memory cache |
| Prefetching | instant.page for fast navigation |
| Embedding Caching | Cached vector search results |

---

## API Reference

### Authentication

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/login` | Login page |
| GET | `/signup` | Signup page |
| POST | `/login` | Authenticate user |
| POST | `/signup` | Register new user |
| GET | `/logout` | End session |
| POST | `/verify_token` | Verify Firebase token |
| GET | `/forgot-password` | Forgot password page |
| POST | `/forgot_password` | Send password reset email |
| POST | `/verify_email_code` | Verify email code |
| POST | `/reset_password` | Complete password reset |

### Blog Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/dashboard` | Home dashboard |
| GET | `/blogs/create` | Blog creation page |
| POST | `/blogs/generate` | Generate blog with AI (streaming) |
| GET | `/blogs/<blog_id>` | View/edit blog |
| POST | `/blogs/<blog_id>/save` | Save blog changes |
| POST | `/blogs/<blog_id>/publish` | Publish blog |
| POST | `/blogs/<blog_id>/delete` | Delete blog |
| POST | `/blogs/<blog_id>/humanize` | Humanize content |
| POST | `/blogs/<blog_id>/seo` | Run SEO analysis |

### Conversational agent (`/chat`)

See [docs/AGENT.md](AGENT.md) for the flow and the guarantees behind these.

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/chat` | The studio. `?s=<id>` opens one conversation |
| GET | `/api/chat/sessions` | List conversations (keyset-paged on `before`) |
| POST | `/api/chat/sessions` | Open a conversation |
| GET | `/api/chat/sessions/<id>` | One conversation, its messages, and any turn still running in it |
| POST | `/api/chat/sessions/<id>` | Rename |
| DELETE | `/api/chat/sessions/<id>` | Delete the conversation. The posts it produced are untouched |
| POST | `/api/chat/sessions/<id>/messages` | Send a message. **202** with a `turn_id`; `409 agent_busy` if a turn is already running |
| GET | `/api/chat/turns/<id>/stream` | Tail a turn as SSE. Bounded lifetime; sends `reconnect` with a cursor, honours `Last-Event-ID` |
| GET | `/api/chat/turns/<id>?cursor=N` | The same event log by poll. Omit `cursor` to replay from the start |
| POST | `/api/chat/outlines/<id>/approve` | **The human in the loop.** Approves and starts the write. `?write=0` approves only |
| POST | `/api/chat/confirm` | Redeem a single-use confirmation token — the only path that performs a destructive action |

Neither of the last two is reachable by the agent. That is the approval
guarantee expressed as routing: its tools can propose, and a request from your
own browser disposes.

### Blog Listing & Filtering

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/drafts` | View draft posts |
| GET | `/approval-queue` | Admin approval queue |
| GET | `/all-blogs` | All blogs (admin only) |
| GET | `/blogs/filter` | Filter blogs by criteria |

### Creation History

Every AI generation writes one transcript, read back at `/history` as a
conversation. Scoped to the signed-in user in every case -- a transcript holds
the prompt somebody typed, so there is no team-wide view of this.

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/history` | Past conversations with the blog agent |
| GET | `/api/history` | One page of runs, keyset-paged on `?before=` |
| GET | `/api/history/<generation_id>` | One transcript in full |
| DELETE | `/api/history/<generation_id>` | Remove one transcript (the blog is untouched) |
| DELETE | `/api/history` | Clear the caller's history |

### User Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/users` | User management page (Admin) |
| GET | `/users/list` | List team members and invitations |
| POST | `/users/invite` | Send user invitation email |
| POST | `/users/<user_id>/edit-role` | Update user role |
| POST | `/users/<user_id>/delete` | Delete user from system |

### Newsletter

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/newsletter` | Newsletter management page |
| POST | `/newsletter/generate` | Generate newsletter from blogs |
| POST | `/newsletter/send` | Send newsletter to subscribers |

### Analytics

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/analytics` | Analytics dashboard |
| POST | `/analytics/auth` | Google OAuth authentication flow |
| GET | `/analytics/data` | Get analytics data |

### Settings

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/settings` | App settings page |
| POST | `/settings/save` | Save app settings |
| GET | `/site-settings` | Site configuration page |
| POST | `/site-settings/save` | Save site settings |

### Activity

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/activity` | Paginated activity log (10 per page) |

### Schedule

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/schedule` | Schedule management page |
| POST | `/schedule/publish-time` | Get AI-recommended publish time |
| POST | `/schedule/save` | Save scheduled blog |

### Public Site

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/site/<slug>` | Public site home page |
| GET | `/site/<slug>/blog` | Blog listing page |
| GET | `/site/<slug>/blog/<post-slug>` | Individual blog post |
| GET | `/site/<slug>/about` | About page |
| GET | `/site/<slug>/contact` | Contact page |
| POST | `/site/<slug>/contact` | Submit contact form |
| POST | `/site/<slug>/subscribe` | Newsletter signup |
| POST | `/site/<slug>/comments` | Submit comment (AI moderated) |
| POST | `/site/<slug>/search` | Semantic search |
| GET | `/site/<slug>/category/<name>` | Posts by category |
| GET | `/site/<slug>/feed.xml` | RSS feed |
| GET | `/site/<slug>/sitemap.xml` | XML sitemap |
| GET | `/site/<slug>/robots.txt` | Robots.txt |
| GET | `/site/<slug>/privacy-policy` | Privacy policy |
| GET | `/site/<slug>/terms-of-service` | Terms of service |

> **Note:** `<slug>` can be either a custom site slug (e.g., `my-blog`) or the user's Firebase ID for backwards compatibility.

---

## Database Schema

### `blogs` Collection

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Document ID |
| `title` | string | Blog title |
| `content` | string | HTML/markdown content |
| `slug` | string | URL-friendly slug |
| `old_slugs` | array | Previous slugs (for 301 redirects) |
| `numeric_id` | number | Sequential ID per user |
| `status` | string | DRAFT, UNDER_REVIEW, PUBLISHED |
| `category` | string | Blog category |
| `author_id` | string | Creator's user ID |
| `site_owner_id` | string | Site owner's user ID |
| `created_at` | timestamp | Creation time |
| `updated_at` | string | Last update datetime |
| `scheduled_at` | string | Scheduled publish time |
| `scheduled_by` | string | User who scheduled |
| `seo_title` | string | SEO meta title |
| `seo_description` | string | SEO meta description |
| `reading_time` | string | Estimated reading time |
| `toc` | string | Table of contents (JSON) |
| `embedding` | array | Vector embedding (768 dimensions) |

### `users` Collection

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Firebase UID |
| `email` | string | User email |
| `name` | string | Display name |
| `role` | string | ADMIN or USER |
| `created_by` | string | ID of inviting user |
| `created_at` | timestamp | Account creation time |
| `site_slug` | string | Custom public site slug |

### `activities` Collection

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Document ID |
| `user_id` | string | User who performed action |
| `user_name` | string | User display name |
| `type` | string | Activity type |
| `action_text` | string | Human-readable description |
| `blog_title` | string | Associated blog (if applicable) |
| `timestamp` | timestamp | When action occurred |

### `comments` Collection

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Document ID |
| `blog_id` | string | Associated blog |
| `author_name` | string | Comment author name |
| `author_email` | string | Comment author email |
| `content` | string | Comment text |
| `status` | string | APPROVED, PENDING, REJECTED |
| `created_at` | timestamp | Comment timestamp |
| `ai_action` | string | Action taken by AI moderation |

### `site_settings` Collection

Per-user site configuration including colors, fonts, social links, legal pages, pagination settings, and timezone preferences.

### `newsletter_subscribers` Collection

| Field | Type | Description |
|-------|------|-------------|
| `email` | string | Subscriber email |
| `site_owner_id` | string | Which site they subscribed to |
| `subscribed_at` | timestamp | Subscription time |
| `unsubscribed` | boolean | Unsubscribe status |

### `contact_submissions` Collection

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Submitter name |
| `email` | string | Submitter email |
| `message` | string | Message content |
| `site_owner_id` | string | Which site received it |
| `created_at` | timestamp | Submission time |

---

## Project Structure

```
FYP-main/
├── app/
│   ├── agents/                 # 13 AI agents
│   │   ├── blog_agent.py          # Pipeline orchestrator
│   │   ├── outline_agent.py       # Outline generation
│   │   ├── content_agent.py       # Content expansion
│   │   ├── seo_agent.py           # SEO optimization
│   │   ├── formatting_agent.py    # Formatting & TOC
│   │   ├── humanize_agent.py      # AI detector bypass
│   │   ├── comment_agent.py       # Comment moderation
│   │   ├── newsletter_agent.py    # Newsletter generation
│   │   ├── semantic_search_agent.py # Agentic search
│   │   ├── category_agent.py      # Auto-categorization
│   │   ├── approval_agent.py      # Review workflows
│   │   ├── drafts_agent.py        # Draft management
│   │   └── publish_time_agent.py  # Optimal publish times
│   ├── firebase/               # Firebase integration
│   │   ├── firebase_admin.py      # Admin SDK initialization
│   │   └── firestore_service.py   # All database operations
│   ├── routes/                 # API routes (10 blueprint modules)
│   │   ├── auth.py                # Authentication (login, signup, reset)
│   │   ├── blog_routes.py         # Blog CRUD & AI generation
│   │   ├── blogs_listing_routes.py # Filtering, drafts, approval queue
│   │   ├── history_routes.py      # AI generation transcripts
│   │   ├── user_mgmt.py           # User management
│   │   ├── site_routes.py         # Public blog site
│   │   ├── newsletter_routes.py   # Newsletter management
│   │   ├── analytics_routes.py    # Google Analytics
│   │   ├── settings_routes.py     # App & site settings
│   │   ├── activity_routes.py     # Activity log
│   │   └── schedule_routes.py     # Blog scheduling
│   ├── services/               # External service integrations
│   │   ├── email_service.py       # Resend API wrapper
│   │   ├── embedding_service.py   # Gemini embedding generation
│   │   └── google_sheets_service.py # Google Sheets sync
│   ├── static/                 # Frontend assets
│   │   ├── css/
│   │   │   ├── pages/            # Dashboard page styles (26 files)
│   │   │   └── site/             # Public site styles
│   │   ├── js/
│   │   │   ├── pages/            # Dashboard page scripts (26 files)
│   │   │   └── site/             # Public site scripts
│   │   └── images/               # Image assets
│   │       └── site/
│   ├── templates/              # Jinja2 templates (35 files)
│   │   ├── base.html             # Main dashboard layout
│   │   ├── home.html             # Dashboard home
│   │   ├── create_blog.html      # Blog creation
│   │   ├── history.html          # Generation history
│   │   ├── drafts.html           # Draft listing
│   │   ├── approval_queue.html   # Approval interface
│   │   ├── comments.html         # Comment moderation
│   │   ├── users.html            # User management
│   │   ├── analytics.html        # Analytics dashboard
│   │   ├── newsletter.html       # Newsletter interface
│   │   ├── schedule.html         # Scheduling interface
│   │   ├── seo_tools.html        # SEO tools
│   │   ├── formatting_tools.html # Formatting tools
│   │   ├── all_blogs.html        # All blogs listing
│   │   ├── activity.html         # Activity log
│   │   ├── app_settings.html     # App configuration
│   │   ├── site_settings.html    # Site configuration
│   │   ├── categories.html       # Category management
│   │   ├── login.html            # Login page
│   │   ├── signup.html           # Signup page
│   │   ├── forgot_password.html  # Password reset
│   │   ├── emails/               # Email templates
│   │   │   ├── invitation.html   # Team invitation
│   │   │   └── newsletter.html   # Newsletter template
│   │   ├── errors/
│   │   │   └── 404.html          # Not found page
│   │   ├── partials/
│   │   │   └── sidebar.html      # Navigation sidebar
│   │   └── site/                 # Public site templates
│   │       ├── site_base.html    # Site layout
│   │       ├── site_home.html    # Site home
│   │       ├── site_blog.html    # Blog listing
│   │       ├── site_post.html    # Individual post
│   │       ├── site_about.html   # About page
│   │       ├── site_contact.html # Contact page
│   │       ├── site_legal.html   # Legal pages
│   │       ├── site_404.html     # Site 404
│   │       └── partials/
│   │           ├── search_agent.html  # Search widget
│   │           ├── site_header.html   # Site header
│   │           └── site_footer.html   # Site footer
│   ├── utils/                  # Utility modules
│   │   ├── cache.py               # In-memory caching with prefix invalidation
│   │   ├── date_utils.py          # Timezone-aware date/time formatting
│   │   ├── slug_utils.py          # URL slug generation & uniqueness
│   │   └── parallel.py            # Parallel execution & timing utilities
│   ├── __init__.py             # App factory & initialization
│   └── scheduler.py            # APScheduler background jobs
├── docs/
│   └── DOCUMENTATION.md        # This file
├── app.py                      # Flask entry point
├── main.py                     # WSGI application wrapper
├── wsgi.py                     # WSGI entry point
├── config.py                   # Flask configuration class
├── requirements.txt            # Python dependencies (19 packages)
├── verify_setup.py             # Setup verification script
├── Dockerfile                  # Docker containerization
├── Procfile                    # Heroku/Railway process file
├── railway.json                # Railway deployment config
├── nixpacks.toml               # Nixpacks deployment config
├── firebase.json               # Firebase hosting config
├── .firebaserc                 # Firebase project config
├── serviceAccountKey.json      # Firebase admin credentials (gitignored)
└── .gitignore                  # Git ignore rules
```

### File Statistics

| Category | Count |
|----------|-------|
| Python Modules | 44 |
| HTML Templates | 35 |
| JavaScript Files | 26 |
| CSS Files | 26 |
| Dependencies | 19 |
| AI Agents | 13 |
| Route Blueprints | 10 |
| Firestore Collections | 10 |

---

## Deployment

### Local Development

```bash
python app.py
# Runs on http://localhost:5000 with debug mode
```

### Production - Gunicorn (Linux/Mac)

```bash
gunicorn main:app -w 4 -b 0.0.0.0:8080
```

### Production - Waitress (Windows)

```bash
waitress-serve --port=8080 main:app
```

### Docker

```bash
# Build
docker build -t scriptly .

# Run
docker run -p 8080:8080 \
  -e GEMINI_API_KEY=your_key \
  -e SECRET_KEY=your_secret \
  -v ./serviceAccountKey.json:/app/serviceAccountKey.json \
  scriptly
```

### Railway

1. Push code to GitHub
2. Connect Railway to your repository
3. Set environment variables in Railway dashboard
4. Deploy automatically on push

Configuration files: `railway.json`, `Procfile`, `Dockerfile`

### Nixpacks

Alternative deployment method configured via `nixpacks.toml`.

### Production Middleware

The application automatically configures:
- **WhiteNoise** - Static file serving with 7-day cache headers
- **ProxyFix** - Handles X-Forwarded headers from reverse proxies
- **Flask-Compress** - Gzip response compression
- **CORS** - Cross-origin request support

---

## Architecture

### Application Factory Pattern

The app uses Flask's application factory pattern (`app/__init__.py`):
1. Create Flask app instance
2. Load configuration from `config.py`
3. Initialize Firebase Admin SDK
4. Register all route blueprints
5. Configure middleware (WhiteNoise, Compress, ProxyFix)
6. Register template filters (date formatting)
7. Start background scheduler

### Request Flow

```
Client → Flask → Route Handler → Service/Agent → Firestore → Response
```

### Session Management

- 15-minute inactivity timeout
- Session refreshed on each authenticated request
- HTTPOnly cookies with Lax SameSite policy
- Firebase token verification on protected routes

### Background Scheduler

APScheduler runs as a daemon thread:
- Checks every 60 seconds for blogs past their `scheduled_at` time
- Updates status to PUBLISHED
- Logs activity for audit trail
- Clears relevant caches

### Caching Strategy

- **In-memory cache** with TTL and prefix-based invalidation
- Published blog lists cached for 2 minutes
- Cache cleared on publish/unpublish/delete operations
- Static files cached 7 days via WhiteNoise headers

### Security

| Mechanism | Implementation |
|-----------|----------------|
| Authentication | Firebase Auth (server-side token verification) |
| Authorization | Role-based (ADMIN/USER) with route decorators |
| Sessions | 15-min timeout, HTTPOnly, SameSite=Lax |
| Admin Routes | Return 404 (not 403) for unauthorized access |
| Password Reset | Firebase Auth email verification flow |
| CSRF Protection | Firebase token-based authentication |

---

## Troubleshooting

### Firebase Connection Issues

- Verify `serviceAccountKey.json` exists and path matches `.env`
- Check Firestore rules allow read/write for your service account
- Ensure Firebase Admin SDK is initialized (check console for errors)
- Verify project ID matches between service account and client config

### Gemini API Errors

- Verify API key is valid in [Google AI Studio](https://aistudio.google.com/)
- Check quota limits in Google Cloud Console
- For "model not found" errors, verify the model name in `blog_agent.py`
- Rate limiting: add delay between rapid generation requests

### Email Not Sending

- Verify Resend API key in [Resend Dashboard](https://resend.com/)
- Check domain verification status for custom domains
- Default sender (`onboarding@resend.dev`) works for testing
- Review Resend dashboard logs for delivery errors

### Semantic Search Not Working

- Ensure blogs have vector embeddings (check `embedding` field in Firestore)
- Run embedding backfill if needed: `python scripts/backfill_embeddings.py`
- Verify Gemini API key supports embedding model
- Check that blog status is PUBLISHED (search only indexes published posts)

### Authentication Issues

- Clear browser cookies and try again
- Verify Firebase Authentication is enabled for Email/Password
- Check that Firebase client config (FB_*) matches your project
- For Google OAuth: verify redirect URIs in Firebase Console

### Performance Issues

- Monitor Firestore read/write counts in Firebase Console
- Check in-memory cache hit rates via logging
- Verify static file caching headers (check browser Network tab)
- Consider reducing `posts_per_page` for faster page loads

### Scheduling Not Working

- Verify APScheduler is running (check console for scheduler logs)
- Ensure `scheduled_at` datetime format is correct
- Check timezone alignment between scheduler and user settings
- Scheduler won't start in Flask debug mode's reloader process

### Google Analytics Not Loading

- Complete OAuth flow in Dashboard > Analytics
- Verify OAuth client ID and secret are correct
- Check that Google Analytics Data API is enabled in Google Cloud
- Ensure the connected Google account has access to the Analytics property

---

## Dependencies

| Package | Purpose |
|---------|---------|
| flask | Web framework |
| python-dotenv | Environment variable loading |
| firebase-admin | Firebase Admin SDK (Firestore, Auth) |
| google-generativeai | Google Gemini AI (content generation, embeddings) |
| google-analytics-data | Google Analytics Data API |
| google-analytics-admin | Google Analytics Admin API |
| google-auth-oauthlib | Google OAuth2 authentication |
| google-auth | Google authentication library |
| requests | HTTP client |
| gunicorn | WSGI server (Linux/Mac production) |
| waitress | WSGI server (Windows production) |
| whitenoise | Static file serving middleware |
| flask-compress | Response gzip compression |
| pytrends | Google Trends integration |
| markdown | Markdown to HTML conversion |
| resend | Email sending service |
| numpy | Numerical operations (vector similarity) |
| APScheduler | Background task scheduling |
| gspread | Google Sheets API client |

---

## Support

For issues, open a ticket at [GitHub Issues](https://github.com/Taha-Khurram/Final_Year_Project/issues).

---

*Last Updated: May 2026*
