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

### The stat tile

A tile is `label · value · delta · trend`, in that reading order. A medallion
and a bare figure is only half a tile: it says *how many* and leaves the reader
to guess whether that number is good, and on a wide card it leaves two thirds of
the width empty. The delta answers "compared to what", the trend answers "going
which way".

- **The value takes proportional figures, never `tabular-nums`.** Equal-width
  digits give every glyph the width of a `0`, so `121` reads loose at display
  size. Tabular is for columns that align vertically — table rows, axis ticks.
- **Large values compact**: `1,284` → `1.3K` → `4.2M`, with the exact figure kept
  on the element's `title` (which is also where JS reads it back from, since the
  compact form is unparseable).
- **The sparkline is one series in one hue.** The current period is marked by
  *form* — a dot with a 2px surface ring — not by a second colour, so nothing
  depends on telling two blues apart. `--accent` carries it in both themes
  (6.4:1 on the light surface, 9.6:1 on the dark); `--viz-stage-1` was rejected
  for this because it lands at 2.3:1 on white.
- **A flat all-zero series sits on the baseline**, not halfway up the box — "nothing
  happened" must not read as "steady at some level".
- **Padding clears the marker, not the line.** The endpoint dot reaches 4.5px past
  its centre, so the plot insets by 6px; at 4px the last dot was clipped by the
  viewBox.
- **The trend has a text equivalent.** `role="img"` plus an `aria-label` naming
  the total, the range and the latest value — a micro-chart has no room for axes,
  and a tooltip may never be the only way to reach a value.
- Where the third column is a **ratio rather than a series** it takes a `.stat-meter`
  instead, whose unfilled track is a lighter step of the same hue.
- Direction is carried by an **arrow glyph and words** as well as by colour, so the
  reserved status hues never mean anything on their own.

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

Both sit under the same sticky page header as the dashboard and compose from
the §12 listing primitives, so a row reads identically on all three screens.
Neither is a table any more: the old column headers and flex cells are gone in
favour of the dashboard's row idiom, which degrades to narrow widths by
dropping columns instead of scrolling sideways.

```
╔ page header ═══════════════════════════════════════════════════╗
║ Content management                                             ║
║ H1 All Blogs          [🔍 search ⌘K]   ( ☾ )   ( ✦ New blog )  ║
╚════════════════════════════════════════════════════════════════╝
┌ filter bar ────────────────────────────────────────────────────┐
│ (All)(Draft)(Under review)(Published)   [⛁ Category ▾] [📅 ▾]  │
└────────────────────────────────────────────────────────────────┘
   …once applied, each pill states its own value:
┌────────────────────────────────────────────────────────────────┐
│ (All)(Draft)(•Published•)      [⛁ Growth ▾] [📅 Jul 1 – Aug 15]│
│                                                  × Clear all   │
└────────────────────────────────────────────────────────────────┘
┌ listing card ──────────────────────────────────────────────────┐
│ Blogs (25)                                        Page 1 of 3  │
│ ▣  Title                          ● Published    yesterday   ⋮ │
│    author · category                                           │
│ …                                                              │
│                       ‹ 1 2 3 ›                                │
└────────────────────────────────────────────────────────────────┘
```

**The filter bar.** Status is the primary dimension, so it stays a permanently
visible segmented control. Everything else is a **filter pill** — a control that
renders its own current value, so the applied query is readable without opening
anything: resting it says `Category`, applied it says `Growth` and takes
`--accent-soft`, the same "selected" treatment the nav rail and the status tabs
use. `Clear all` exists only while something is applied.

The category pill opens a **listbox we own** (`.select-pill` + `.menu`), not a
native select popup — the browser draws that one itself, so it cannot take the
product's surfaces, radius or type, and on a forced dark theme over a light OS
it renders white-on-blue against the dark canvas. The native `<select>` stays in
the DOM as the value holder: choosing an item writes through to it and fires a
real `change` event, so `all_blogs.js` keeps reading `.value` and listening for
`change` exactly as it did. Behaviour (open, dismiss, ⌘-less arrow/Home/End
navigation, focus return) is the `initSelectPill` module in `app.js`.

