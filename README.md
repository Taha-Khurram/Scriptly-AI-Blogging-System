<div align="center">

<img src="app/static/images/logo_text.png" alt="Scriptly" width="220" />

# Scriptly — AI Blog Platform

**Generate. Humanize. Publish. On autopilot.**

An AI-powered blog content platform built with Flask and Google Gemini — it runs the full
content lifecycle, from topic ideation to a public-facing blog site, with humanization,
SEO tooling, team collaboration, and a 13-agent AI pipeline.

![Flask](https://img.shields.io/badge/Flask-3.x-000000?style=flat-square&logo=flask&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=flat-square&logo=python&logoColor=white)
![Gemini](https://img.shields.io/badge/Google%20Gemini-2.5%20Flash-4285F4?style=flat-square&logo=googlegemini&logoColor=white)
![Firebase](https://img.shields.io/badge/Firebase-Firestore-FFCA28?style=flat-square&logo=firebase&logoColor=black)
![Auth](https://img.shields.io/badge/Auth-Firebase%20%2B%20Google%20OAuth-EA4335?style=flat-square&logo=googleauthenticator&logoColor=white)
![Analytics](https://img.shields.io/badge/Analytics-Google%20Analytics-E37400?style=flat-square&logo=googleanalytics&logoColor=white)
![Render](https://img.shields.io/badge/Deploy-Render-46E3B7?style=flat-square&logo=render&logoColor=white)
![License](https://img.shields.io/badge/License-Proprietary%20(All%20Rights%20Reserved)-6E56CF?style=flat-square)

</div>

---

## 🌟 Highlights

- **📝 AI blog pipeline** — 13 specialized agents take a topic to a finished, SEO-scored article: outline → content → formatting → SEO → categorization, orchestrated end-to-end.
- **🕵️ AI Humanizer** — beats detectors (GPTZero, Originality.ai, ZeroGPT) with 2-chunk rotating-prompt rewriting, E-E-A-T enforcement, and a **5-pass** zero-cost post-processor.
- **🔎 Semantic Search Agent** — agentic search with intent classification, query expansion, and hybrid vector + keyword retrieval, with the agent's reasoning shown to users.
- **🌐 Public blog sites** — every user gets a customizable, SEO-friendly public site (`/site/<slug>`) with RSS, sitemap, comments, newsletter forms, and social sharing.
- **👥 Team collaboration** — multi-user with role-based access, invitations, approval workflows, and a full paginated activity audit trail.
- **📊 Google integrations** — real-time Analytics dashboard (OAuth), plus a Google Sheets Activity Agent that batches every dashboard click into a single "Blogs" tab.
- **⚡ SEO Optimization Suite** — URL/keyword metrics via Ahrefs and full site-audit reports, all through RapidAPI.
- **🖼️ Media Gallery & Leads** — upload/manage blog images and triage contact-form submissions with read/unread stats.
- **🚀 Production-ready** — gzip + WhiteNoise static caching, in-memory query cache, APScheduler auto-publish, and one-click Render deploys via `render.yaml`.

---

## 🗺️ App map

| Area | Screens |
|------|---------|
| **Create & draft** | Create Blog (streaming) · Drafts · Approval Queue · All Blogs |
| **Publish** | Schedule (AI-recommended times) · Categories · Newsletter |
| **Engage** | Comment Moderation · Leads (contact submissions) · Gallery |
| **Insights** | Analytics · SEO Tools · Optimization (Ahrefs) · Activity Log |
| **Public site** | Home · Blog · Post · About · Contact · Legal · RSS/Sitemap |
| **Settings** | Site Settings · App Settings · User Management |
| **Auth** | Login · Sign up · Forgot Password · Google OAuth |

---

## 🧰 Tech stack

| Concern | Choice |
|---------|--------|
| **Framework** | Flask 3.x (app factory + blueprints) |
| **Language** | Python 3.11 |
| **Database** | Firebase Firestore (NoSQL) |
| **Auth** | Firebase Auth — Email/Password, Google OAuth, Password Reset |
| **AI / LLM** | Google Gemini (`gemini-2.5-flash`) |
| **Embeddings** | Google `gemini-embedding-001` (768-dim) |
| **Email** | Gmail SMTP (`smtplib` + App Password) |
| **Analytics** | Google Analytics Data API |
| **SEO data** | RapidAPI — keyword research, Ahrefs URL metrics, site audit |
| **Sheets** | Google Sheets API (`gspread`) — Activity Agent |
| **Scheduling** | APScheduler (background auto-publish) |
| **Static / perf** | WhiteNoise + Flask-Compress (gzip), instant.page prefetch |
| **Server** | Gunicorn (Linux/Mac) · Waitress (Windows) |
| **Deploy** | Render (`render.yaml`) |

---

## 🏗️ Architecture

Scriptly is a single Flask app assembled by an **app factory** (`app/__init__.py`) that
registers feature blueprints under `app/routes/` and boots an **APScheduler** background
worker for scheduled publishing.

```
Topic ──▶ Blog Agent (orchestrator)
            ├─ Outline Agent      → structured outline
            ├─ Content Agent      → full article
            ├─ Formatting Agent   → TOC, reading time, headings
            ├─ SEO Agent          → keywords, readability, meta
            ├─ Category Agent      → auto-categorization
            └─ Humanize Agent      → detector-bypass rewrite (on demand)
                                       │
Firestore ◀── embeddings (gemini-embedding-001) ◀── published blog
     │
     └──▶ Semantic Search Agent (vector + keyword hybrid) ──▶ Public site
```

- **13 AI agents** (`app/agents/`) — each wraps a single Gemini model with a focused role; the Blog Agent chains them into the generation pipeline.
- **Firebase layer** (`app/firebase/`) — Admin SDK init + a `firestore_service` that centralizes all reads/writes across ~16 collections.
- **Services** (`app/services/`) — Gmail email, Gemini embeddings, and the Google Sheets activity agent.
- **Utils** (`app/utils/`) — in-memory cache, retry/backoff, parallel execution, background task manager, timezone-aware dates, slug generation, and validators.
- **Public sites** are served from `site_routes.py` and rendered from `app/templates/site/`, fronted by gzip + a 7-day static cache and a 2-minute query cache.

### AI agents

| Agent | Purpose |
|-------|---------|
| **Blog** | Orchestrates the full generation pipeline |
| **Outline** | Structured blog outlines from topics |
| **Content** | Expands outlines into complete articles |
| **SEO** | Keyword analysis, readability scoring, meta generation |
| **Formatting** | TOC, reading time, heading structure |
| **Humanize** | Detector-bypass rewriting with E-E-A-T |
| **Comment** | Real-time moderation for public comments |
| **Newsletter** | Newsletters from published blogs |
| **Semantic Search** | Agentic vector + keyword hybrid search |
| **Category** | Auto-categorization of content |
| **Approval** | Content review workflow assistance |
| **Drafts** | Draft management and suggestions |
| **Publish Time** | AI-recommended optimal publish times |

<details>
<summary><strong>🧠 Humanize Agent internals</strong></summary>

```
Content → split into 2 chunks at ## headings → rewrite with rotating prompts → 5-pass post-process → validate
```

- **2-chunk rewriting** with **4 prompt variants** (Direct, Conversational, Punchy, Relaxed) to break statistical fingerprints
- **E-E-A-T compliance** enforced in every prompt
- **5-pass post-processing** (zero API cost): AI-word replacement · long-sentence splitting · contraction mixing · paragraph-length variation · imperfection injection

</details>

<details>
<summary><strong>📈 Google Sheets Activity Agent</strong></summary>

```
User action → frontend tracker (batched) → /api/track-activity → server queue → flush worker → Google Sheets "Blogs" tab
```

- Single delegated `document` listener captures every click, navigation, and form submit
- Client-side batching, flushed every 5s or on unload via `sendBeacon`
- Server-side write queue batches up to 20 rows per Sheets write
- Toggle from **Site Settings → Google Sheets**

</details>

---

## 🚀 Quick start

### Prerequisites
- Python 3.11 (Render build target, pinned in `runtime.txt`; 3.9+ works locally)
- Firebase project with Firestore + Authentication enabled
- Google Gemini API key

### Install & run
```bash
git clone https://github.com/Taha-Khurram/Final_Year_Project.git
cd Final_Year_Project
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Create a .env file in the project root (see Environment Variables below)
python app.py                    # http://localhost:5000
```

**First run:** sign up at `/signup` (first user becomes admin) → configure your public
site in **Dashboard → Site Settings** → start creating content.

---

## 🔑 Environment variables

Create a `.env` in the project root:

| Variable | Description | Required |
|----------|-------------|:--------:|
| `SECRET_KEY` | Flask secret key (32-byte hex) | ✅ |
| `FIREBASE_SERVICE_ACCOUNT` | Path to Firebase service account JSON | ✅ |
| `GEMINI_API_KEY` | Google Gemini API key | ✅ |
| `FB_API_KEY` | Firebase client API key | ✅ |
| `FB_AUTH_DOMAIN` | Firebase auth domain | ✅ |
| `FB_PROJECT_ID` | Firebase project ID | ✅ |
| `FB_STORAGE_BUCKET` | Firebase storage bucket | ✅ |
| `FB_SENDER_ID` | Firebase messaging sender ID | ✅ |
| `FB_APP_ID` | Firebase app ID | ✅ |
| `FB_MEASUREMENT_ID` | Firebase Analytics measurement ID | ⬜ |
| `GMAIL_USER` | Gmail address used to send email | ⬜ |
| `GMAIL_APP_PASSWORD` | Gmail App Password (not your account password) | ⬜ |
| `FROM_NAME` | Email sender display name (default `Scriptly`) | ⬜ |
| `RAPIDAPI_KEY` | RapidAPI key — SEO keyword research | ⬜ |
| `AHREFS_RAPIDAPI_KEY` | RapidAPI key — Ahrefs URL/keyword metrics | ⬜ |
| `SITE_AUDIT_RAPIDAPI_KEY` | RapidAPI key — site audit reports | ⬜ |
| `GOOGLE_OAUTH_CLIENT_ID` | Google OAuth client ID (Analytics) | ⬜ |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Google OAuth client secret (Analytics) | ⬜ |
| `GOOGLE_SHEETS_SPREADSHEET_ID` | Google Sheets spreadsheet ID | ⬜ |

---

## 🌍 Public blog site

Each user gets a public blog at `/site/<site_slug>`:

| Page | Description |
|------|-------------|
| Home | Landing page with featured posts |
| Blog | Paginated listing with category filters |
| Post | Article with TOC, sharing, comments |
| About | Customizable author page |
| Contact | Contact form (stored as Leads) |
| Privacy / Terms | Configurable legal pages |
| RSS / Sitemap | `/feed.xml` and `/sitemap.xml` |

SEO-friendly slugs · responsive + mobile nav · newsletter forms · semantic search with
agent insights · social sharing · related posts · gzip + 7-day static cache + 2-minute
query cache + instant.page prefetch.

---

## 📦 Deployment

```bash
# Local
python app.py

# Production — Gunicorn (Linux/Mac). Single worker + threads: the app keeps an
# in-memory cache and a background scheduler, so multiple workers would duplicate
# jobs and split the cache.
gunicorn main:app --workers 1 --threads 8 --timeout 300 -b 0.0.0.0:8080

# Production — Waitress (Windows)
waitress-serve --port=8080 main:app
```

### Deploy to Render (free tier, no card required)

The repo ships a [`render.yaml`](render.yaml) Blueprint that provisions a single
Python web service running the app, the background workers, and the APScheduler
auto-publisher together.

1. Push the repo to GitHub.
2. In the [Render dashboard](https://dashboard.render.com/), click **New +** →
   **Blueprint** and select the repo. Render reads `render.yaml` automatically.
3. When prompted, fill in every secret env var (see [`.env.example`](.env.example)).
   For `FIREBASE_SERVICE_ACCOUNT`, paste the **entire** service-account JSON as
   the value — the app parses either a file path or a raw JSON string.
4. Deploy. After the first build, add the `https://<your-service>.onrender.com`
   callback to your Google OAuth authorized redirect URIs.

> **Notes on the free plan:** the service sleeps after ~15 min of inactivity and
> cold-starts on the next request; while asleep the APScheduler auto-publisher
> does not fire. That's fine for demos — upgrade to a paid instance (or move the
> scheduler to Render Cron) if you need always-on publishing.

---

## 🗄️ Database schema

Firestore collections (created automatically):

| Collection | Description |
|-----------|-------------|
| `blogs` | Posts with content, metadata, embeddings, scheduling |
| `users` | Accounts with roles and team hierarchy |
| `invitations` | Pending user invitations |
| `categories` | Categories with post counts |
| `activities` | Admin activity audit trail |
| `comments` | Comments with moderation status |
| `site_settings` | Per-user public site configuration |
| `app_config` | Global application settings |
| `analytics_config` | Google Analytics OAuth/config per user |
| `newsletter_subscribers` | Email subscribers per site |
| `newsletter_drafts` | Draft newsletters before sending |
| `newsletter_history` | Sent newsletter records |
| `contact_submissions` | Contact form entries (Leads) |
| `schedule_entries` | Scheduled blog publish jobs |
| `gallery_images` | Uploaded image metadata |
| `seo_reports` | Saved SEO / site-audit reports |

---

## 📁 Project structure

```
FYP-main/
├── app/
│   ├── agents/          # 13 AI agents (blog, outline, content, seo, humanize, …)
│   ├── firebase/        # Admin SDK init + firestore_service
│   ├── routes/          # 13 feature blueprints (auth, blog, site, optimization, …)
│   ├── services/        # Gmail email, Gemini embeddings, Google Sheets agent
│   ├── static/          # 30 CSS · 30 JS · images
│   ├── templates/       # 39 Jinja2 templates (dashboard, site, emails, errors)
│   ├── utils/           # cache, retry, parallel, task_manager, dates, slugs, validators
│   ├── __init__.py      # App factory
│   └── scheduler.py     # Background job scheduler
├── docs/                # DOCUMENTATION.md — full reference
├── scripts/             # backfill_embeddings.py
├── tests/               # Pytest suite
├── app.py · main.py · wsgi.py   # Entry points
├── config.py · requirements.txt · firestore.indexes.json
├── render.yaml · runtime.txt · .env.example
└── firebase.json · .firebaserc · .gitignore
```

---

## 📚 Documentation

See [docs/DOCUMENTATION.md](docs/DOCUMENTATION.md) for setup details, the full API
reference, AI agent internals, public-site customization, and the production guide.

---

## 🤝 Contributing

Contributions are accepted only from collaborators the Author has explicitly
authorized (see the note in the license below). If that's you, read
[CONTRIBUTING.md](CONTRIBUTING.md) for setup, coding standards, commit
conventions, and the pull-request process.

---

## 📄 License

**Proprietary — All Rights Reserved.** Scriptly is **not** open source. No use,
copying, modification, deployment, or distribution is permitted **without the
Author's prior written permission**. Unauthorized use is a violation of copyright
law. See [LICENSE](LICENSE) for the full terms.

Part of a Final Year Project (FYP) at the University.

---

## 👤 Author

**Taha Khurram** · [GitHub](https://github.com/Taha-Khurram)
