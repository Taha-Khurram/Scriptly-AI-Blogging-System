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

### Studio start state — `chat.html`

> **Superseded, and mostly carried over.** `/create` is now the Studio: one
> conversation instead of one prompt. The *start state* below survived almost
> intact — the aura, the eyebrow, the gradient clause, the lede, the starter
> chips and the prompt box are the same patterns in `chat.css`
> (`.chat-blank*`, `.chat-starter`, `.chat-composer .prompt-box`), and the
> `[data-state]` switch moved from `.create-stage` to `.chat-shell`
> (`blank` | `open`). What went is the destination pill: an agent that asks
> before it writes has no use for a control that names where an unwritten post
> will land. What is new is the rail of past conversations beside it. The
> reasoning below still applies; only the class names moved.

Two panels in one stage under the shared page header, switched by one attribute
(`.create-stage[data-state]`) exactly as the optimization panels switch: the
composer you arrive at, and the run card that replaces it in place. They are
siblings rather than a navigation, because the run outlives the screen — the
task keeps going server-side, and a failed run has to be able to hand the
prompt back.

```
╔ page header ═══════════════════════════════════════════════════╗
║ Blog studio                                                    ║
║ H1 Create                          ( ☾ )      ( ✎ Drafts )     ║
╚════════════════════════════════════════════════════════════════╝
                 ( ✦ Ready to create, name )
          What's on your  ·  mind today?   ← brand-gradient text
                 lede: what the agent will actually do
   ╭ .prompt-box ─────────────────────────────────────────────╮
   │  A step-by-step guide to…                                │  ← --surface-2, 28px
   │  ( ⌸ Save to drafts ▾ )  Lands in Drafts…   412/2000 (↑) │
   ╰──────────────────────────────────────────────────────────╯
          Enter to generate · Shift + Enter for a new line
   Not sure where to start?
   ( How-to guide ) ( Comparison ) ( Listicle ) ( Explainer )
```

The centred prompt entry still mirrors Gemini's empty state — soft-filled input
with no hard stroke, lifting to `--surface-1` + `--elev-2` on focus, circular
accent submit — but it is a **column** now, field over a control footer, because
a single-line box with one round button could not say where the finished blog
was going to land. `/api/generate` has always taken `auto_submit`; nothing on
screen could set it, so every generation silently became a draft.

**Destination.** A `.select-pill` wrapped around a real `<select>` (§13), with
the explanatory note carried on the `<option>` rather than in the page script —
the role check that decides which outcome is even on offer already lives in the
template, and two places deciding the same thing is how one of them ends up
wrong. Admins are offered *Publish when done*, everyone else *Send for review*,
because that is what the server does with the flag.

**Starters.** Four chips that drop a shaped prompt in and select its first
`[bracketed]` slot, so the reader types over the part that is theirs. The blank
page was the screen's real problem: it asked for a topic "in detail" and gave no
sense of what detail buys you.

**The counter** appears at 200 characters and turns `--warning` past 1200 — a
hint that the prompt has become an essay, not an error. The field accepts up to
`maxlength`; nothing is blocked.

### Working session — `chat.html` → `drafts.html`

> **Superseded in form, kept in substance.** The standalone run card described
> here is gone; a run is now one turn in an ongoing thread, and the thread keeps
> going after it. The progress bar and percentage went with it, because a
> conversation does not have a completion percentage — the status line at the
> foot of the live turn says what stage the agent is on and nothing pretends to
> know how much is left. The `.turn-*` markup, the reasoning disclosure and the
> streamed draft are unchanged, which is why they were extracted into
> `components/thread.css` in the first place.


```
   ┌ .run-card ───────────────────────────────────────────────┐
   │ ✦  Writing the draft                       30%    0:42   │
   │    runs on the server — you can leave and come back      │
   │ ▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░░  ← brand gradient + shimmer    │
   │ ✔ Warming up                                             │
   │ ◌ Writing the draft   ← pulsing, the long one             │
   │ ○ Formatting and styling                                 │
   │ ○ Assigning a category                                   │
   │ ○ Saving to your library                                 │
   │ ┌ Working from ────────────────────────────────────────┐ │
   │ │ "A step-by-step how-to guide on composting…"         │ │
   │ └──────────────────────────────────────────────────────┘ │
   └──────────────────────────────────────────────────────────┘
```

Generation progress is the agent-working surface: the brand gradient under
`brandShimmer`, the same signature `#nav-progress` uses, so "the system is
thinking" always looks the same wherever it happens.

What it is *not* any more is one line of text and a 6px bar. The pipeline
reports five named stages, so the card names all five and marks where it is —
"Writing blog content" used to sit alone for a minute with nothing to say how
far in it was or that three more steps were still to come. The five are the
stages `_run_generation_task` actually emits; the old client had `outline` and
`humanizing` in its message table and neither ever fired (the outline is derived
from the generated headings with no model call, and humanization is a separate
on-demand action from the drafts screen).

**The bar is the server's real percentage and nothing else.** It does not creep
between stages. A bar that invents progress can strand a wrong value, and there
are already three honest liveness cues — the shimmer, the pulsing step dot, and
the elapsed clock — so it never has to lie about how much is left. Every state
is also a colour, a glyph and a word, so reduced-motion loses nothing.