**The date range modal** is a presets rail beside a calendar — the arrangement
every analytics tool converged on, because the two are alternatives rather than
steps: most sessions end at the rail and never touch the grid.

```
┌ Date range ─────────────────────────────────── × ┐
│ Today        │  ‹      August 2026      ›        │
│ Last 7 days  │  Su Mo Tu We Th Fr Sa             │
│ ▸Last 30 days│  ░░ ░░ ░░ ░░ ░░ ░░  1             │
│ Last 90 days │   2  3  4  5  6  7  8             │
│ This year    │   9 10 11 12 13 14 (15)           │
│ All time     │  16 17 …                          │
├──────────────┴───────────────────────────────────┤
│ Showing Jul 16 – Aug 15, 2026    [Clear] [Apply] │
└──────────────────────────────────────────────────┘
```

The rail is a fixed 176px so "Last 90 days" never wraps and the grid keeps a
stable width as the month changes; below 560px it becomes a row of chips above
the calendar. The preset matching the current selection is highlighted, so
reopening shows the range in force rather than six identical rows, and the
footer's summary is the label for what Apply is about to do.

The calendar is ours, not `<input type="date">`. That control brings the
browser's own popup — unstylable, and single-date only, so a *range* meant two
disconnected pickers. Two hidden inputs (`#dateFrom` / `#dateTo`) remain as the
value holders the rest of the JS reads; the grid writes to them.

- Two clicks: the first opens the range, the second closes it. Picking a second
  date **before** the first swaps the endpoints rather than rejecting the click.
- While a range is half-open, Apply is disabled and the summary says *Pick the
  end of the range* — a half range would otherwise submit as "everything since".
- Hovering previews the band; arrow keys move day by day and follow the focus
  into the next month.
- Six full weeks always render, so the modal never changes height mid-month.
- **Building and painting are separate.** Hovering repaints the band constantly,
  and rebuilding the grid's `innerHTML` that often destroys the very button the
  pointer is over — losing `:hover`, losing focus, flickering. `buildCalendar()`
  runs on a month change; `paintCalendar()` only rewrites class names.
- The band is drawn on the **cell** and the endpoints on the **button** inside
  it. That is what lets the highlight run continuously across a week while the
  ends stay circular.
- Dates are formatted with a local `isoLocal()`, never `toISOString()` — the
  latter converts to UTC first, so anywhere behind UTC "today" comes back as
  yesterday for most of the day.

Both listings put the same pair in the card head — a `.card-count` of matching
records and a `.card-note` of `Page n of m` — and both render **relative**
timestamps (`yesterday`, `2 weeks ago`) with the absolute date kept on the
element's `title`, so nothing is lost at the widths where that column is hidden.

**All Blogs.** The header's search field carries `id="searchInput"` — the id
`all_blogs.js` already binds its debounced query to — so the page has one search
box rather than a header one and a filter-bar one that mean different things.
It queries the API, not the rendered rows. The monogram is the *author's*
initial, which is the column the old table spent 150px on. Rows are rendered
both server-side (first page) and by `renderBlogRow` (every filter and page
after), and the two must stay identical.

**Drafts.** Same shell, with the row's trailing area carrying two hover-revealed
shortcuts (preview, submit) beside the ⋮ menu — the menu still lists every
action, so nothing depends on hover, and the shortcuts are hidden below 700px
where the row has no room for them. The header's search filters the rendered
rows client-side, since a draft list is one page of ten.

Pagination on drafts is **new**: `/drafts` had been paginating server-side since
it was written but rendered no controls, so page 2 onward was unreachable.

### Categories — `categories.html`

The same shell and the same rows: a category is one more record, with an
article count where a blog carries a status. The old four-column table
(Name / Usage Stats / **Status** / Actions) is gone — that status column said
"Active" on every row and encoded nothing.

