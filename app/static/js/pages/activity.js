/**
 * Activity — the audit trail: who changed what, filtered by kind, person and
 * date.
 *
 * Was thirteen globals (currentFilter, currentUser, currentSearch,
 * currentDateFrom, currentDateTo, currentPage, perPage, searchTimeout,
 * goToPage, openDateModal, setDatePreset, applyDateFilter, clearDateFilter)
 * hanging off `window`, wired to the markup through `onclick="goToPage(3)"`
 * strings and re-bound on a `DOMContentLoaded` listener that PJAX never fires —
 * so the page depended on app.js re-dispatching that event after a navigation.
 *
 * Rows are the shared .data-row, so nothing here builds a table. The first page
 * is rendered by Jinja (see the activity_row macro in activity.html) and this
 * file renders the identical structure for every page, filter and search after
 * it — the two have to stay in step.
 *
 * Two behaviours worth naming:
 *
 *   - The old page never re-read the stat tiles. Every figure above the list
 *     stayed frozen at whatever the first response held, whatever you filtered
 *     to afterwards. They are refreshed alongside the list now.
 *
 *   - Filtering replaced the list's innerHTML with a spinner in a
 *     `text-center py-5` block, which collapsed the card to the height of one
 *     line and threw the pager up the page under the pointer that was about to
 *     click it. The wait holds the card's height now.
 *
 * PJAX re-injects this file on every visit to /activity-log, so nothing holds a
 * reference across navigations and every document-level listener goes through
 * an AbortController the next run aborts.
 */