**A run survives the screen.** The task id is parked in `sessionStorage`, so
navigating away and back re-attaches to a generation in flight, painting the
stored stage before the first poll answers rather than sitting at 5%. A stale
entry (older than the task manager's 600s) is discarded, not resumed. The
half-typed prompt is parked the same way, because PJAX rebuilds
`.dashboard-main` from scratch.

**Failure lands on the card**, not only in a toast that has faded by the time
the reader looks back: the stage it stopped on goes `--danger` and stops
pulsing, the steps after it are dimmed as never-ran, and two buttons offer the
only two useful next moves — *Edit prompt* (returns to the composer with the
text intact) and *Try again*. A network blip is not a failure: three consecutive
misses are tolerated, since the generation is server-side and does not care that
one poll missed. `404`/`403` from the status endpoint are terminal — the old
poller checked only `data.status`, so an expired task meant polling forever.

### Creation history — `history.html`

Every conversation with the agent, kept. Two panes: an index of past runs, and
the one the reader picked, read back as the same thread the create screen paints
a live run in.

```
┌ .hist-shell ────────────────────────────────────────────────┐
│ ╭ .hist-rail (20rem, sticky) ╮ ╭ .hist-pane ──────────────╮ │
│ │ 🔍 filter          ( Clear)│ │                    YOU   │ │
│ │ ✦ How to Set an Ad Budget… │ │   ╭ prompt bubble ─────╮ │ │
│ │   3 hours ago · Growth     │ │   ╰────────────────────╯ │ │
│ │ ✦ RAG, Explained Without…  │ │ SCRIPTLY                 │ │
│ │   20 hours ago · AI        │ │ ✦ ⣿ Its thinking · 5 ▾   │ │
│ │ ⚠ Ten practical ideas for… │ │   H2 the title it chose  │ │
│ │   2 days ago · Stopped     │ │   the opening of the…    │ │
│ │ ───────────────────────────│ │   ✔ 1,043 words · 5 min  │ │
│ │ ( Load older )             │ │   ✔ Filed under Growth   │ │
│ ╰────────────────────────────╯ │   ( Open ) ( Reuse )  🗑  │ │
│                                ╰──────────────────────────╯ │
└─────────────────────────────────────────────────────────────┘
```

**The thread is a shared component** — `css/components/thread.css`, extracted
from the create screen's stylesheet and now rendered by both the Studio
(`chat.css`) and History (`history.css`). A finished generation and a
running one are the same conversation at two moments, and a reader who watched a
draft being written should recognise it a week later. What each page keeps is
what is genuinely its own: Create keeps the working-state animation on the
turn mark; History keeps the title, the excerpt note and the action row, none of
which exist while a run is still going.

**Reasoning is collapsed here and open on Create.** While a run works, its plan
is the only thing to read; afterwards the outcome is, and the plan is there for
the reader who wants to know why the piece took the angle it did. The
disclosure is labelled by what it knows — *Its thinking · 5 steps* — rather than
by a duration it did not watch.

**The excerpt is the opening, and says so.** A transcript stores ~900 characters
of the draft, not the draft: the post is one link away, and duplicating a 7 KB
body into a second collection would double the storage for every draft the app
has made. The note under it carries the link, and disappears when the blog it
pointed at is gone.

**Selection is a URL, not a state.** `?run=<id>` is written with `replaceState`,
so a reload or a copied link lands on the conversation being read, while PJAX
keeps owning the page's real history entries — pushing an entry per row would
make Back mean "the previous row I glanced at".

**Paging is keyset, filtering is local.** *Load older* passes the `created_at`
of the oldest row on screen; the filter field searches what is already loaded
and hides the button while a query is active, because loading a page the filter
would then hide reads as a broken button.

**A run that stopped is a first-class row.** It is marked in the rail (`--danger`
medallion, *Stopped*) rather than only inside, because the failed run is the one
a reader is most likely to come back for — it is the only kind that leaves
nothing in Drafts to explain itself. Its turn carries the error where the draft
would have been, and offers the prompt back.

**Deleting a transcript never touches the blog.** The two are separate objects
with separate lifetimes, and the copy says so in the confirm, in the toast and
in the API's own response.

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

**No native `<select>` is ever visible.** The browser draws that popup itself,
so it takes none of the product's surfaces, radius or type — square corners and
a hard OS-blue highlight dropped into the middle of a rounded, tokenised screen,
and on a forced dark theme over a light OS it renders white-on-blue. Declaring
`color-scheme` fixes the *mode* but not the chrome. Every select in the app is
therefore an owned listbox with the real `<select>` kept in the DOM as the value
holder, in one of two shapes:

- **`.filter-pill`** — toolbar chrome, for a filter bar (All Blogs' category,
  Gallery's sort).
- **`.select-field`** — `.app-field` chrome, for a select standing in a form
  beside text inputs (the newsletter composer's post count).

Behaviour is the same `initSelectPill` module either way: it binds on
`[data-select-trigger]`, never on a class, so one module drives both looks. It
writes the chosen value through to the `<select>` and fires a real bubbling
`change`, so existing `.value` reads and `change` listeners need no changes. It
deliberately does **not** set the trigger's caption — what a trigger should read
once a value is applied differs per screen, so each page owns that one line.

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

**Compose opens on a prompt, not a form.** The starting state borrows the
Studio's idiom (`chat.html`) — one question, one field, one action — because the
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

### Optimization — `optimization.html`

Five jobs behind one segmented control, under the standard shell. The screen
was already tokenised but had adopted none of the components: a bespoke
underlined tab bar, a bespoke custom select, a bespoke dropdown, a bespoke
empty state and a bespoke dashed empty card — ~600 lines of page CSS
re-implementing things that already existed.

```
╔ page header ═══════════════════════════════════════════════════╗
║ Search performance                                             ║
║ H1 Optimization                                     ( ☾ )      ║
╚════════════════════════════════════════════════════════════════╝
┌ ✦ 12 Optimizations run ┬ ↗ +11 Average gain ┬ 🏆 82 Best score ┐
└────────────────────────┴────────────────────┴──────────────────┘
┌ (Optimize) (Reports 12) (Draft keywords) (URL metrics) (Domain…)┐
└────────────────────────────────────────────────────────────────┘
┌ optimize ──────────────────────────────────────────────────────┐
│ Optimize a draft                                               │
│ …rewrites the title, meta, headings — and saves over the draft. │
│ Draft [⛁ Growth loops ▾]  Region [🌐 United States ▾]  (✦ Opt.) │
├────────────────────────────────────────────────────────────────┤
│ SEO score after            ┌ A ┐              [⬇ Export report] │
│  88   was 64  ↑+24         │grade│                              │
│ Score breakdown                                                │
│  Content   ▓▓▓▓▓▓▓▌░░░░  50 → 85  ↑+35   ← bar = now, ▌ = before│
│  Links     ▓▓▓▓▓▓░▌░░░░  70 → 62  ↓-8                          │
│ ┌ New title ─────────┐ ┌ Primary keyword ──┐                   │
│ ┌ ✓ Changes made ────┐ ┌ ⚡ Go further ─────┐                   │
└────────────────────────────────────────────────────────────────┘
```

**Optimize leads.** The old bar opened on URL Metrics — a third-party lookup —
and buried the product's own capability fourth. Optimize is also the only tab
that *changes* anything; the other four report on the world.

**The irreversible action names its own blast radius.** A run rewrites the
draft's title, meta description, headings and body and saves over it, with no
undo. It fired from a single unguarded click on a button labelled "Optimize".
It now opens a confirmation that names the draft and the region and repeats the
consequence in the button's own label — `Optimize and save` — the same rule the
newsletter's send follows.

**The score breakdown is finally on screen.** The API has always returned a
per-category before/after (`comparison.breakdown_comparison`); it went straight
into the exported HTML report and nowhere else. Each category is a **bullet
chart**: the bar is the score now, a 2px tick is where it started. Two stacked
fills would hide the smaller of the pair whichever way the run went — a tick
reads the same in both directions and does not depend on telling two tones
apart. Direction is carried by an arrow glyph, a sign and a title word as well
as by colour.

**One attribute owns panel state.** `.opt-body[data-state]` switches between
`empty · loading · error · results`. The old screen ran two mechanisms side by
side — `display:block` on the empty state and a `.show` class on the results —
which is two sources of truth for one question, and it had **no error state at
all**: a failed lookup only raised a toast and returned the panel to "empty", so
a rate-limited API looked exactly like a run you had never started. Failures now
land in the panel, quoting the message the API actually sent, with a retry.

**Tables where the data is tabular.** Keyword research is a comparison down a
column, so it stays a table rather than becoming the row idiom; it sheds CPC,
clicks and traffic potential below 760px instead of scrolling the page
sideways. Domain keywords is the provider's shape, so its columns are the
**union of every row's keys** — built from the first row alone, a field omitted
on row 1 dropped that column for every row — and the first column is `sticky`
so the keyword stays readable while the rest scrolls under it.

Reports moved to the `.data-row` listing: the grade is the monogram, the row
opens a detail modal, and export and delete sit permanently in the trailing
area rather than behind hover, because the row's own click is taken and there
is no ⋮ menu holding a duplicate.

Six things were broken rather than merely dated, and are fixed:

- **The custom select had no keyboard support.** `cs-*` hid the native control
  with `display: none !important` and rebuilt it as `<div role="option">` with
  click handlers and a trigger that listened for no keys — so the draft and
  country pickers could not be operated by keyboard at all, and a screen reader
  got a listbox with no focusable options. They are `.select-pill` now, which
  keeps the real `<select>` as the value holder; ~115 lines of JS and ~150 of
  CSS went with it.
- **`document` listeners accumulated on every visit.** The page bound its
  dismiss handlers at module scope, and PJAX re-injects the file on each
  navigation to `/optimization`, so the fifth visit had five copies bound and
  nothing ever removed them. One IIFE, one `AbortController` the next run
  aborts.
- **Eight globals and inline `onclick`.** `report.id` was interpolated straight
  into an `onclick` attribute with no escaping, and `escapeHtml` was the
  `textContent → innerHTML` two-liner, which leaves both quote characters
  alone. Everything is delegation off `.dashboard-main` and the five-character
  escaper now.
- **Auto Optimize had no empty state.** `optimizeEmptyState` was referenced four
  times and `id="optimizeEmptyState"` existed nowhere in the template, so every
  reference was `null` behind a guard and the tab opened on a form above a void.
- **Delete used `window.confirm()`** — the last browser-native confirm in the
  product, and the only destructive action that did not say what it destroyed.
- **"Site Audit" was not an audit.** The endpoint is `topsearchkeywords.php`
  and returns the terms a domain ranks for. A tab that promises an audit and
  delivers a keyword list is a label that lies; it is **Domain keywords**.

The URL Metrics tiles are deliberately **not** `.stat-card`: a stat tile
promises label · value · delta · trend and that API returns none of the
comparison half. `.opt-metric` says what it can — the figure, its unit, and a
meter only where the figure is genuinely a ratio (domain and URL rating, out of
100). Counts compact to `1.2M` with the exact figure on `title`. Everything the
tiles do not claim used to be printed underneath as a flat label/value grid —
a debug view shipped as product UI — and now sits behind a closed disclosure
labelled as what it is.

The header tiles are server-rendered from the saved reports (`_summarise_reports`
in `optimization_routes.py`) so they are true on first paint, and re-derived in
the browser only once the report list has actually been fetched — an empty
cache before the first fetch would otherwise zero out figures that were already
correct. The tab is written to the hash, so a reload returns to the panel you
were on.

The exported report is a standalone document opened from the filesystem, where
no stylesheet of ours is loaded, so it is the one place that carries literal
hex — the light-theme token values, not the old `#4318FF` palette it shipped
with.

### Schedule — `schedule.html`

Three views of one queue under the standard shell. The screen had adopted none of
the components: the legacy `.dashboard-header`, its own stat tiles, its own
`.custom-modal-*` chrome and its own buttons — ~450 lines of page CSS restating
things that had already moved into `dashboard.css`.

```
╔ page header ═══════════════════════════════════════════════════════╗
║ Content planning                                                   ║
║ H1 Schedule    [🔍 search ⌘K]   ( ☾ )   ( 📅+ Schedule a blog )     ║
╚════════════════════════════════════════════════════════════════════╝
┌ ⚠ 1 post is past its publish time ────────────────── [ Show them ] ┐
└────────────────────────────────────────────────────────────────────┘
┌ 📅 QUEUED ─────────┬ ✓ PUBLISHED ────────┬ ⏳ NEXT PUBLISH ────────┐
│ 5   ⚠ 1 past due   │ 34   5 in 30 days   │ in 3 days              │
│      ▓▓▓▓▓▓▓▓░░ 4/5│      ╱╲__╱▔╲_●  ← 8w│ Tue, 19 Aug at 10:00 AM│
└────────────────────┴─────────────────────┴────────────────────────┘
┌ toolbar ───────────────────────────────────────────────────────────┐
│ (Week)(Month)(Upcoming 5)         ‹  Aug 16 – 22, 2026  ›  [Today] │
└────────────────────────────────────────────────────────────────────┘
┌ calendar card ─────────────────────────────────────────────────────┐
│  SUN   MON   TUE  (17)   THU   FRI   SAT     ← circled = today     │
│   16    ▌9 AM  …                                                   │
│         ▌Title                                                     │
└────────────────────────────────────────────────────────────────────┘
```

**Week is day columns, not time bands.** The old grid split each week into
Morning / Afternoon / Evening — 21 cells for a queue that is usually three posts,
and the band label said nothing the chip's own `9:30 AM` did not already say.
Each day is now one ordered stack. Below 940px the seven columns become seven
day *rows*, and below 640px an empty day drops out entirely.

**Month and Upcoming are new.** A month-at-a-glance is what content planning is
actually for, and *Upcoming* — the queue as `.data-row`s grouped under
`Today` / `Tomorrow` / weekday heads, with `Past due` first — answers "what goes
out next", which a week-only grid could not. It is also the only one of the three
that is readable on a phone. The month grid always draws **six full weeks**, so
the card never changes height mid-month, and a cell past three chips gets a
`+n more` that expands it in place rather than opening one more surface;
the expanded set lives in page state, so the minute tick can repaint without
collapsing a cell the reader has opened.

**Today is the circled date and nothing else.** Washing today's column in
`--accent-tint` is the obvious move and it is wrong: that is exactly the fill a
scheduled event carries, so every chip in today's column vanished into its own
background.

**The whole event is its own trigger, at every size.** The old chip hid a `⋮`
button behind `:hover` — unreachable on touch, invisible to anyone who does not
already know it is there, and impossible at month-chip width. A **published**
entry gets no trigger at all, because it has no actions left; the affordance
matches the capability instead of opening a menu holding one disabled row. In the
agenda the `⋮` is pinned visible rather than hover-revealed, since there it *is*
the menu and not a shortcut to one.

**Search answers a different question here.** Filtering a calendar in place just
empties cells, so a query switches to the list of every match across all of
time, published included, with the count stated and a `Clear`. Any tab click
leaves the search.

**The publish time is our own picker, not `datetime-local`.** That control brings
the browser's own calendar-and-time popup — unstylable, so it took none of the
product's surfaces, radius or type, and on a forced dark theme over a light OS it
landed as a pale OS widget in the middle of a dark dialog. It is the same reason
All Blogs' date range is our own grid. Both dialogs now carry:

```
Publish at
┌ --surface-2 ───────────────────────────────┬──────────┐
│  ‹        August 2026        ›             │  TIME    │
│  Su Mo Tu We Th Fr Sa                      │ ┌──────┐ │
│  ░░ ░░ ░░ ░░ ░░ ░░  1    ← past: disabled  │ │ 9:00 │ │
│   2  3  4  5  6  7  8                      │ │ 9:15 │ │
│   9 10 11 12 13 14 15                      │ │(9:30)│ │← chosen
│  16 ⟨17⟩ 18 (19) 20 21 22   ⟨today⟩ (pick) │ │ 9:45 │ │
│  23 24 25 26 27 28 29                      │ └──────┘ │
│  30 31  1  2  3  4  5                      │  scrolls │
├────────────────────────────────────────────┴──────────┤
│ 📅 Publishes Wednesday, 19 August 2026 at 9:30 AM     │
└───────────────────────────────────────────────────────┘
```

- **Inline, not a popover.** `.app-modal-body` scrolls, so a popover would be
  clipped by the dialog that contains it.
- The grid is `.cal` from §12, which **moved out of all_blogs.css** now that this
  is its second user. What stayed behind is only the *range* vocabulary
  (`.is-start` / `.is-end` / `.is-in-range` / `.is-preview` / `.is-picking`); a
  range is the specialisation, and a single selected day is the general case.
- **Time is a scrolling column of quarter-hours**, not three number fields and
  not a native time input. Blog publishing is not a to-the-minute act, and a list
  can grey out the slots that have already gone by on today's date — which a
  free-text field cannot do until you have already typed one. The column opens
  scrolled to the choice, or to the first slot still open.
- **Out-of-bounds is greyed, never removed.** A past day and a passed slot stay in
  place, so the month keeps its shape and the column does not silently start at an
  arbitrary row.
- **Changing the day revalidates the time.** Pick 00:15 tomorrow, then switch to
  today, and the time is dropped rather than left asserting an instant the server
  would refuse.
- **It opens on the value in force.** Reschedule lands on the time being replaced
  — except for an overdue entry, whose own time is no longer offerable, so that
  one opens unset. Schedule-a-blog always opens unset, because it is a fresh
  choice rather than an edit. Both are built at init, not on first open: a widget
  that only becomes valid after some other handler has run renders an empty grid
  the first time anything reaches it another way.
- **The summary is the label for what the confirm button will do**, the same job
  it does in the date-range modal's footer — and while the pick is incomplete it
  says which half is missing.
- Arrow keys walk the grid and follow the focus into the next month; build and
  paint are separate, so a repaint never destroys the button under the pointer.

**The AI Publish Time Agent panel is commented out of both dialogs for now.**
`GET /api/schedule/best-time` is untouched and still serves `drafts.js` and
`approval.js` — only this screen stopped calling it. The JS block is commented
rather than left live-but-unreachable (unreferenced functions that still compile
are what a later reader keeps and a linter stays quiet about), and it carries the
four-step restore note: the two template blocks, the JS block, the two
`loadBestTimes()` calls, and an `apply-slot` case that now has to write through
`resetPicker()` rather than into a datetime input that no longer exists. The CSS
(`.sched-besttime` / `.sched-slots` / `.sched-slot`) is still in place.

Six things were broken rather than merely dated, and are fixed:

- **Two destructive actions were `window.confirm()`.** Publish-now puts a post
  on a live site ahead of schedule and cancel-schedule un-publishes a plan, and
  both fired from the browser's own dialog, which takes none of the product's
  surfaces and cannot name what it is about to do. Both are now `.app-modal`
  confirmations that state the post *and* its scheduled time and repeat the
  consequence in the button's own label — `Publish now`, `Move back to drafts` —
  the rule the newsletter's send and the optimizer's run already follow.
- **A stalled publisher was invisible.** The job runs on a 60-second interval, so
  an entry still `SCHEDULED` more than a few minutes past its time did not run.
  The old screen drew it identically to a future post. Overdue is now its own
  state — banner, tile, `Past due` pill, its own group at the top of Upcoming.
- **The API fabricated two fields on every row.** `schedule_list` read
  `entry.get("category", "General")` and `entry.get("author", "Unknown")` from
  `schedule_entries` documents that have never carried either key, so every
  entry came back labelled `General` / `Unknown`. `save_schedule_entry` now
  denormalises the real category and author name at write time, older entries
  get their author resolved by a lookup **per distinct author** rather than per
  row, and anything genuinely unknown comes back empty so the client can omit
  the label instead of inventing one.
- **There was no loading state and no error state.** The calendar painted empty
  and then repainted once the fetch landed, and a failed request reached only
  `console.error` — so a broken API looked exactly like an empty schedule.
  Empty · loading · error · results are now four states of one region switched
  by one attribute, and the error state quotes what the server actually said and
  offers the retry.
- **Five globals and inline `onclick`.** Blog ids and titles were interpolated
  straight into `onclick` attributes through the `textContent → innerHTML`
  escaper, which leaves both quote characters alone — a title containing `"`
  closed the attribute. It is delegation off `.dashboard-main` and the
  five-character escaper now.
- **The page script was not re-entrant.** PJAX re-injects it on every visit to
  `/schedule`, and nothing was ever unbound. One IIFE, one `AbortController` the
  next run aborts, `{ signal }` on every listener and every fetch — so a
  response can no longer land in a screen that is no longer there, and an
  `AbortError` is told apart from a real failure rather than reported as "check
  your connection".

Two gaps are *not* fixed, because they live outside this screen: a non-admin's
`requested_schedule_at` is written and shown in the approval queue but the
approval flow never acts on it, and `POST /api/blogs/<id>/status` accepts
`SCHEDULED` without writing a `schedule_entries` document, so anything scheduled
that way would never appear on this calendar.

### Approval queue — `approval_queue.html`

Shares the schedule's vocabulary: stat tiles, status pills, and the requested
publish time printed on the row awaiting a decision.

### Site settings — `site_settings.html`

Eleven sections, 113 controls, one save. The screen it replaces was the largest
single template in the app — 3256 lines, of which **1028 were an inline
`<style>` block and 590 an inline `<script>`**: uncacheable, re-parsed on every
visit, and PJAX re-injecting the whole style block into `<head>` each time. It
was also the only screen in the product with **no `@media` queries at all**.

```
╔ page header ═══════════════════════════════════════════════════════╗
║ Configuration                                                      ║
║ H1 Site Settings   [🔍 find a setting]  ( ☾ )  ( Visit your site ↗ )║
╚════════════════════════════════════════════════════════════════════╝
┌ ▣  My Awesome Blog ─────────────── [ 🔗 host/site/my-blog  ⧉ ] ────┐
│    Notes on building things                                        │
└────────────────────────────────────────────────────────────────────┘
┌ rail ────────────┬ panel ──────────────────────────────────────────┐
│ SITE             │  General                                        │
│  ▸ General       │  ───────────────────────────────────────────────│
│    Appearance    │  Site name              Niche or category       │
│    Content       │  ▢ My Awesome Blog      ▢ Technology            │
│ PAGES            │                                                 │
│    Header/footer │  Site URL slug                                  │
│    Hero sections │  ┌ /site/ │ my-blog                    (↻) ┐    │
│    Legal      ●  │  Description                                    │
│ DISCOVERY        │  ▢▢▢                                            │
│    SEO           │  Visibility  [ 👁 Public — anyone can view ▾ ]   │
│    Social        │                                                 │
│    Permalinks    │  YOUR CONTENT AT A GLANCE                       │
│ SYSTEM           │  ┌ 34 Published ┬ 8 Categories ┬ 2 Awaiting ┐   │
│    Locale & time │  PUBLISHED POSTS                                │
│    Google Sheets │  ▣ Title · category · date        [ Unpublish ] │
└──────────────────┴─────────────────────────────────────────────────┘
┌ sticky, only while dirty ──────────────────────────────────────────┐
│ ● 3 unsaved changes in General and Legal      [Discard] [Save]     │
└────────────────────────────────────────────────────────────────────┘
```

**A rail, not a tab strip.** `.seg-tabs` scrolls sideways, so at eleven items
most sections are off-screen at any moment and finding one is a hunt. The rail
shows all eleven at once, groups them (*Site · Pages · Discovery · System*), and
has room for a per-section change marker. Below 1000px it becomes a horizontal
scroller with the group labels dropped and a hairline where each label was, so
the grouping survives the collapse.

**The save bar states what it is about to do.** 113 controls behind one
always-on *Save All Settings* button said nothing about what had been edited, or
whether anything had. The bar is sticky, appears only while something differs
from what was last saved, counts the edits, names the sections in the rail's own
words, and offers Discard. Each changed field carries a dot, and so does its
section in the rail — otherwise "3 changes" leaves you opening eleven panels
looking for them.

**Leaving is guarded.** PJAX swaps `.dashboard-main`, so navigating away took the
whole form with it silently. The guard is bound in the **capture phase** on
purpose: `app.js` binds its PJAX interceptor on `document` in the bubble phase
and is registered first, so a bubble-phase guard would fire after the navigation
had already started. `beforeunload` covers the hard reload the dialog cannot.

**113 fields are eight Jinja macros.** `field` · `area` · `toggle` · `choose` ·
`image` · `colour` · `field_foot` · `panel_head` / `nav_item`. Written longhand —
as the old template was — that is ~2200 lines of near-identical markup, and every
label association has to be got right 113 separate times.

**Spacing belongs to the container, never to adjacent siblings.** Every panel,
group and disclosure body is a flex column with one `gap`. The rule this replaces
was a chain of `+` selectors ending in `.app-field + .app-field { margin-top }`,
and the children of a two-column `.set-grid` are *all* `.app-field` — so items
2..n each took a 20px top margin the first item did not, and **every two-column
row sat staggered against the one beside it**. Deleting those rules is only half
the fix: `.set-disclosure-body` had been relying on them, and lost its spacing
entirely until it was given a gap of its own. Both halves are guarded by a test
that refuses any `margin` on a `+` selector whose subject can be a grid child.

What keeps controls on one line across a row is the **label reserving a line**
(`min-height` of one line, plus `text-wrap: balance`), because a control's
vertical position is set by the height of the label above it. Grid rows stay
ragged at the *bottom* — hints differ in length — and that is correct: the gap
absorbs it, and `align-items: start` stops a tall neighbour stretching a
one-line field's input to match.

**Hint and counter share one footer row.** As two blocks a field with both had a
right-aligned number floating above a left-aligned sentence. `.app-field-foot` is
emitted only when there is something to put in it, and every field type routes
through it — a select's hint sits on the same baseline as a text input's in the
same row.

**The colour well stretches rather than guessing.** A fixed `height: 40px` beside
an input sized by its own padding and font is visibly crooked and drifts the
moment either token changes; `align-self: stretch` is the whole fix.

**`.app-field` styles every input in it as a text field.** dashboard.css §14 sets
`width: 100%`, padding, a `--surface-2` fill and a radius on
`.app-field input, textarea, select` — which is right for the eighty text boxes on
this screen and wrong for anything else in one. The permalink radios shipped that
way: each became a full-width 43px filled box, so the glyph drew in the middle of
its card and the label was pushed past the card's right edge and off the screen
entirely. A control that is a *glyph* rather than a box has to take its box back:

```css
.set-radio input[type="radio"],
.set-radio input[type="radio"]:hover,
.set-radio input[type="radio"]:focus { width: auto; padding: 0; background: none; … }
```

The `:hover` and `:focus` copies are load-bearing, not thoroughness:
`.app-field input:focus` weighs the same (0,2,1) as the page's own selector, so
without them focusing a radio repaints a fill and a 3px ring onto the control —
and the ring belongs to the card, which draws it with `:has()`.

`.set-prefixed input` escaped the same trap only because it already overrode
width and background for its own reasons. A `<input type="color">` is exempt: it
*is* a box, declares its own geometry, and the inherited hover/focus states are
wanted feedback on it. A guard now walks the rendered page for every non-text
input inside an `.app-field` and checks each one either resets the chrome or owns
its geometry deliberately.

One positioning bug is worth recording because the markup makes it inevitable:
the image field's replace/remove pair cannot be *children* of the preview — a
button inside a button is invalid — so they are siblings, `position: absolute`,
and with no positioned ancestor they resolved against the initial containing
block and **floated at the top-right corner of the page**. `.set-image-wrap`
exists to be that ancestor, and the guard checks the overlay is never emitted
outside it.

Also: twelve inline `style="…"` attributes became real classes (`.set-row-head`,
`.set-standalone-label`, `.set-status-row`, `.set-group-head`, `.set-inline-link`).
An inline style cannot be re-themed, cannot be overridden by a media query, and
hides a layout decision from the stylesheet where the rest of them live. The only
survivors are the two `--reveal-delay` values, which are that component's
documented API. Each panel head repeats its rail icon, and the hero disclosures'
field counts are asserted against the real number of controls inside them, so a
hardcoded "21 fields" cannot go stale.

**Ten native `<select>`s became ten `.select-field` listboxes**, so no
browser-drawn popup is visible on the screen with the most dropdowns in the app.
The trigger caption is the page's one line of glue, as §13 requires.

**The switch is this page's, not the system's.** Bootstrap's `.form-switch`
draws its knob from a fixed-colour `background-image` data URI, so it cannot
follow the accent and reads wrong in dark mode. `.set-switch` keeps the real
`<input>` as the control — transparent over the track — so label wiring, focus
and keyboard behaviour are the platform's. It stays page-scoped because this is
its first user; it is the obvious candidate to move into §12 when a second
screen wants one.

Nested maps are **normalised once** at the top of the template
(`{% set seo = settings.seo or {} %}`, eleven of them). `settings.seo.x` raises
`UndefinedError` the moment `seo` is absent — Jinja's `Undefined` raises on
further attribute access — and the old template had ten such chained reads. The
server's defaults do seed all eleven maps, which is the only reason it never
fired; the page was one changed default away from 500ing for a fresh account.
The binding also keeps the truthiness the template relies on, since an empty map
is still falsy.

Five things were broken rather than merely dated, and are fixed:

- **The entire Legal section was never saved.** The hand-written payload built no
  `legal` key, so `data.get('legal', {})` on the server always saw nothing. Nine
  fields — the privacy policy body, the terms body, the legal contact address and
  the whole cookie banner — could be typed, saved, and confirmed with a green
  *Settings Saved* toast, and were never stored. `site_routes.py` reads every one
  of them to serve `/privacy-policy`, `/terms-of-service` and the banner, so the
  feature was wired at both ends and disconnected in the middle. Note the key
  inside the map is `contact_email`, not the field's id.
- **The route could not tell "omitted" from "cleared".** Every nested section was
  written as `data.get(section, {})`, and only Firestore's field-by-field
  `set(merge=True)` stopped an absent section from blanking the stored one. A
  caller that omits a section now leaves it alone explicitly, so the behaviour
  does not depend on that detail.
- **Sheet rows and gallery filenames went into markup raw.** The activity table
  interpolated `user`, `action` and `page` straight in, from a spreadsheet anyone
  with edit access can write to, and the picker built `onclick="…('<url>')"` from
  the filename. Both escape all five characters now, and the picker is
  delegation rather than an inline attribute.
- **The character counters never initialised on an in-app arrival.** They were
  set from a `DOMContentLoaded` listener, which never fires again after the first
  load, so every PJAX visit showed `0/70` regardless of the field's contents.
- **The timezone did not refresh the time preview.** `updateTimePreview()` reads
  the timezone, but was wired to `date_format` and `time_format` only — the one
  field the preview exists to explain was the one that left it stale. The
  refresh is also debounced and sequence-numbered now, so a slow earlier answer
  cannot overwrite a newer one.

One thing the old screen depended on and no longer does: the published/pending
figures were found with `document.querySelector('[style*="rgba(67, 24, 255"] h2')`
— matching on an inline style string that existed **only** to keep that selector
working. A tidy-up of the CSS would have silently broken the counters. They are
`[data-stat-published]` and `[data-stat-pending]` now.

### Analytics — `analytics.html`

Four states behind one screen — OAuth missing, not connected, connected with no
property chosen, and the dashboard. The first three are centred `.an-gate`
cards; only the last one has charts.

```
╔ page header ══════════════════════════════════════════════════════════╗
║ Insights · H1 Analytics                       ( ☾ )  ( ⚡ Disconnect ) ║
╚═══════════════════════════════════════════════════════════════════════╝
┌ filter bar — one row, scopes everything below ────────────────────────┐
│ (Today)(7 days)(30 days)     ⛁ property · 🌐 domain · ● Tracking live │
└───────────────────────────────────────────────────────────────────────┘
┌ Page views ──────┐┌ Sessions ────────┐┌ Users ───────────┐
│ ▣ 12.4K ↑18%  ∿∿ ││ ▣ 8.1K  ↓6.5% ∿∿ ││ ▣ 6.3K level ∿∿ │ ← also the
└──────────────────┘└──────────────────┘└──────────────────┘   chart's
┌ Page views · last 7 days (8fr) ───────┐┌ Right now (4fr) ──┐  selector
│ 500 ┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈ ││        47         │
│     ╱‾╲    ╱‾‾╲___╱‾╲  2px + 10% wash ││   people active…  │
│ 0   ───────────────────────────────── ││ ───────────────── │
│     Aug 16  18  20  22                ││ Avg. session 2m14 │
│     ▸ Table view                      ││ Bounce  41% ▓▓░░  │
└───────────────────────────────────────┘└───────────────────┘
┌ Top pages (7fr) ──────────────────────┐┌ Traffic sources (5fr) ────────┐
│ Home — Scriptly                       ││ Organic search ▓▓▓▓▓▓ 700 44% │
│ /                    900  1m12s  36%  ││ Direct         ▓▓▓▓   400 25% │
│ ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓  ││ …                             │
└───────────────────────────────────────┘└ 2 other channels ▓  30  2%    ┘
```

**No categorical palette.** Every mark on the screen is `--accent`, because
nothing here encodes *identity*. The time series carries one series at a time —
the reader picks it — and channel names are nominal, so colouring those bars by
value would spend the identity channel re-encoding what bar length already
shows. That also means the screen needs no legend and no colour-matching.

**The numbers were checked, not eyeballed.** `--accent` sits at 6.4:1 against
`--surface-1` in light and 9.6:1 in dark, well past the 3:1 floor for marks.
Axis and tick text is `--text-muted` (6.1:1 / 6.3:1); `--text-faint` manages
only 3.7:1 in light, so no small text on this screen uses it. Gridlines are
`--border-subtle` at 1.2:1, which is the point — a grid is recessive by spec.

**Marks** follow the fixed specs: 2px line with round joins, a ~10% wash beneath
it (`--accent-tint` is 8% light / 12% dark), hairline *solid* gridlines, one 8px
endpoint dot with a 2px surface ring, and horizontal bars 10px thick with a 4px
rounded data-end and a square baseline. The y axis rounds on a 1 / 2 / 2.5 / 5
ladder — a plain 1 / 2 / 5 ladder sends a peak of 240 to an axis of 500, and the
line then never rises past halfway.

**The KPI row is the chart's control.** Three `.stat-card`s carrying the full
`label · value · delta · trend` contract, each a button that repaints the chart.
The screen this replaced had four medallions and four bare figures: they said
how many and left the reader to guess whether that was good. The delta needed a
comparison window, so `/api/analytics/overview` now reports the previous period
of equal length beside the current one, and a new `/api/analytics/timeseries`
supplies the shape — by hour for today, by day otherwise.

**Gaps are real zeros.** GA4 omits days with no traffic, so plotting only what
it returns compresses those gaps and the x-axis quietly misstates the range.
`_fill_days` densifies the window; `_fill_hours` stops at the last hour that
reported rather than padding a flat line across hours that have not happened.
And the daily rows are never summed for the period total — `totalUsers` is
de-duplicated, so a returning visitor would be counted twice.

**Shares are of the whole.** Page share divides by the period's total page
views, not by the ten rows on screen; the channel endpoint returns every channel
rather than the top eight, so the percentages add up to 100 and the tail folds
into one "Other" row instead of being dropped. The share *meter* on a page row
scales against the top row, because at 3% of a site's traffic every bar would
otherwise be an invisible sliver.

**Every chart has a table view** — a `<details>` twin under the time series
carrying all three series per point, so nothing is reachable only by hovering.
The crosshair snaps to the nearest X (readers aim at a date, never at a 2px
line), keyboard arrows walk the same readout, and the tooltip leads with the
value because the reader already knows which series is on screen.

**Failure is contained.** Each panel fetches independently, so a broken chart
leaves the figures above it standing. An expired token reveals the pre-rendered
`.setup-banner.is-danger` and dims the data in place — the screen this replaced
overwrote the whole content wrapper with a banner to report it. The realtime
pulse only animates while a poll is actually succeeding: a heartbeat over a dead
connection is a lie the reader cannot catch. And a period change refetches
behind the previous render held at reduced opacity — no skeleton, no jump.

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
| Icons | `.material-symbols-outlined` | Google Material Symbols, self-hosted subset (below). Nav chrome at `wght 300 / opsz 24 / 22px`; add `.icon-inline` for an icon inside text, `.icon-fill` for the filled cut |
| Brand marks | `.brand-icon` | The four social logos, inline SVG from `partials/brand_icon.html` — Material Symbols has no company logos |
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
| Setup banner | `.setup-banner` | `--warning-soft` on `--warning-border`; a blocked screen explaining itself. `.is-danger` for a connection that broke rather than one never set up. In dashboard.css §12 since analytics became its second user |
| Time series | `.an-chart` / `.an-line` / `.an-area` / `.an-dot` / `.an-grid` / `.an-tick` / `.an-cursor` | single-series area chart, inline SVG at the container's real pixel size; crosshair + keyboard readout |
| Chart table view | `.an-table-wrap` / `.an-table-toggle` / `.an-table` | the `<details>` twin every chart carries, so no value is hover-only |
| Channel bars | `.an-bars` / `.an-bar-row` / `.an-bar-track` / `.an-bar-fill` | horizontal bars, one hue, direct-labelled; `.is-other` for the folded tail |
| Gate card | `.an-gate` / `.an-gate-icon` / `.an-gate-cta` / `.an-steps` | a screen that cannot show its content yet, explaining what it needs |
| Device toggle | `.device-toggle` / `.device-btn` | desktop ↔ phone preview width |
| Email preview | `.preview-stage` / `.preview-device` | sandboxed iframe on `--email-paper`, which does not invert |
| Send bar | `.send-bar` | sticky footer stating the audience beside the irreversible action |
| Panel state | `.opt-body[data-state]` / `.opt-state[data-for]` | one attribute switches empty · loading · error · results |
| Agent working | `.opt-working` / `.opt-working-badge` / `.opt-working-bar` | spinner pill over the indeterminate `brandShimmer` bar |
| Bullet meter | `.opt-meter` / `-track` / `-fill` / `-tick` | bar = value now, tick = the reference it started from |
| Bare metric | `.opt-metric` | label · value · unit, for data with no delta or trend to state |
| Data table | `.opt-table` / `.opt-table-scroll` | tokenised table; `.is-pinned` sticks the first column, `.opt-col-wide` drops below 760px |
| Difficulty | `.kw-difficulty` | word first, then the track and the number — never hue alone |
| Raw dump | `.opt-raw` / `.opt-raw-row` | closed disclosure over fields the UI does not claim |
| Row button | `.opt-row-btn` | trailing row action that is always visible, where no ⋮ menu duplicates it |
| Card head | `.card-head` / `.card-title` / `.card-link` / `.card-note` | title row + pill "view all" link + wrapping sub-line |
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
| Select field | `.select-field` / `.select-field-value` / `.select-field-caret` / `.menu.is-block` | the same listbox wearing `.app-field` chrome, for a select inside a form rather than a filter bar |
| Clear filters | `.filter-clear` | ghost text button, present only while something is applied |
| Date range | `.date-modal` / `.date-preset` / `.date-summary` | presets, then calendar, then what Apply will do |
| Calendar | `.cal` / `.cal-head` / `.cal-nav` / `.cal-dow` / `.cal-cell` / `.cal-day` | six full weeks, always; `.is-outside` / `.is-today` / `.is-selected`, and `:disabled` for out of bounds. In dashboard.css §12 since the schedule's publish-time picker became its second user |
| Range band | `.cal-cell.is-start` / `.is-end` / `.is-in-range` / `.is-preview` / `.cal.is-picking` | all_blogs.css only — band on the cell, endpoints on the button |
| Time column | `.sched-times` / `.sched-time` | scrolling quarter-hours beside a calendar; passed slots greyed, not removed |
| Prompt entry | `.prompt-box` / `.prompt-input` / `.prompt-foot` / `.prompt-submit` | soft-filled, 28px; a column — field over a control footer |
| Run card | `.run-card` / `-head` / `-bar` / `-steps` / `-step` / `-prompt` / `-fail` | named stages with the server's real percentage; see §2 |
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

**Every icon in the app is a Material Symbols ligature.** There is no second
icon set and no icon CDN. The `@font-face` and the three classes live in
`app/static/css/icons.css`, loaded by both `base.html` and
`site_base.html`; the file is `app/static/fonts/material-symbols-outlined.woff2`,
preloaded in both.

```html
<i class="material-symbols-outlined icon-inline" aria-hidden="true">edit_square</i>
```

- `.material-symbols-outlined` — the face. 22px, `wght 300`, for nav chrome
  where the glyph *is* the control.
- `.icon-inline` — an icon sitting in text: `1.15em`, `wght 400`, so it tracks
  the line it is on. Deliberately **one** class, so an existing element-scoped
  override (`.filter-pill > i { font-size: … }`, and there are ~114 of them)
  still outranks it.
- `.icon-fill` — the filled cut. Sets only the `FILL` axis, through a custom
  property, so it composes with any size modifier.

The ligature name is real text in the DOM, so **every icon needs
`aria-hidden="true"`** or a screen reader announces "more_vert".

Icons are `<i>` elements rather than `<span>` because that is what the
Bootstrap Icons they replaced were, and ~114 CSS rules select them as `i`.

Brand logos are the one thing the font cannot supply — Material Symbols has no
company marks. The four social logos are inline SVG from
`templates/partials/brand_icon.html`, sized by `.brand-icon`.

Self-hosting is not a performance preference. The icons are ligatures, so a
font that fails to arrive doesn't degrade to blank — every icon renders as its
own name (`edit_square`, `left_panel_close`) sprayed across the UI. And because
sidebar navigation is PJAX, nothing reloads, so one failed CDN request stayed
broken until a hard refresh. This is not hypothetical: the icon webfont *was*
on a CDN, and a `font-src` directive that listed the stylesheet's host but not
the font's blanked out all 675 icons in the app at once.

The file is subset to the ~141 glyphs actually used — 19KB, against 4MB for the
full variable font. `FILL` is kept as a live axis (`0..1`) because `.icon-fill`
needs it; the rest are pinned. The cost is that **an icon added to a template is
not in the font until the subset is rebuilt**:

```
python scripts/update_icon_font.py           # rescan, refetch, rewrite
python scripts/update_icon_font.py --check   # fail if an icon is missing
```

The script scans templates, JS and CSS for ligature names and validates them
against Google's published codepoints list before building — necessary because
the Fonts API answers `200` with an *empty font* for a name that doesn't exist,
so a typo would otherwise ship silently. `--check` is the CI form.
`material-symbols-outlined.txt` next to the font records what went in.

Names chosen at runtime — a lookup table keyed by record type, a severity
ladder, a delta direction — cannot be found by scanning. Those are listed
explicitly in `EXTRA_ICONS` in the script; **add to that set when you add a
data-driven icon**, or it renders as its own name. `tests/test_icons.py` renders
every page and fails if any icon resolves to a glyph the subset lacks.

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
  `optimization.js`, `newsletter.js` and `schedule.js` escape all five.
  **`all_blogs.js`, `categories.js`, `comments.js`, `activity.js`, `leads.js`,
  `analytics.js` and `site/comments.js` still use the two-line version and still
  build attributes with it.**
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
- **A page script runs again on every visit, so it must be re-entrant.** PJAX
  re-injects the file each time, and a listener bound to `document` or `window`
  at module scope is never removed — Optimization's dismiss handlers were bound
  five times over by the fifth visit. Take the newsletter/optimization pattern:
  one IIFE, one `AbortController` parked on `window` that the next run aborts,
  and `{ signal }` on every listener. Passing the same signal to `fetch` also
  cancels in-flight requests, so a response cannot land in a screen that is no
  longer there — which is why those handlers distinguish `AbortError` from a
  real failure instead of reporting "check your connection" on every navigation.
- **A screen with no error state reports failure as absence.** Optimization
  raised a toast and returned the panel to its empty state, so a rate-limited
  API looked identical to a lookup you never ran — and the toast was gone four
  seconds later. Empty, loading, error and results are four states of one
  region, switched by one attribute (`.opt-body[data-state]`), and the error
  state quotes what the server actually said and offers the retry.
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
- **A bar drawn on a `<span>` needs `display`.** `width` and `height` do nothing
  on an inline box, so `.stat-meter-fill` — a span carrying `height: 100%` and a
  server-rendered `style="width: 67%"` — computed to zero and the meter sat
  empty at every value, on both Newsletter and Optimization. The *track* looked
  fine throughout, which is what hid it: the track is a flex item of
  `.stat-meter` and gets blockified for free, while its child does not. Do not
  let a fill inherit its box type from an ancestor's `display` — a later change
  to that ancestor silently empties the bar. Note that `position: relative`
  does **not** blockify either; only `absolute`/`fixed` do.
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