Three things were broken rather than merely dated, and are fixed:

- **The modals and the page script sat outside `.dashboard-main`.** PJAX swaps
  only that element's innerHTML, so arriving from anywhere in the app dropped
  them entirely and Rename / Delete / View blogs failed until a hard reload.
  They now live inside it, like every other screen's.
- **There was no way to create a category.** The header button had been
  commented out, though `POST /api/categories` and the Add modal both worked.
  It is back as the header's primary action.
- **`categories.js` opened with `new bootstrap.Dropdown(el)`.** Bootstrap loads
  with `defer`, so on a hard page load it was not defined yet — the constructor
  threw and took the rest of the file with it, leaving the form handlers and
  the search unbound. (It only appeared to work when *arriving by PJAX*, which
  injects the script after Bootstrap is ready.) The loop was never needed:
  Bootstrap's data-api is delegated off document.

The search also stopped writing `display: flex !important` inline — that fought
the row's own grid and could not be undone by CSS — and now toggles `hidden`
through the header's `page-search` event like the other listings.

### Media library — `gallery.html`

The same shell again — sticky page header, filter bar, listing card — because a
stored image is one more record. What is different is that a record you pick by
*looking* needs a grid, so the card holds tiles rather than rows, with a list
view behind a toggle for when the question is "which one is 4MB".

```
╔ page header ═══════════════════════════════════════════════════╗
║ Media library                                                  ║
║ H1 Gallery         [🔍 search ⌘K]    ( ☾ )    ( ⬆ Upload )     ║
╚════════════════════════════════════════════════════════════════╝
┌ toolbar ───────────────────────────────────────────────────────┐
│ (All 128)(JPG 71)(PNG 46)(WebP 11)      [⇅ Newest ▾] [▦|☰]     │
└────────────────────────────────────────────────────────────────┘
   …and while anything is selected, the same card becomes:
┌────────────────────────────────────────────────────────────────┐
│ (×) 3 selected          [Select page] [Copy URLs] [🗑 Delete]  │
└────────────────────────────────────────────────────────────────┘
┌ library card ──────────────────────────────────────────────────┐
│ Images (128)                              Page 1 of 6 · 34.2 MB│
│  ▢ ▢ ▢ ▢ ▢ ▢     ← square thumb, name + weight always visible  │
│  ▢ ▢ ▢ ▢ ▢ ▢                                                   │
│                    ‹ 1 2 3 … 6 ›                               │
└────────────────────────────────────────────────────────────────┘
```

**Upload is an action, not a landing strip.** The old screen kept a dashed
dropzone pinned above the grid at all times — ~200px of the first screen spent
on a control that matters for the few seconds an upload starts. It is now the
header's primary action, plus a drag-anywhere overlay that appears only while
files are actually over the window, plus the empty state's CTA. `dragenter` and
`dragleave` fire for every child element the pointer crosses, so the overlay is
driven by a depth counter (enters minus leaves) and reset on `drop` and on
window blur — a drag that ends outside the window cannot strand it on screen.

**The upload tray** is docked bottom-right with one row per file: its own
thumbnail (an object URL, revoked when the row finishes), its own progress from
`xhr.upload.onprogress`, and its own outcome. The single anonymous bar it
replaces could not say *which* of five files had failed. Files are validated in
the browser before the request — a 12MB photo used to upload in full and only
then be refused — and three upload at a time, because strictly sequential left
the connection idle between files.

**Selection** is Photos-style rather than a mode: with nothing selected a click
opens the preview, once anything is selected a click toggles. ⌘/Ctrl-click
toggles regardless, Shift-click takes the range from the anchor, `⌘A` takes the
page, `Delete` opens the confirm and `Escape` clears. Bulk delete is one request
(`POST /api/gallery/images/bulk-delete`), not one per image, and anything the
caller does not own is skipped and reported rather than aborting the batch.