(function activityPage() {
    'use strict';

    if (window.__activityAbort) {
        try { window.__activityAbort.abort(); } catch (e) { /* already gone */ }
    }
    const controller = new AbortController();
    const signal = controller.signal;
    window.__activityAbort = controller;

    const $ = (sel, root) => (root || document).querySelector(sel);
    const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

    const root = $('.dashboard-main');
    const list = $('#activityList');
    if (!root || !list) return;

    const state = {
        type: 'all',
        user: 'all',
        query: '',
        from: '',
        to: '',
        page: parseInt(list.dataset.page, 10) || 1,
        perPage: parseInt(list.dataset.perPage, 10) || 10,
        total: parseInt(list.dataset.total, 10) || 0
    };

    let searchTimer = null;

    // ----------------------------------------------------------------------
    // Helpers
    // ----------------------------------------------------------------------

    // Escapes all five characters Jinja's autoescape does, not just the three
    // the `div.textContent -> div.innerHTML` trick covers: names and target
    // titles land in attributes here too.
    function esc(str) {
        return String(str == null ? '' : str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&#34;')
            .replace(/'/g, '&#39;');
    }

    // The same wording leads.js, drafts.js and home.js use, so a timestamp
    // reads the same wherever it appears.
    function relative(value) {
        const then = new Date(value);
        if (isNaN(then.getTime())) return '';

        const secs = (Date.now() - then.getTime()) / 1000;
        if (secs < 90) return 'just now';

        const mins = Math.round(secs / 60);
        if (mins < 60) return mins + ' min ago';

        const hours = Math.round(mins / 60);
        if (hours < 24) return hours === 1 ? 'an hour ago' : hours + ' hours ago';

        const days = Math.round(hours / 24);
        if (days === 1) return 'yesterday';
        if (days < 7) return days + ' days ago';
        if (days < 30) {
            const weeks = Math.round(days / 7);
            return weeks === 1 ? 'a week ago' : weeks + ' weeks ago';
        }
        return then.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
    }

    function absolute(value) {
        const d = new Date(value);
        if (isNaN(d.getTime())) return '';
        return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) +
            ' at ' + d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
    }

    function paintTimes() {
        $$('time[data-relative]', list).forEach((el) => {
            const iso = el.getAttribute('datetime');
            const text = relative(iso);
            if (!text) return;
            el.title = absolute(iso);
            el.textContent = text;
        });
    }

    // ----------------------------------------------------------------------
    // Types
    //
    // The same mapping the repository filters on and the template renders from,
    // so a row cannot land under a heading the tab beside it would not match.
    // ----------------------------------------------------------------------

    const BLOG_TYPES = ['blog', 'generated', 'edited', 'published', 'deleted', 'status_change', 'seo_optimized'];

    const TYPES = {
        blog: { label: 'Content', icon: 'article' },
        user: { label: 'People', icon: 'person' },
        comment: { label: 'Comment', icon: 'chat_bubble' },
        settings: { label: 'Settings', icon: 'settings' },
        newsletter: { label: 'Newsletter', icon: 'mail' },
        category: { label: 'Category', icon: 'label' }
    };

    const OTHER = { label: 'Other', icon: 'monitoring' };

    function kindOf(entry) {
        const raw = entry.target_type || entry.type || '';
        if (BLOG_TYPES.indexOf(raw) !== -1) return 'blog';
        return TYPES[raw] ? raw : 'other';
    }

    // ----------------------------------------------------------------------
    // Rows
    //
    // The structure the activity_row macro in activity.html produces. Any
    // change to one has to be made to the other.
    // ----------------------------------------------------------------------

    function rowHtml(entry) {
        const who = entry.user_name || 'Someone';
        const what = entry.target_name || entry.blog_title || '';
        const kind = kindOf(entry);
        const type = TYPES[kind] || OTHER;
        const mark = who.trim() ? who.trim().charAt(0).toUpperCase() : '?';
        const when = entry.timestamp || '';

        return '<div class="data-row">' +
            '<span class="row-mark" aria-hidden="true">' + esc(mark) + '</span>' +

            '<span class="row-main">' +
            '<span class="row-title">' +
            '<strong class="log-actor">' + esc(who) + '</strong> ' +
            '<span class="log-action">' + esc(entry.action_text || 'made a change') + '</span>' +
            '</span>' +
            '<span class="row-meta">' +
            (what
                ? '<span class="log-target">' + esc(what) + '</span>'
                : '<span class="log-target is-none">No target recorded</span>') +
            '<span class="row-sep row-meta-type" aria-hidden="true">·</span>' +
            '<span class="row-meta-type">' + type.label + '</span>' +
            '</span>' +
            '</span>' +

            '<span class="type-pill type-' + kind + '">' +
            '<i class="material-symbols-outlined icon-inline" aria-hidden="true">' + type.icon + '</i> ' + type.label +
            '</span>' +

            '<time class="row-time" datetime="' + esc(when) + '" data-relative>' +
            esc(String(when).slice(0, 10)) + '</time>' +
            '</div>';
    }

    // ----------------------------------------------------------------------
    // Card head and pager
    // ----------------------------------------------------------------------

    const TYPE_NOTE = {
        blog: 'Content only', user: 'People only', comment: 'Comments only',
        settings: 'Settings only', newsletter: 'Newsletter only', category: 'Categories only'
    };

    function pages() {
        return Math.max(1, Math.ceil(state.total / state.perPage));
    }

    function paintHead() {
        const count = $('[data-list-count]', root);
        if (count) count.textContent = String(state.total);

        const note = $('[data-list-note]', root);
        if (!note) return;

        const parts = [];
        if (pages() > 1) parts.push('Page ' + state.page + ' of ' + pages());
        if (state.query) parts.push('Matching “' + state.query + '”');
        else if (TYPE_NOTE[state.type]) parts.push(TYPE_NOTE[state.type]);
        note.textContent = parts.join(' · ');
    }

    // A window of three around the current page, with the first and last always
    // reachable — the same shape leads.js and approval.js draw. The old pager
    // printed every page number up to the current one plus two, so a log with
    // 40 pages rendered a row of buttons wider than the card.
    function paintPager() {
        const nav = $('[data-pager]', root);
        if (!nav) return;

        const last = pages();
        if (last <= 1) {
            nav.innerHTML = '';
            return;
        }

        const wanted = new Set([1, last, state.page]);
        if (state.page - 1 > 1) wanted.add(state.page - 1);
        if (state.page + 1 < last) wanted.add(state.page + 1);

        let html = '';
        let previous = 0;

        Array.from(wanted).sort((a, b) => a - b).forEach((p) => {
            if (previous && p - previous > 1) html += '<span class="pager-dots">…</span>';
            html += '<button type="button" class="pager-btn' + (p === state.page ? ' is-active' : '') + '" ' +
                'data-page="' + p + '"' + (p === state.page ? ' aria-current="page"' : '') + '>' + p + '</button>';
            previous = p;
        });

        nav.innerHTML = html;
    }

    // ----------------------------------------------------------------------
    // Loading
    // ----------------------------------------------------------------------

    function params() {
        const q = new URLSearchParams({ page: String(state.page), per_page: String(state.perPage) });
        if (state.type !== 'all') q.set('type', state.type);
        if (state.user !== 'all') q.set('user', state.user);
        if (state.query) q.set('search', state.query);
        if (state.from) q.set('date_from', state.from);
        if (state.to) q.set('date_to', state.to);
        return q;
    }

    // Why the list is empty, in the reader's terms. "No activity found." was the
    // same sentence whether the log was untouched or a filter had excluded
    // everything in it, and only one of those has anything to do about it.
    function emptyHtml() {
        const filtered = state.type !== 'all' || state.user !== 'all' || state.query || state.from || state.to;
        return '<div class="list-empty">' +
            '<span class="list-empty-icon"><i class="material-symbols-outlined icon-inline" aria-hidden="true">history</i></span>' +
            '<p>' + (filtered
                ? 'Nothing matches those filters. Widen the date range, or clear them and start again.'
                : 'Nothing recorded yet. Every publish, invitation and settings change your team ' +
                  'makes is written down here.') +
            '</p></div>';
    }

    async function load() {
        list.setAttribute('aria-busy', 'true');
        list.innerHTML = '<p class="activity-loading">' +
            '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>' +
            'Reading the log…</p>';

        try {
            const res = await fetch('/api/activity?' + params().toString(), { signal });
            const data = await res.json();
            if (!data.success) throw new Error('The activity log could not be read.');

            const entries = data.activities || [];
            state.total = data.total || 0;
            state.page = data.page || state.page;
            state.perPage = data.per_page || state.perPage;

            list.innerHTML = entries.length ? entries.map(rowHtml).join('') : emptyHtml();
            paintTimes();
            paintHead();
            paintPager();
            refreshStats();
        } catch (error) {
            if (error.name === 'AbortError') return;
            list.innerHTML = '<div class="list-empty">' +
                '<span class="list-empty-icon"><i class="material-symbols-outlined icon-inline" aria-hidden="true">error</i></span>' +
                '<p>' + esc(error.message) + ' Try again in a moment.</p></div>';
            const nav = $('[data-pager]', root);
            if (nav) nav.innerHTML = '';
        } finally {
            list.setAttribute('aria-busy', 'false');
        }
    }

    // The tiles count the whole log, not the current filter, so they only move
    // when something new is written — but they have to move then. The old page
    // left all four frozen at whatever the first render held for the lifetime
    // of the session.
    async function refreshStats() {
        try {
            const res = await fetch('/api/activity/stats', { signal });
            const data = await res.json();
            if (!data.success || !data.stats) return;

            const s = data.stats;
            const total = s.total || 0;
            const rest = (s.comment || 0) + (s.settings || 0) + (s.newsletter || 0) + (s.category || 0);

            paintTile('[data-stat-total]', total, null, total);
            paintTile('[data-stat-blog]', s.blog || 0, 'blog', total);
            paintTile('[data-stat-user]', s.user || 0, 'user', total);
            paintTile('[data-stat-rest]', rest, 'rest', total);
        } catch (e) {
            // Non-critical: the list below them is what the screen is for.
        }
    }

    function paintTile(sel, value, key, total) {
        const el = $(sel, root);
        if (!el) return;
        el.textContent = String(value);
        if (!key) return;

        const card = el.closest('.stat-card');
        const meter = card ? $('.stat-meter', card) : null;
        if (!meter) return;

        const share = total ? Math.round((value / total) * 100) : 0;
        meter.hidden = total === 0;
        const fill = $('.stat-meter-fill', meter);
        if (fill) fill.style.width = share + '%';
        const note = $('.stat-meter-note', meter);
        if (note) note.textContent = share + '% of the log';
    }

    // ----------------------------------------------------------------------
    // Filters
    // ----------------------------------------------------------------------

    function paintFilterChrome() {
        const applied = state.type !== 'all' || state.user !== 'all' || state.from || state.to;
        const clear = $('[data-clear-filters]', root);
        if (clear) clear.hidden = !applied;
    }

    document.addEventListener('page-search', (e) => {
        const value = ((e.detail && e.detail.value) || '').trim();

        // The no-change check has to happen inside the timer, not before it:
        // typing a letter and deleting it again lands back on the current query
        // but leaves a scheduled fetch for the letter behind, and an early
        // return here would let that one run.
        clearTimeout(searchTimer);
        searchTimer = window.setTimeout(() => {
            if (value === state.query) return;
            state.query = value;
            state.page = 1;
            load();
        }, 300);
    }, { signal });

    // The select-pill machinery in app.js writes through to the hidden <select>
    // and fires a real `change`, so this stays a plain change listener.
    const userSelect = $('#userFilter', root);
    if (userSelect) {
        userSelect.addEventListener('change', () => {
            state.user = userSelect.value || 'all';
            state.page = 1;

            // The pill renders its own current value: an applied filter should
            // read as "Ayesha", not as "Anyone" with a tint on it.
            const pill = userSelect.closest('[data-select-pill]');
            const trigger = pill ? $('[data-select-trigger]', pill) : null;
            const value = pill ? $('[data-user-value]', pill) : null;
            const on = state.user !== 'all';
            if (trigger) trigger.classList.toggle('is-active', on);
            if (value) {
                value.hidden = !on;
                value.textContent = on
                    ? (userSelect.options[userSelect.selectedIndex] || {}).text || ''
                    : '';
            }

            paintFilterChrome();
            load();
        }, { signal });
    }

    root.addEventListener('click', (e) => {
        const tab = e.target.closest('.activity-filter .seg-tab');
        if (tab) {
            if (tab.dataset.filter === state.type) return;
            $$('.activity-filter .seg-tab', root).forEach((t) => {
                t.classList.toggle('is-active', t === tab);
            });
            state.type = tab.dataset.filter || 'all';
            state.page = 1;
            paintFilterChrome();
            load();
            return;
        }

        const pageBtn = e.target.closest('[data-pager] .pager-btn');
        if (pageBtn) {
            const page = parseInt(pageBtn.dataset.page, 10);
            if (!page || page === state.page) return;
            state.page = page;
            load();
            // The list is about to be replaced from the top; leaving the reader
            // at the bottom of the old page would land them mid-way down the new
            // one with no idea it had changed.
            const card = $('.activity-card', root);
            if (card) card.scrollIntoView({ behavior: 'smooth', block: 'start' });
            return;
        }

        if (e.target.closest('[data-clear-filters]')) {
            state.type = 'all';
            state.user = 'all';
            state.from = '';
            state.to = '';
            state.page = 1;

            $$('.activity-filter .seg-tab', root).forEach((t) => {
                t.classList.toggle('is-active', t.dataset.filter === 'all');
            });
            if (userSelect && userSelect.value !== 'all') {
                userSelect.value = 'all';
                userSelect.dispatchEvent(new Event('change', { bubbles: true }));
                return;   // the change handler reloads and repaints
            }
            paintDateChrome();
            paintFilterChrome();
            load();
            return;
        }

        if (e.target.closest('[data-date-open]')) openDateDialog();
    }, { signal });

    // ======================================================================
    // DATE RANGE
    //
    // The same dialog All Blogs uses — the stylesheet moved to dashboard.css
    // §12 when this screen became its second user. A range picker over two
    // hidden inputs: the first click sets the start and arms the range, the
    // second closes it, and picking a second date before the first swaps them
    // rather than rejecting the click.
    //
    // This is the second implementation of that behaviour (all_blogs.js has the
    // other). They are not shared yet because that file drives its copy through
    // module-level globals; if a third screen ever needs a range, all three
    // should move to one component rather than a fourth copy appearing here.
    // ======================================================================

    const dateModal = document.getElementById('dateRangeModal');
    const calendar = dateModal ? $('[data-calendar]', dateModal) : null;

    // Each preset is a pure function of "today", so the same definitions drive
    // both setting a range and recognising one that is already set.
    const PRESETS = { today: 0, week: 7, month: 30, quarter: 90 };

    let calCursor = new Date();
    let picking = false;    // start chosen, waiting for the end
    let preview = '';       // hovered day while picking

    // Local, not toISOString(). toISOString() converts to UTC first, so anywhere
    // behind UTC "today" comes back as yesterday for most of the day.
    function isoLocal(date) {
        const m = String(date.getMonth() + 1).padStart(2, '0');
        const d = String(date.getDate()).padStart(2, '0');
        return date.getFullYear() + '-' + m + '-' + d;
    }

    function todayIso() {
        return isoLocal(new Date());
    }

    function isoDaysAgo(days) {
        const d = new Date();
        d.setDate(d.getDate() - days);
        return isoLocal(d);
    }

    function parseIso(iso) {
        if (!iso) return null;
        const [y, m, d] = String(iso).split('-').map(Number);
        if (!y || !m || !d) return null;
        return new Date(y, m - 1, d);
    }

    function fromInput() { return dateModal ? $('[data-date-from]', dateModal) : null; }
    function toInput() { return dateModal ? $('[data-date-to]', dateModal) : null; }
    function draftFrom() { const el = fromInput(); return el ? el.value : ''; }
    function draftTo() { const el = toInput(); return el ? el.value : ''; }

    function setDraft(from, to) {
        const a = fromInput();
        const b = toInput();
        if (a) a.value = from || '';
        if (b) b.value = to || '';
        paintCalendar();
        paintDraftChrome();
    }

    // Building and painting are separate on purpose. Hovering while picking
    // repaints the band on every mousemove, and rebuilding the grid's innerHTML
    // that often would destroy the very button the pointer is over — losing
    // :hover, losing focus, and flickering.
    function buildCalendar() {
        if (!calendar) return;
        const grid = $('[data-cal-grid]', calendar);
        if (!grid) return;

        const title = $('[data-cal-title]', calendar);
        if (title) {
            title.textContent = calCursor.toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
        }

        const first = new Date(calCursor.getFullYear(), calCursor.getMonth(), 1);
        const start = new Date(first);
        start.setDate(1 - first.getDay());

        let html = '';
        for (let i = 0; i < 42; i++) {
            const day = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i);
            const iso = isoLocal(day);
            html += '<div class="cal-cell" data-cell="' + iso + '">' +
                '<button type="button" class="cal-day" data-iso="' + iso + '" aria-label="' +
                esc(day.toLocaleDateString(undefined, { dateStyle: 'full' })) + '">' +
                day.getDate() + '</button></div>';
        }

        grid.innerHTML = html;
        paintCalendar();
    }

    function paintCalendar() {
        if (!calendar) return;
        const grid = $('[data-cal-grid]', calendar);
        if (!grid) return;

        calendar.classList.toggle('is-picking', picking);

        const from = draftFrom();
        const open = picking && !draftTo();
        const to = draftTo() || (open ? preview : '');
        const lo = from && to ? (from <= to ? from : to) : from;
        const hi = from && to ? (from <= to ? to : from) : '';
        const today = todayIso();
        const month = calCursor.getMonth();

        $$('.cal-cell', grid).forEach((cell) => {
            const iso = cell.dataset.cell;
            const day = parseIso(iso);

            cell.classList.toggle('is-outside', day.getMonth() !== month);
            cell.classList.toggle('is-today', iso === today);
            cell.classList.toggle('is-in-range', !!(lo && hi && iso > lo && iso < hi && !open));
            cell.classList.toggle('is-preview', !!(lo && hi && iso > lo && iso < hi && open));
            cell.classList.toggle('is-start', !!(lo && iso === lo));
            cell.classList.toggle('is-end', !!(iso === (hi || lo) && (hi || lo)));
        });
    }

    // Which preset, if any, the draft corresponds to. Lets the dialog show the
    // range in force on reopen rather than six identical rows.
    function matchingPreset(from, to) {
        if (!from && !to) return 'all';
        if (to !== todayIso()) return null;
        for (const key in PRESETS) {
            if (from === isoDaysAgo(PRESETS[key])) return key;
        }
        if (from === new Date().getFullYear() + '-01-01') return 'year';
        return null;
    }

    function describe(from, to) {
        const pretty = (d) => new Date(d + 'T00:00:00')
            .toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });

        if (!from && !to) return 'Showing all time';
        if (from && !to) return 'Showing from ' + pretty(from);
        if (!from && to) return 'Showing up to ' + pretty(to);
        if (from === to) return 'Showing ' + pretty(from);
        return 'Showing ' + pretty(from) + ' – ' + pretty(to);
    }

    // Keeps the preset highlight, the summary line and Apply in step with the
    // draft selection.
    function paintDraftChrome() {
        if (!dateModal) return;

        const from = draftFrom();
        const to = draftTo();
        const active = matchingPreset(from, to);

        $$('.date-preset', dateModal).forEach((btn) => {
            btn.classList.toggle('is-active', btn.dataset.preset === active);
        });

        const half = !!(from && !to);
        const summary = $('[data-date-summary]', dateModal);
        if (summary) {
            summary.textContent = half ? 'Pick the end of the range' : describe(from, to);
            summary.classList.toggle('is-hint', half);
        }

        // A half-open range would silently become "everything since", so Apply
        // waits until the second date is in.
        const apply = $('[data-date-apply]', dateModal);
        if (apply) apply.disabled = half;
    }

    // The pill outside the dialog, reflecting what is actually applied.
    function paintDateChrome() {
        const pill = $('[data-date-open]', root);
        const label = $('[data-date-label]', root);
        if (!pill || !label) return;

        const on = !!(state.from || state.to);
        pill.classList.toggle('is-active', on);

        if (!on) {
            label.textContent = 'Any time';
            return;
        }
        const short = (d) => d
            ? new Date(d + 'T00:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
            : 'Any';
        label.textContent = short(state.from) + ' – ' + short(state.to);
    }

    function openDateDialog() {
        if (!dateModal || typeof bootstrap === 'undefined') return;

        picking = false;
        preview = '';
        // Open on the month the range ends in, so an existing selection is on
        // screen instead of "today" with the choice scrolled out of view.
        calCursor = parseIso(state.to) || parseIso(state.from) || new Date();
        buildCalendar();
        setDraft(state.from, state.to);

        (bootstrap.Modal.getInstance(dateModal) || new bootstrap.Modal(dateModal)).show();
    }

    if (calendar) {
        calendar.addEventListener('click', (e) => {
            if (e.target.closest('[data-cal-prev]')) {
                calCursor = new Date(calCursor.getFullYear(), calCursor.getMonth() - 1, 1);
                buildCalendar();
                return;
            }
            if (e.target.closest('[data-cal-next]')) {
                calCursor = new Date(calCursor.getFullYear(), calCursor.getMonth() + 1, 1);
                buildCalendar();
                return;
            }

            const day = e.target.closest('.cal-day');
            if (!day) return;
            const iso = day.dataset.iso;

            if (!picking && !(draftFrom() && !draftTo())) {
                // Fresh selection: this is the start, and the range is open.
                picking = true;
                preview = iso;
                setDraft(iso, '');
                return;
            }

            const start = draftFrom();
            picking = false;
            preview = '';
            setDraft(iso < start ? iso : start, iso < start ? start : iso);
        }, { signal });

        calendar.addEventListener('mouseover', (e) => {
            const day = e.target.closest('.cal-day');
            if (!day || !picking) return;
            preview = day.dataset.iso;
            paintCalendar();
        }, { signal });

        calendar.addEventListener('keydown', (e) => {
            const day = e.target.closest('.cal-day');
            if (!day) return;

            const step = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 }[e.key];
            if (!step) return;
            e.preventDefault();

            const next = parseIso(day.dataset.iso);
            next.setDate(next.getDate() + step);

            // Follow the focus into the next month when it walks off the edge.
            if (next.getMonth() !== calCursor.getMonth() || next.getFullYear() !== calCursor.getFullYear()) {
                calCursor = new Date(next.getFullYear(), next.getMonth(), 1);
                buildCalendar();
            }

            const target = $('.cal-day[data-iso="' + isoLocal(next) + '"]', calendar);
            if (target) target.focus();
        }, { signal });
    }

    if (dateModal) {
        dateModal.addEventListener('click', (e) => {
            const preset = e.target.closest('.date-preset');
            if (preset) {
                picking = false;
                preview = '';

                const key = preset.dataset.preset;
                if (key === 'all') { setDraft('', ''); return; }

                const to = todayIso();
                const from = key === 'year'
                    ? new Date().getFullYear() + '-01-01'
                    : isoDaysAgo(PRESETS[key] || 0);

                calCursor = parseIso(to);
                buildCalendar();
                setDraft(from, to);
                return;
            }

            if (e.target.closest('[data-date-clear]')) {
                picking = false;
                preview = '';
                setDraft('', '');
                commitDates('', '');
                return;
            }

            if (e.target.closest('[data-date-apply]')) {
                commitDates(draftFrom(), draftTo());
            }
        }, { signal });
    }

    function commitDates(from, to) {
        state.from = from;
        state.to = to;
        state.page = 1;

        if (dateModal && typeof bootstrap !== 'undefined') {
            const modal = bootstrap.Modal.getInstance(dateModal);
            if (modal) modal.hide();
        }

        paintDateChrome();
        paintFilterChrome();
        load();
    }

    // ----------------------------------------------------------------------
    // Boot
    //
    // The first page is already on screen from the server, so nothing is
    // fetched here: the timestamps are converted in place and the head and
    // pager are drawn from the counts the template published on #activityList.
    // The list is only re-fetched once a filter, a search or a page asks for it.
    // ----------------------------------------------------------------------

    paintTimes();
    paintHead();
    paintPager();
    paintDateChrome();
    paintFilterChrome();

    // syncThemeControls only runs on DOMContentLoaded, which PJAX never fires.
    if (typeof window.syncThemeControls === 'function') window.syncThemeControls();
})();
