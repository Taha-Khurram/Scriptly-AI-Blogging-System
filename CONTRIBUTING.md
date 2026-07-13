# Contributing to Scriptly

Thanks for your interest in Scriptly. This document explains how to set up the
project, the standards we hold code to, and the workflow for proposing changes.

> [!IMPORTANT]
> Scriptly is **proprietary software** — see [LICENSE](LICENSE). It is not open
> source, and no use, copying, or distribution is permitted without the Author's
> prior written consent. Contributions are accepted **only from collaborators the
> Author has explicitly authorized**. By submitting a contribution, you agree that
> your work becomes part of the Software and is subject to the same License, and
> you assign all rights in that contribution to the Author. If you have not been
> granted access, please contact the Author before doing any work.

---

## Table of contents

- [Code of conduct](#code-of-conduct)
- [Getting started](#getting-started)
- [Development workflow](#development-workflow)
- [Branching & commits](#branching--commits)
- [Coding standards](#coding-standards)
- [Testing](#testing)
- [Pull requests](#pull-requests)
- [Reporting bugs & requesting features](#reporting-bugs--requesting-features)
- [Security](#security)

---

## Code of conduct

Be respectful, constructive, and professional in all interactions. Assume good
intent, keep discussions technical, and focus on the work. Harassment or abusive
behavior of any kind is not tolerated.

---

## Getting started

### Prerequisites

- Python 3.11 (the Docker/Nixpacks build target; 3.9+ works locally)
- A Firebase project with Firestore and Authentication enabled
- A Google Gemini API key
- Git

### Local setup

```bash
git clone https://github.com/Taha-Khurram/Final_Year_Project.git
cd Final_Year_Project

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

Create a `.env` file in the project root with the variables documented in the
[README](README.md#-environment-variables), then run:

```bash
python app.py                       # http://localhost:5000
```

> Never commit secrets. `.env` and `serviceAccountKey.json` are gitignored and
> must stay out of version control. Use placeholder values when sharing configs.

---

## Development workflow

1. Confirm you have authorization to contribute (see the note at the top).
2. Sync your local `main` with the remote: `git pull origin main`.
3. Create a topic branch off `main` (see [Branching & commits](#branching--commits)).
4. Make focused changes, keeping each branch scoped to a single concern.
5. Run the test suite and linters locally before pushing.
6. Open a pull request against `main` and request review.

---

## Branching & commits

**Branch names** — use a short, descriptive, kebab-case name prefixed by type:

```
feat/newsletter-scheduling
fix/analytics-oauth-refresh
docs/update-agent-architecture
refactor/firestore-service-cache
```

**Commit messages** — this repo uses
[Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<optional scope>): <short summary>
```

Common types: `feat`, `fix`, `docs`, `refactor`, `perf`, `test`, `chore`.

```
feat(analytics): add configurable date-range selector
fix(pjax): re-initialize page scripts on every navigation
perf(cache): batch Firestore reads for the blog listing
```

Keep the summary in the imperative mood, under ~72 characters, and add a body
when the change needs context ("why", not just "what").

---

## Coding standards

### Python

- Follow [PEP 8](https://peps.python.org/pep-0008/); 4-space indentation, no tabs.
- Prefer clear, descriptive names over abbreviations.
- Add docstrings to modules, classes, and non-trivial functions.
- Keep functions focused; extract helpers into `app/utils/` when logic is reused.
- Match the surrounding code's style, naming, and structure.

### Architecture conventions

- **Routes** live in `app/routes/` as Flask blueprints — one blueprint per feature
  area. Register new blueprints in the app factory (`app/__init__.py`).
- **AI agents** live in `app/agents/`, one class per agent, each wrapping a single
  Gemini model with a focused responsibility.
- **Firestore access** goes through `app/firebase/firestore_service.py` — do not
  scatter raw `db.collection(...)` calls across routes.
- **External integrations** (email, embeddings, Sheets) live in `app/services/`.
- **Templates** are Jinja2 under `app/templates/`; keep dashboard and public-site
  templates separated (`templates/` vs `templates/site/`).
- Never hard-code secrets, API keys, or model names in multiple places — read
  configuration from `config.py` / environment variables.

### Frontend

- Keep page-specific CSS/JS under `app/static/css/pages/` and `app/static/js/pages/`.
- Public-site assets belong under the `site/` subfolders.

---

## Testing

The project uses [pytest](https://docs.pytest.org/) (configured in `pytest.ini`).

```bash
pytest                     # run the full suite
pytest tests/test_validators.py   # run a single file
pytest -k oauth -v         # run tests matching a keyword
```

- Add or update tests for any behavior you change; place them in `tests/`.
- All tests must pass before a pull request is merged.
- Prefer fast, isolated unit tests; mock external services (Firebase, Gemini,
  RapidAPI) rather than calling them live.

---

## Pull requests

Before opening a PR, make sure:

- [ ] The branch is up to date with `main` and merges cleanly.
- [ ] The change is focused and scoped to a single concern.
- [ ] Tests pass locally (`pytest`) and new behavior is covered.
- [ ] No secrets, credentials, or personal data are included in the diff.
- [ ] Docs (README / `docs/DOCUMENTATION.md`) are updated if behavior changed.
- [ ] Commit messages follow Conventional Commits.

In the PR description, explain **what** changed and **why**, list any manual
testing performed, and link related issues. Keep PRs small and reviewable; split
large efforts into a series of focused PRs where practical.

---

## Reporting bugs & requesting features

Open an issue with:

- **Bugs** — steps to reproduce, expected vs. actual behavior, environment
  (OS, Python version), and relevant logs or screenshots. Redact any secrets.
- **Features** — the problem you're trying to solve, the proposed approach, and
  any alternatives considered.

---

## Security

Do **not** open a public issue for security vulnerabilities. Report them
privately to the Author via [GitHub](https://github.com/Taha-Khurram) and allow
reasonable time for a fix before any disclosure. Never include live credentials
in reports.