**The tile** is transparent at rest, fills with `--surface-2` on hover and
`--accent-tint` + an accent border when selected — the Drive/Photos idiom. A
permanent `--surface-2` block behind every tile put a second card inside the
card, which against `--surface-1` in dark mode is a 4% step that reads as grime
rather than structure. Filename and weight sit *below* the thumbnail and are
always visible; the checkbox and copy button ride a top-down scrim inside the
frame, because they sit over an arbitrary photograph and neither can be relied
on to land on something dark.

Every overlay control lives inside `.media-frame`, the square that holds the
thumbnail. The opener used to be `inset: 0` with a hand-tuned `bottom: 44px` to
clear the caption — a number that silently broke the hit area whenever the
caption's height changed.

**The checked mark is a real tick**: two adjacent borders of a box, rotated 45°,
taking their colour from `currentColor`. It was briefly two full-length diagonal
gradients, which is an ✗ rather than a ✓ — a checkbox drawn as a cross reads as
"rejected" on a control that means "chosen". A styled `appearance: none` input
cannot hold the tick as a child and pseudo-elements on replaced elements are not
guaranteed to render, so the input sits transparent on top as the control and a
sibling `.media-check-box` span is the visual.

**The preview** is the image on a checkerboard stage beside its facts, with
`‹ ›` and arrow keys walking the library without closing. Dimensions are read
off the loaded element's `naturalWidth`/`naturalHeight` — nothing in the upload
pipeline stores them, and adding an imaging dependency to learn two integers is
not worth it when the browser has already decoded the file. Copy comes in three
formats (URL, Markdown, HTML) and all three copy the **absolute** URL: the
stored value is a site-relative `/static/…` path, which pastes as a broken
image anywhere off-origin — the old copy button handed that path over as-is.

Four things were broken rather than merely dated, and are fixed:

- **Ownership was checked after the delete.** `delete_gallery_image` removed the
  document and *then* returned it for the route to compare `user_id` — so any
  signed-in account could destroy another account's image metadata and be told
  "403" once it was already gone. `get_gallery_image` now answers that question
  first.
- **Every page number was rendered.** `renderPagination` looped `1..totalPages`,
  so a 40-page library drew 40 buttons across the card. It takes the `.pager`
  window the listings use — never more than five buttons.
- **There was no search, filter or sort**, on the one screen in the product
  whose records have no titles to scan. Search, the type facet and the sort all
  run server-side over the whole collection, so they reach past the 24 tiles on
  screen, and all three live in the query string so a filtered library survives
  a reload and can be linked to.
- **Deleting the last item on a page stranded you** on an empty grid. The data
  layer clamps an out-of-range page to the last real one and reports which page
  it actually returned.

The size string is rendered twice — by Jinja for the first paint and by
`formatBytes()` for every re-render — and the two must agree. They did not:
Jinja's `round` is Python's, which rounds half to *even*, while `Math.round`
rounds half *up*, so a 248,320-byte file painted as "242 KB" and silently became
"243 KB" on the first client render. The template uses `(x + 0.5) | int`, which
is exactly `Math.round` for positive values.

### Newsletter — `newsletter.html`

Three jobs behind one set of segmented tabs — **Compose · Subscribers ·
Archive** — under the standard shell. The screen used to stack all three down
one scrolling page, so the archive sat below a composer you were not using and
the send button sat below that.

```
╔ page header ═══════════════════════════════════════════════════╗
║ Email marketing                                                ║
║ H1 Newsletter                                       ( ☾ )      ║
╚════════════════════════════════════════════════════════════════╝
┌ 👥 128 Subscribers ┬ 📤 12 Issues sent ┬ 📄 34 Published posts ┐
└────────────────────┴───────────────────┴───────────────────────┘
┌ (Compose) (Subscribers 128) (Archive 12) ──────────────────────┐
└────────────────────────────────────────────────────────────────┘
┌ compose ───────────────────────────────────────────────────────┐
│ Subject line          │ ▣ Live preview      [🖥|📱]            │
│ ▢                     │ ┌────────────────────────────┐         │
│ Introduction          │ │  rendered email, on paper  │         │
│ ▢▢▢                   │ │  white in both themes      │         │
│ Post summaries        │ │                            │         │
│ ① Title  ▢▢           │ └────────────────────────────┘         │
│ ② Title  ▢▢           │                                        │
├────────────────────────────────────────────────────────────────┤
│ 👥 Goes to 128 subscribers   [Discard] [Send test] [Send ✈]    │  ← sticky
└────────────────────────────────────────────────────────────────┘
```

