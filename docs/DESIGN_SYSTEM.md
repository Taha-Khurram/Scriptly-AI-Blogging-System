# Scriptly Design System

A Gemini-inspired visual language for the dashboard: calm, spacious, low-density,
with a single gradient accent used sparingly. Everything below lives in
[`app/static/css/dashboard.css`](../app/static/css/dashboard.css) as CSS custom
properties; page stylesheets in `app/static/css/pages/` consume the tokens and
never hardcode a colour.

This is a **restyle only**. No screen gained or lost a feature, a control, or a
route — the token layer replaced the old palette underneath the existing markup.

---

## 1. Tokens

### Type

Google Sans where the platform ships it (Chrome / Android / ChromeOS), Inter as
the substitute everywhere else. Hierarchy comes from **size and colour**, not
weight — the scale tops out at 600 and body emphasis is 500.

| Token | Value | Use |
| --- | --- | --- |
| `--font-sans` | `'Google Sans', 'Google Sans Text', 'Inter', …` | everything |
| `--font-mono` | `'Roboto Mono', ui-monospace, …` | code, slugs, raw HTML |
| `--fs-display` | 2.75rem / 44px | empty-state prompt headline |
| `--fs-h1` | 1.75rem / 28px | page title |
| `--fs-h2` | 1.375rem / 22px | card / section title |
| `--fs-h3` | 1.125rem / 18px | sub-section |
| `--fs-body` | 0.9375rem / 15px | body copy |
| `--fs-sm` | 0.875rem / 14px | nav, buttons, table cells |
| `--fs-xs` | 0.75rem / 12px | meta, hints |
| `--fs-2xs` | 0.6875rem / 11px | pills, eyebrow labels |
| `--lh-tight` · `--lh-body` · `--lh-relaxed` | 1.25 · 1.6 · 1.75 | headings · UI · prose |
| `--fw-regular` · `--fw-medium` · `--fw-strong` | 400 · 500 · 600 | body · emphasis · ceiling |

### Spacing — 4px base

`--sp-1` 4 · `--sp-2` 8 · `--sp-3` 12 · `--sp-4` 16 · `--sp-5` 20 · `--sp-6` 24 ·
`--sp-8` 32 · `--sp-10` 40 · `--sp-12` 48 · `--sp-16` 64

Density is low on purpose: cards pad at `--sp-6`, the canvas at `--sp-8`/`--sp-10`.

### Radius

`--radius-xs` 8 · `--radius-sm` 12 · `--radius-md` 16 · `--radius-lg` 20 ·
`--radius-xl` 28 · `--radius-pill` 999px

Buttons, chips, status badges and nav items are pills. Cards, inputs and modals
sit at 12–20px. Nothing in the dashboard is square.

### Elevation

Depth is shadow and blur, never a hard stroke.

| Token | Use |
| --- | --- |
| `--elev-0` | flush surfaces |
| `--elev-1` | resting cards, tables, stat tiles |
| `--elev-2` | hover / raised, focused prompt entry |
| `--elev-3` | modals, dropdowns, toasts, expanded nav overlay |
| `--elev-inset` | 1px inset hairline where a border is unavoidable |

### Colour — light