**Edit and preview are side by side.** The preview was a separate card *further
down the page*, reached by "Continue to Preview" and left by "Back to Edit", so
checking a wording change cost two clicks and two scrolls. It is now always
there, re-rendered 400ms after you stop typing. Each render carries a sequence
number and a stale response is dropped — a slow earlier render must never
overwrite a newer one. A desktop/phone toggle sits above it, because most
newsletters are opened on a phone.

The preview iframes are `sandbox=""` — no scripts, no same-origin. `html_content`
is replayed from storage in the archive viewer, and an email body is not a
thing to execute.

**Sending is the dangerous act, so it is the guarded one.** Previously one click
on a button labelled "Send" delivered to every subscriber, with no confirmation
and no undo. Now:

- **Send test** was already supported by the API (`test_mode` / `test_email`) but
  its markup was *commented out in the template*, so the safest step in the flow
  was unreachable. It is back, prefilled with the signed-in address.
- **Send** opens a confirmation that states the audience as a figure and repeats
  it in the button's own label — `Send to 128 subscribers`, not `Send`.
- Both send controls are **disabled while the mail transport is unconfigured**.
  The old screen left them live and let the request fail.

**Compose opens on a prompt, not a form.** The starting state borrows
`create_blog.html`'s idiom — one question, one field, one action — because the
first act here is also "ask the agent for something". With nothing published it
says so up front instead of letting Generate fail.

Four things were broken rather than merely dated, and are fixed:

- **The modals sat outside `.dashboard-main`.** PJAX swaps only that element's
  innerHTML, so View and Delete in the archive were dropped on every in-app
  navigation and worked only after a hard reload — the same fault Categories
  had. (The *script* survived only by accident: PJAX scrapes `<script>` tags
  from the whole document, not just the swapped region.)
- **Nothing was escaped.** `${sub.email}`, `${item.subject}` and `${post.title}`
  went into markup raw, and a summary containing `</textarea>` closed the field
  it was being written into. Subscriber addresses and blog titles are
  user-supplied. Values are now escaped for attribute context, and the summary
  is assigned as `.value` rather than interpolated.
- **650 lines of `<style>` and 415 of `<script>` lived in the template** — the
  only screen in the app with no page CSS/JS files. Uncacheable, re-parsed on
  every visit, and PJAX had to re-inject the whole style block into `<head>`
  each time. Both are files now.
- **The page declared ~20 globals**, including `closeDeleteModal` and
  `showDeleteConfirm`, which `leads.js` also declares. The two only avoided
  colliding by never being on screen together. It is one scoped IIFE now.

Subscribers gained search and CSV export (built in the browser from data already
fetched — no new endpoint). Cells beginning `=`, `+`, `-` or `@` are quoted so a
subscriber cannot make a spreadsheet execute their address as a formula, and the
file carries a BOM so Excel reads UTF-8 addresses correctly.

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
| Stat tile | `.stat-card` / `.stat-icon` / `.stat-label` / `.stat-count` / `.stat-delta` / `.stat-trend` / `.stat-meter` | the full label · value · delta · trend contract; in dashboard.css §12 since six screens use it — only the medallion's *colour* stays with the page |
| Setup banner | `.setup-banner` | `--warning-soft` on `--warning-border`; a blocked screen explaining itself |
| Device toggle | `.device-toggle` / `.device-btn` | desktop ↔ phone preview width |
| Email preview | `.preview-stage` / `.preview-device` | sandboxed iframe on `--email-paper`, which does not invert |
| Send bar | `.send-bar` | sticky footer stating the audience beside the irreversible action |
| Card head | `.card-head` / `.card-title` / `.card-link` | title row + pill "view all" link |
| Stacked bar | `.viz-stack` / `.viz-seg` | ordinal ramp, 2px surface gaps, 4px outer ends |
| Chart legend | `.viz-legend` | swatch · label · count · share — doubles as the table view |
| Segmented tabs | `.seg-tabs` / `.seg-tab` / `.seg-count` | active = `--accent-soft`, same as the nav rail's current item |
| Card shell | `.surface-card` | `--surface-1` + `--elev-1` + `--radius-lg` |
| Row | `.data-row` / `.row-mark` / `.row-main` / `.row-title` / `.row-meta` | monogram · title/meta · trailing; pages restate only `grid-template-columns` |
| Row opener | `.row-open` | the main column as a button, for rows that open a modal |
| Row quick action | `.row-action` | revealed on row hover/focus; always duplicated in the ⋮ menu |
| Empty state | `.list-empty` / `.list-empty-icon` | medallion + one sentence |
| Pager | `.pager` / `.pager-btn` / `.pager-dots` | pill buttons, `.is-active` = accent |
| Action tile | `.action-tile` | 2-up grid; `.is-lead` takes `--accent-soft` |
| List card | `.dashboard-list-card` | header + rows + empty state |
| Status pill | `.list-card-badge`, `.blog-status-badge`, `.status-badge` | `-soft` fill + solid text |
| Modal shell | `.app-modal` / `-head` / `-body` / `-foot` / `-title` / `-close` / `-note` | Bootstrap's machinery, our chrome |
| Modal field | `.app-field` / `.app-field-hint` | soft fill, focus lifts to `--surface-1` |
| Modal button | `.app-btn` | `.is-primary` / `.is-ghost` / `.is-danger` pills |
| Filter bar | `.filter-bar` / `.filter-bar-controls` | segmented tabs + filter pills on one card; in dashboard.css §12 since the gallery became its second user |
| Media tile | `.media-tile` / `.media-thumb` / `.media-caption` | square thumb on a checkerboard, name + weight always visible |
| Tile selection | `.media-check` / `.media-checkbox` | corner checkbox, drawn tick so it inverts with the accent |
| View toggle | `.view-toggle` / `.view-toggle-btn` | grid ↔ list, remembered in `localStorage` |
| Selection bar | `.gallery-selection-bar` / `.selection-count` | replaces the filters in place while a selection exists |
| Drop overlay | `.drop-overlay` | whole-canvas drop target, shown only mid-drag |
| Upload tray | `.upload-tray` / `.upload-item` | docked queue, one progress bar and one outcome per file |
| Preview | `.preview-stage` / `.preview-rail` / `.preview-facts` | image beside its metadata and copy formats |
| Filter pill | `.filter-pill` / `.filter-pill-value` / `.filter-pill-caret` | states its own value; `.is-active` = `--accent-soft` |
| Select pill | `.select-pill` / `.menu` / `.menu-item` / `.menu-check` | our own listbox wrapped around a real `<select>`; see below |
| Clear filters | `.filter-clear` | ghost text button, present only while something is applied |
| Date range | `.date-modal` / `.date-preset` / `.date-summary` | presets, then calendar, then what Apply will do |
| Range calendar | `.cal` / `.cal-cell` / `.cal-day` | band on the cell, endpoints on the button; `.is-start` / `.is-end` / `.is-in-range` / `.is-preview` |
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
- **Declare `color-scheme`, not just the tokens.** Some controls the browser
  draws itself — the `<input type="date">` calendar, scrollbars, native select
  popups — ignore CSS colours entirely and follow `color-scheme`. Tokenising a
  screen is not enough: forcing `data-theme="dark"` on a light OS leaves those
  widgets white until the theme blocks also set `color-scheme: dark`.