| Token | Value | Role |
| --- | --- | --- |
| `--bg-canvas` | `#F0F4F9` | app background |
| `--surface-1` | `#FFFFFF` | cards, modals, menus |
| `--surface-2` | `#F0F4F9` | sunken panels, row hover, tonal buttons |
| `--surface-3` | `#E9EEF6` | nav hover, chips, code blocks |
| `--surface-4` | `#DDE3EA` | pressed / disabled fills |
| `--nav-bg` | `#F0F4F9` | left rail |
| `--inverse-surface` / `--on-inverse` | `#303030` / `#F2F2F2` | floating toasts, tooltips |
| `--scrim` | `rgba(32,33,36,.45)` | modal / overlay backdrop |
| `--text-strong` | `#1F1F1F` | headings, values |
| `--text-1` | `#202124` | body |
| `--text-2` | `#444746` | secondary |
| `--text-muted` | `#5F6368` | labels, meta |
| `--text-faint` | `#80868B` | placeholders, empty states |
| `--border-subtle` / `--border` / `--border-strong` | `#E8EAED` / `#DADCE0` / `#C4C7C5` | dividers → inputs → emphasis |
| `--accent` | `#0B57D0` | primary actions only |
| `--accent-hover` | `#0842A0` | hover / pressed |
| `--accent-soft` / `--accent-on-soft` | `#D3E3FD` / `#041E49` | active nav pill, selected chips |
| `--accent-tint` | `rgba(11,87,208,.08)` | icon medallions, subtle fills |
| `--focus-ring` | `rgba(11,87,208,.28)` | 3px focus halo |
| `--brand-gradient` | `#4285F4 → #9B72CB → #D96570` | branding + agent-working only |

Status colours come in a triplet — solid, `-soft` fill, `-border`:
`--success` `#1E8E3E`, `--warning` `#B06000`, `--danger` `#D93025`, `--info` `#1A73E8`.

### Colour — dark

Deep charcoal, never pure black. Accent inverts to a light tint with dark text on
top, the Material/Gemini convention.

`--bg-canvas` `#131314` · `--surface-1` `#1E1F20` · `--surface-2` `#282A2C` ·
`--surface-3` `#333537` · `--surface-4` `#3C4043` · `--nav-bg` `#1B1C1D` ·
`--text-strong` `#E3E3E3` · `--text-2` `#C4C7C5` · `--text-muted` `#9AA0A6` ·
`--border` `#3C4043` · `--accent` `#A8C7FA` (text on it: `#062E6F`) ·
`--accent-soft` `#004A77` / `--accent-on-soft` `#C2E7FF`.

Dark mode follows the OS (`prefers-color-scheme`); `data-theme="dark"` /
`data-theme="light"` on `<html>` overrides it in either direction.

**The switch.** Every screen carries one. On dashboard screens it is an icon
button in the page header, with the account menu carrying it as well — which is
what reaches the screens that have not adopted the header component yet, now
that the nav rail is navigation only. Auth and the error page put it top-right
of the form pane. The choice is
stored in `localStorage` under `scriptly-theme` and re-applied by an inline
script in `base.html` *before first paint*, so the canvas never flashes the
wrong surface. No stored value means "follow the OS", and while in that state
the app tracks OS changes live. `app.js` owns `setTheme()` / `toggleTheme()` /
`getActiveTheme()`, keeps every `[data-theme-toggle]` control in sync (icon,
`aria-pressed`, label) and repoints `<meta name="theme-color">` at the active
canvas. The icon always shows the theme you would switch *to*.

### Canvas geometry

`--canvas-pad-x` / `--canvas-pad-y` publish the inset `.dashboard-main` uses
(40/32 → 24/24 below 1210px → 16/16 below 768px). They exist so a full-bleed
element *inside* the canvas — the sticky page header — can cancel the inset with
a negative margin and stay edge-to-edge at every breakpoint without restating
the media queries. `--canvas-glass` is the canvas colour at 0.78 alpha, for the
header's blurred backdrop; no surface token carries alpha.

### Colour — data-viz

Charts do not reach into the status palette. Status colours mean *state* and are
reserved for badges, where they ride a label; borrowing them for chart marks
also fails on contrast — the system's `--success` `#1E8E3E` and `--warning`
`#B06000` separate by only **ΔE 5** under deuteranopia, which is unreadable as
two touching segments of one bar.

The dashboard's pipeline is an *ordered* scale (Draft → In review → Published),
so it takes an **ordinal ramp**: one hue, monotone lightness.