- **`animation-fill-mode: both` on a card is a stacking-context trap.** Keeping
  the final keyframe applied keeps its `transform` applied, which makes the card
  a stacking context forever — and a dropdown opened inside it then paints
  *under* the next card no matter what z-index it carries. `.reveal` uses
  `backwards`, because its last keyframe is already the element's natural state.
  If a popover is mysteriously behind a sibling, look for a lingering transform
  before reaching for a bigger z-index.
- **Scope a shared component's modifier under its block.** `.status-pill.status-draft`,
  never a bare `.status-draft` — drafts.css already owns that name for the badge
  in its preview modal, and page CSS loads *after* dashboard.css, so the
  unscoped version would silently repaint every pill on the page. The same trap
  in reverse cost a working button: the page header's label span was called
  `.action-label`, which home.css already used for the quick-action tiles, and
  its `color` won over inheritance and left white text on a light-blue pill.
  Where a component cannot control its children's class names, pin them with
  `.block > * { color: inherit }`.
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
- **`div.textContent` → `div.innerHTML` is not an attribute escaper.** It
  encodes `&`, `<` and `>` and leaves both quote characters alone, which is safe
  for text and unsafe the moment the result is interpolated into
  `title="…"` / `alt="…"` / `aria-label="…"` / `data-*="…"`. A gallery filename
  or a blog title containing `"` closes the attribute and the rest injects.
  `gallery.js` escapes all five characters, exactly as Jinja's autoescape does —
  which is also what keeps its client markup byte-identical to the server's.
  **`all_blogs.js`, `categories.js`, `comments.js`, `activity.js`, `leads.js`,
  `schedule.js`, `analytics.js` and `site/comments.js` still use the two-line
  version and still build attributes with it.**
- **On-media controls do not take a theme.** `--media-*` / `--on-media` are the
  one token group that must not be redefined per theme: what sits behind them is
  a photograph, not a surface, so a light-mode value would put dark chrome on a
  dark photo half the time. `--email-paper` is the same idea for a different
  reason — a newsletter preview shows the email as the *recipient's* client will
  draw it, and mail clients paint white, so tinting it with the app's dark
  surface would make the composer lie about what is about to be sent. Anything
  overlaying an image or standing in for foreign paper uses those; anything
  overlaying our own surface uses the surface tokens.
- **An irreversible action names its own blast radius.** A button that says
  "Send" is a button whose consequence you have to already know. The newsletter's
  confirmation states the audience as a figure *and* repeats it in the button
  label — `Send to 128 subscribers` — and the rehearsal (send yourself a test)
  sits beside it rather than being the thing you remember afterwards.
- **A page's script and its modals belong inside `.dashboard-main`.** PJAX swaps
  only that element's innerHTML. Categories and Newsletter both shipped with
  modals outside it and both were dead on in-app navigation until a hard reload.
  Scripts are the confusing case: PJAX scrapes `<script>` tags from the *whole*
  document, so a misplaced script still runs and the screen looks half-working
  rather than obviously broken.
- **The same value rendered twice must round the same way.** Jinja's `round`
  filter is Python's — half to *even* — while JS `Math.round` is half *up*. A
  file of exactly 242.5 KB therefore painted one way from the server and
  another from the client, with nothing in between to explain the change. Where
  a template and a script both format the same figure, `(x + 0.5) | int` is the
  Jinja spelling of `Math.round`, and the pair needs a test that walks values
  either side of `.5` rather than a spot check.
- **A hover-only control is unreachable on touch and invisible to the eye that
  needs it.** The gallery's filename and file size sit under the tile
  permanently rather than inside a hover overlay — the filename is the thing you
  came to the tile to read. Where a control genuinely is hover-revealed (the
  tile checkbox, the row's quick actions), it needs a `@media (hover: none)`
  block that pins it visible, exactly as `.row-action` has.
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