| Token | Light | Dark |
| --- | --- | --- |
| `--viz-stage-1` | `#7CACF8` | `#3B6FBF` |
| `--viz-stage-2` | `#1B6EF3` | `#6BA0F5` |
| `--viz-stage-3` | `#0842A0` | `#A8C7FA` |
| `--viz-stage-other` | `#C4C7C5` | `#5F6368` |

Both sets pass the ordinal checks (monotone lightness, adjacent ΔL ≥ 0.06, light
end ≥ 2:1 against that mode's surface) — dark is re-stepped so the *far* stage is
the brightest against the dark canvas, not flipped. `--viz-stage-other` sits
outside the ramp on purpose: it is the remainder that belongs to no stage.

Separation between segments is a **2px gap in the surface colour**, never a
stroke, and every segment is direct-labelled in the legend below the bar, so
identity never rests on hue alone.

### Motion

`--ease-standard` `cubic-bezier(.2,0,0,1)` · `--ease-emphasized` `cubic-bezier(.3,0,0,1)` ·
`--dur-fast` 150ms · `--dur-base` 250ms · `--dur-slow` 400ms.

Gentle hovers, fade-and-rise page entry, and one signature: `brandShimmer` sweeps
the brand gradient across whatever is currently working (`#nav-progress`, the
indeterminate `.progress-bar`).

`prefers-reduced-motion: reduce` collapses every animation to ~0.

### Legacy aliases

The previous token names still resolve, so page CSS written against them
re-themes for free: `--primary-color` → `--accent`, `--secondary-color` →
`--text-muted`, `--bg-body` → `--bg-canvas`, `--bg-surface` → `--surface-1`,
`--info-color` → `--text-strong`, `--shadow-sm`/`--shadow-card` → `--elev-1`,
`--shadow-hover` → `--elev-2`, plus `--radius-sm|md|lg|xl` and
`--transition-base`. `--text-primary` / `--text-secondary`, which page CSS
referenced but nothing defined, now resolve too.

### Bootstrap bridge

Bootstrap 5.3 is loaded from CDN, so `--bs-*` variables are mapped onto these
tokens (body, borders, radii, modal, dropdown, card, table, buttons, form
controls), and the utilities that ship hardcoded colours — `.bg-white`,
`.bg-light`, `.text-dark`, `.text-muted`, `.text-secondary`, `.border*` — are
overridden. Templates keep their Bootstrap classes and inherit the system.

---

## 2. Screens

Mapped onto the app as it exists. Each row is the real template and the
components it now composes from.

### Home / dashboard — `home.html`

The rail is the resting state — icon-only at 72px, exactly like Gemini's
collapsed sidebar. The panel toggle at the top expands it to a 300px labelled
drawer (brand + toggle header, filled "new" pill, plain nav rows, section label,
user row) which pushes the canvas; below 900px it floats over instead. The choice
is stored in `localStorage` under `scriptly-nav`, restored by a small inline
script in `partials/sidebar.html` before first paint.

Inside the canvas the dashboard is a **bento of four cards under a sticky page
header** — the header is the shared component (below), not page furniture.

```
┌ rail ─┬ canvas ─────────────────────────────────────────────────────┐
│       │ ╔ sticky page header (full-bleed, condenses on scroll) ════╗ │
│       │ ║ eyebrow: greeting                                       ║ │
│       │ ║ H1 Dashboard      [🔍 search  ⌘K]  ( ☾ )  ( ✦ New blog )║ │
│       │ ╚═════════════════════════════════════════════════════════╝ │
│       │ ┌ hero (7fr) ─────────────────┐ ┌ pipeline (5fr) ─────────┐ │
│       │ │ ✦ eyebrow pill              │ │ Content pipeline    all→│ │
│       │ │ Headline with a             │ │ 24 pieces   ← hero fig  │ │
│       │ │ gradient clause.            │ │                         │ │
│       │ │ lede                        │ │ ▓▓▓▐██▐████████▐░░  bar │ │
│       │ │ ( primary ) ( ghost → )     │ │ ● Draft      5     21%  │ │
│       │ │ ──────────────────────      │ │ ● In review  3     12%  │ │
│       │ │ ( ⧗ n awaiting ) ( ✎ n )    │ │ ● Published 12     50%  │ │
│       │ └─────────────────────────────┘ └─────────────────────────┘ │
│       │ ┌ recent work (8fr) ──────────┐ ┌ jump back in (4fr) ─────┐ │
│       │ │ Recent work  (All)(rev)(pub)│ │ ┌──────┐ ┌──────┐       │ │
│       │ │ ▣ title · cat · when   pill │ │ │ tile │ │ tile │  …    │ │
│       │ │ …                           │ │ └──────┘ └──────┘       │ │
│       │ │ View all blogs →            │ │                         │ │
│       │ └─────────────────────────────┘ └─────────────────────────┘ │
└───────┴─────────────────────────────────────────────────────────────┘
```

Both rows are `align-items: stretch`, and the shorter card absorbs the slack
internally (the pipeline's caption takes `margin-bottom: auto`, the work card's
"view all" takes `margin-top: auto`) so a row always ends on one line.

**Hero.** `--surface-1` with the brand gradient as an *aura* — three radial stops
bled into the top-right corner over a masked 64px grid — rather than as a
saturated block, plus one gradient clause in the headline. A pointer spotlight
(`--mx` / `--my`, written by `home.js`) tracks the cursor at 10% accent. That is
the screen's whole gradient budget. The nudge strip beneath names only what is
actually waiting: *n awaiting review*, *n drafts open*, or "all caught up".

**Pipeline.** A hero figure (`≥48px`, proportional figures — `tabular-nums` reads
loose at display size) over the ordinal stacked bar and its legend. `total_blogs`
is counted independently of the status buckets, so the template folds any
remainder into a neutral **Other** segment rather than letting the bar
misrepresent the whole. Each segment is an `<a>` to that stage's filtered list —
so it routes through PJAX and gets keyboard activation for free — and the legend
prints every count and share, which is the chart's table view: the hover tooltip
only ever enhances.

**Recent work.** One list behind segmented tabs (All / In review / Published)
replaces the three cramped columns; every row is `▣ monogram · title · category ·
relative time · status pill`, and each tab keeps its own "view all" link and
empty state. Status pills are the one place the reserved status palette is used,
always with a label.

**Search.** The header field filters these rows live across all three panels;
Enter hands the query to All Blogs (`?search=`), which can search the whole
collection rather than the five rows on screen.

### Agent start state — `create_blog.html`

```
              eyebrow: "Ready to create,"
                  name (400 weight, 2rem)
        What's on your  ·  mind today?  ← brand-gradient text
   ╭──────────────────────────────────────────────╮
   │  Describe your blog topic in detail…      (↑)│  ← --surface-2, 28px
   ╰──────────────────────────────────────────────╯
         ( ◌ Starting generation… )   ▓▓▓░░░░░░░
```

The centred prompt entry mirrors Gemini's empty state: soft-filled input with no
hard stroke, lifting to `--surface-1` + `--elev-2` on focus, circular accent
submit. A radial accent glow sits behind at very low opacity.

### Working session — `create_blog.html` → `drafts.html`

Generation progress is the agent-working surface: a pill status card with a
spinner, and an indeterminate `.progress-bar` running `brandShimmer`. The top
`#nav-progress` bar uses the same gradient so "the system is thinking" always
looks the same, wherever it happens.

### Content editor — `drafts.html`, `approval_queue.html`

Modal on `--surface-1` at `--radius-lg` / `--elev-3`. Title input, slug group,
collapsible SEO block, cover-image picker, TOC panel and content preview are all
tonal panels (`--surface-2` inside `--border`), so the editor reads as one
continuous sheet rather than a stack of boxes. Rich-text (TinyMCE) inherits the
same border and radius tokens.

### Content library — `all_blogs.html`, `drafts.html`

```
┌ filter card ─────────────────────────────────────────────┐
│ (All) (Drafts) (Under Review) (Published)   ← pills      │
│ [category ▾]  [🔍 search        ]  [📅 any date]         │
└──────────────────────────────────────────────────────────┘
┌ table card ──────────────────────────────────────────────┐
│ TITLE            AUTHOR      CATEGORY   STATUS   DATE  ⋮ │
│ row (hover → --surface-2)   ◍ avatar   pill    • pill    │
└──────────────────────────────────────────────────────────┘
              ‹  1  2  3  ›   ← pill page buttons
```

Active filter tab and active page button are `--accent` + `--text-on-accent`;
status badges use the status `-soft` fill with the solid colour as text.

### Scheduling / publishing — `schedule.html`, `approval_queue.html`

Stat tiles, then a week navigator (`‹ May 18 – May 24 ›` + `Today` pill) above the
calendar grid. Scheduled events are tonal chips; the pre-publish checklist and
best-time suggestions render as `.best-time-chip` pills inside a `--surface-2`
panel.

### Analytics — `analytics.html`

Card-based stat tiles on the same `--surface-1` / `--elev-1` / medallion pattern
as Home, so the two screens read as one system. Reconnect and setup states are
`--warning-soft` banners with `--warning-border`.

### Account — `profile.html`

Sits inside the standard shell (rail + `.dashboard-main` + eyebrow/H1 header)
rather than as a standalone centred card, so it is reachable from anywhere and
participates in PJAX like every other screen. Two cards: identity (avatar,
change/remove) on the left, account details (display name, read-only email and
role, save) on the right. Collapses to one column below 1100px.

### Sign in / sign up / reset — `login.html`, `signup.html`, `forgot_password.html`

```
┌ brand pane (fixed ink) ────────┬ form pane (--surface-1) ──────┐
│  wordmark                      │                        ( ☾ ) │
│                                │   eyebrow                     │
│  ✦ AI CONTENT STUDIO           │   H1 title                    │
│  Headline with a gradient      │   subtitle                    │
│  clause.                       │                               │
│  lede copy                     │   label                       │
│                                │   ▢ soft-filled field    (👁) │
│  ◌ point · note                │   label                       │
│  ◌ point · note                │   ▢ soft-filled field         │
│  ◌ point · note                │            forgot password?   │
│                                │   ( primary pill )            │
│  ────────────────────          │   ──────── or ────────        │
│  © year · line                 │   ( G  Continue with Google )  │
└────────────────────────────────┴───────────────────────────────┘
```

The brand pane is `#121316` in **both** themes — the one fixed surface in the
product, and the anchor the split reads against. Behind it, three radial stops
of the brand gradient drift on a 26s loop at 0.30 opacity (`authAurora`), over a
64px grid masked to fade downward. It is shared markup:
`partials/auth_brand.html`, with `brand_title` / `brand_lede` set per page.

Below 900px the pane is dropped entirely and a compact wordmark appears on the
card instead.

Fields follow the `.prompt-box` idiom rather than the boxed-input idiom: filled
with `--surface-2`, no resting stroke, `--surface-3` on hover, and on focus they
lift to `--surface-1` with an accent border and a 3px focus ring. Password
fields carry a reveal toggle; signup shows a live requirements checklist that
fills in green as each rule is met (`auth.js`), which replaced the red error
string that used to re-render on every keystroke. Autofill is repainted in the
token surfaces so Chrome's fixed pale yellow cannot break dark mode.

Firebase's raw error codes are mapped to plain sentences and surfaced through
the app's toast system instead of `alert()`.

### Error screens — `errors/404.html`

Extends `base.html`, so it inherits the tokens, fonts, theme switch and toasts.
Wordmark, the code in brand gradient (`inline-block`, so the ramp spans the
glyphs rather than the full column), title, one line of explanation, a primary
and a ghost pill, then quiet "jump to" pills. Signed-out visitors get *Sign in*
in place of the dashboard action and no jump list.

### Light and dark

Every screen above is fully tokenised, so both themes come from the same markup.
The rail, canvas, cards, inputs, tables, modals, menus, toasts, skeletons,
badges, code panels, auth panes, error screens and loaders all swap together.

---

## 3. Components

Reusable pieces, all token-driven:

| Component | Selector | Notes |
| --- | --- | --- |
| Nav icons | `.material-symbols-outlined` | Google Material Symbols, `wght 300 / opsz 24`, self-hosted subset (below) |
| Nav rail | `.dashboard-sidebar` | 72px icon rail at rest; `.expanded` → 300px labelled drawer |
| Brand | `.sidebar-brand` / `.sidebar-lockup` | mark alone in the rail, full lockup in the drawer |
| Panel toggle | `.sidebar-toggle` | collapses the drawer; in the rail the brand does the opening |
| Nav item | `.sidebar-menu a` | 48px circle in the rail, 44px pill in the drawer; active = `--accent-soft` |
| Nav scroll | `.sidebar-menu` | `min-height: 0` + `flex-shrink: 0` rows; thin thumb in the drawer, silent in the rail |
| New action | `.sidebar-menu a[data-page="create"]` | Gemini's persistent "new" action, first in the rail; takes its fill only in the drawer |
| User row | `.sidebar-user-card` | avatar alone in the rail; avatar + name/role + logout in the drawer |
| Page header | `.page-header` | sticky full-bleed bar; see below |
| Header search | `.page-search` | soft-filled pill, ⌘K/Ctrl K hint, clear button |
| Header action | `.page-header-action` / `.page-header-icon-btn` | primary / ghost pill, 44px round icon button |
| Page header (legacy) | `.dashboard-header` / `.header-title` | plain eyebrow + H1, still used by the other screens |
| Stat tile | `.stat-card` / `.stat-icon` | tonal circular medallion |
| Card head | `.card-head` / `.card-title` / `.card-link` | title row + pill "view all" link |
| Stacked bar | `.viz-stack` / `.viz-seg` | ordinal ramp, 2px surface gaps, 4px outer ends |
| Chart legend | `.viz-legend` | swatch · label · count · share — doubles as the table view |
| Segmented tabs | `.work-tabs` / `.work-tab` | active = `--accent-soft`, same as the nav rail's current item |
| Work row | `.work-row` | monogram · title/meta · status pill |
| Action tile | `.action-tile` | 2-up grid; `.is-lead` takes `--accent-soft` |
| List card | `.dashboard-list-card` | header + rows + empty state |
| Status pill | `.list-card-badge`, `.blog-status-badge`, `.status-badge` | `-soft` fill + solid text |
| Filter pill | `.filter-tab` | active = accent |
| Table | `.blogs-container` / `.blog-row` | hairline dividers, `--surface-2` hover |
| Pagination | `.page-btn` | pill buttons |
| Prompt entry | `.prompt-box` / `.prompt-submit` | soft-filled, 28px |
| Toast | `.custom-toast` | `--elev-3`, gradient progress line |
| Skeleton | `.skeleton*` | accent-tinted shimmer |
| Loaders | `#page-loader`, `#nav-progress`, `#action-loader` | brand gradient = working |
| Theme switch | `[data-theme-toggle]` | `.page-header-icon-btn` in the header, `.user-menu-action` in the account menu, `.auth-theme-toggle` on auth / 404 |
| Brand pane | `.auth-brand` | fixed ink surface + `authAurora` + masked grid |
| Auth field | `.auth-field` / `.input-wrapper` / `.auth-input` | soft fill, focus lifts to `--surface-1` |
| Reveal | `.auth-reveal` | `data-reveal="<input id>"`, swaps the eye icon |
| Requirements | `.pw-rules` | `li.met` fills green as each rule passes |
| OAuth button | `.auth-oauth` | outlined pill, official four-colour G |
| Inline error | `.error-message.show` | flex row, Material `error` glyph |
| Error screen | `.error-shell` / `.error-code` / `.error-jump-list` | gradient code, pill actions |

### The page header

`partials/page_header.html` is a Jinja macro, so the actions are the call body:

```jinja
{% from 'partials/page_header.html' import page_header with context %}
{% call page_header(title='Dashboard', eyebrow=greeting, search=true,
                    search_placeholder='Search your content') %}
  <button class="page-header-icon-btn" data-theme-toggle>…</button>
  <a href="…" class="page-header-action is-primary">New blog</a>
{% endcall %}
```

It must be the **first child of `.dashboard-main`**: the sticky offset assumes
the canvas inset, and PJAX only swaps that element's `innerHTML`, so a header
placed outside it would not re-render on navigation.

Two states. Resting: transparent, eyebrow visible, H1 at `--fs-h1`. Stuck (past
12px of scroll): `--canvas-glass` + a 16px backdrop blur, hairline bottom rule,
`--elev-1`, eyebrow collapsed to zero height, H1 down to `--fs-h2`. The
thresholds are asymmetric — condense at 12px, relax at 4px — so a header sitting
exactly on the line cannot flicker as its own height change nudges the scroll.
Opaque `--bg-canvas` is the base and the blur is layered on in an `@supports`
block, because a 0.78-alpha fill with no blur behind it lets content scroll
through legibly-but-wrong.

Behaviour lives in the `initPageHeader` IIFE in `app.js`, bound once and
delegated off `document`/`window` — the header itself is destroyed and rebuilt by
every PJAX navigation, so nothing may hold a reference to it.

The search field owns no behaviour of its own. It emits `page-search` on
`document` with `{ value, submit }` and the page decides what that means;
⌘K/Ctrl+K focuses it from anywhere, a bare `/` focuses it when the reader is not
already typing, Escape clears then blurs, and the hint relabels itself to `⌘ K`
on Apple platforms.

### The brand assets

Two files, both **black-on-transparent** so one asset serves both themes — ink
on the light rail, inverted to white on the dark one:

| File | Size | Used by |
| --- | --- | --- |
| `images/logo-mark.png` | 108×96 | the collapsed rail |
| `images/logo-lockup.png` | 418×96 | the drawer, auth screens, 404 |

Both are derived from `images/site/logo1.png`. That source is a *screenshot of a
transparent-background preview* — its alpha is 255 everywhere and the
checkerboard is baked into the pixels — so it cannot be used directly without
painting a grey checkerboard behind the logo. The artwork was separated from the
background by what it is (saturated nib, dark wordmark) rather than by alpha,
and the drop shadow dropped with it. If the brand art changes, redo that
extraction rather than pointing the templates at the raw file.

In the rail the mark **is** the expand control: hover or keyboard focus
cross-fades it to `left_panel_open`, so 72px of width carries both the branding
and the affordance. The drawer shows the lockup with `left_panel_close`
opposite it.

### The icon font

Material Symbols is **self-hosted**, not loaded from `fonts.gstatic.com`: the
`@font-face` sits at the top of `dashboard.css` and the file is
`app/static/fonts/material-symbols-outlined.woff2`, preloaded in `base.html`.

This is not a performance preference. The icons are ligatures, so a font that
fails to arrive doesn't degrade to blank — every icon renders as its own name
(`edit_square`, `left_panel_close`) sprayed across the rail. And because sidebar
navigation is PJAX, nothing reloads, so one failed CDN request stayed broken
until a hard refresh. Serving it ourselves removes the failure mode entirely;
verified by rendering with both Google font hosts blackholed.

The file is subset to the ~29 glyphs actually used — 5KB, against 4MB for the
full variable font. The cost is that **an icon added to a template is not in the
font until the subset is rebuilt**:

```
python scripts/update_icon_font.py           # rescan, refetch, rewrite
python scripts/update_icon_font.py --check   # fail if an icon is missing
```

The script scans templates, JS and CSS for ligature names and validates them
against Google's published codepoints list before building — necessary because
the Fonts API answers `200` with an *empty font* for a name that doesn't exist,
so a typo would otherwise ship silently. `--check` is the CI form.
`material-symbols-outlined.txt` next to the font records what went in.

---

## 4. Conventions

- **Never hardcode a colour in page CSS.** Use a token; add one here if it is
  genuinely missing.
- **`--surface-2` is the canvas colour in light mode.** A `--surface-2` fill
  placed directly on `--bg-canvas` is invisible in light and fine in dark — the
  easiest way to ship a half-broken screen. Either raise the container to
  `--surface-1` (what the auth form pane does) or use `--surface-3` for the fill
  (what the error screen's pills do). Check both themes before calling it done.
- **The gradient is rationed.** Branding, the hero banner, gradient headline text,
  and agent-working indicators. Nothing else.
- **Status colour is for state, never for a chart series.** A badge that says
  "Published" in green is state. A bar segment coloured green because it happens
  to be the third one is not — and the system's `--success`/`--warning` pair is
  ΔE 5 apart under deuteranopia, so as two touching segments it is unreadable.
  Ordered stages take the `--viz-stage-*` ordinal ramp; identity is carried by a
  direct label either way.
- **An animation must not be able to strand a wrong value.** The dashboard's
  count-up rewrites a number the server already rendered correctly, so it skips
  entirely when the tab is hidden (`requestAnimationFrame` does not run there and
  the figure would sit at 0) and carries a timeout that snaps to the true value
  if the frame loop is throttled to a crawl. Same principle for the pipeline bar:
  the CSS renders the real widths and JS zeroes them for one frame to start the
  transition, so a page without JS still shows true proportions.
- **Weight ≤ 600.** Reach for size, colour or spacing before bold.
- **Elevation over borders.** A 1px `--border-subtle` divider is fine; a heavy
  stroke is not.
- **New surfaces need a dark value.** If a colour only works in one theme it is
  not finished.
- **A fixed-height row inside a flex column needs `flex-shrink: 0`.** Otherwise
  the browser silently compresses the rows to fit instead of scrolling — the
  nav rail lost 18px per item on short viewports this way, and no scrollbar ever
  appeared to explain it. The scroll container needs `min-height: 0` to match.
  Test the rail at ~600px of viewport height, where all 16 admin items are in
  play.
- **Theme rules set colour, never state.** A `:root[data-theme="dark"] .x`
  selector weighs the same (0,3,0) as a `.parent:hover .x` one and sits later in
  the file, so any property a theme rule declares silently wins over that
  component's own hover/focus/active states. A stray `opacity` in the dark-theme
  brand rule pinned the rail's mark visible and killed its hover swap entirely.
  Keep `filter`/`color` in theme rules and leave `opacity`, `transform` and
  `visibility` to the component.
- **Verify hover states without `!important`.** Forcing a state with overrides
  proves the *styling* works while hiding the cascade conflict that stops it
  firing — which is how the bug above shipped. Rewrite `:hover` to a class of
  equal specificity instead, so source order and weight stay honest.
- **`gap` still applies to a zero-width flex item.** The rail hides nav labels
  with `width: 0` so they can animate open, which leaves them in flow — so a
  `gap` on the row is drawn beside nothing and shoves the icon off the rail's
  axis by half the gap. Centre icon-only states with `gap: 0` and restore the
  gap in the labelled state. Backgrounds are unaffected, so the active pill goes
  on looking centred while every glyph inside it is not: measure, don't squint.
