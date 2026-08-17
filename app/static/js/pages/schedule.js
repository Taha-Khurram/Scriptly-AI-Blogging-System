/**
 * Schedule — week · month · upcoming.
 *
 * The version this replaces declared five globals (window._scheduleOpenReschedule
 * and friends) so that markup it had built by string concatenation could reach
 * them from inline `onclick` attributes, and it interpolated blog titles into
 * those attributes through a `div.textContent -> div.innerHTML` escaper, which
 * encodes &, < and > and leaves both quote characters alone. A title containing
 * a double quote closed the attribute.
 *
 * Everything here is one IIFE, every control is reached by delegation off
 * .dashboard-main, and the escaper covers all five characters Jinja's autoescape
 * does. Listeners and in-flight fetches hang off an AbortController the next run
 * aborts — PJAX re-injects this file on every visit to /schedule, so anything
 * bound to `document` at module scope would otherwise accumulate one copy per
 * visit and a late response could land in a screen that is no longer there.
 */

(function schedulePage() {
    'use strict';

    if (window.__scheduleAbort) {
        try { window.__scheduleAbort.abort(); } catch (e) { /* already gone */ }
    }
    const controller = new AbortController();
    const signal = controller.signal;
    window.__scheduleAbort = controller;

    const root = document.querySelector('.dashboard-main');
    if (!root) return;

    const $ = (sel, scope) => (scope || root).querySelector(sel);
    const $$ = (sel, scope) => Array.from((scope || root).querySelectorAll(sel));

    const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    const MONTH_CAP = 3;          // chips drawn in a month cell before "+n more"
    const WEEKS_BACK = 8;         // sparkline window
    const DAY_MS = 86400000;

    // The publisher runs on a 60s interval, so a minute or two behind schedule is
    // the job waiting its turn. Beyond that it did not run, and the entry is a
    // failure the screen has to say out loud rather than draw as a future post.
    const OVERDUE_GRACE_MS = 3 * 60 * 1000;

    const state = {
        entries: [],          // [{ id, title, category, author, status, date }]
        byId: Object.create(null),
        loaded: false,
        view: 'week',
        anchor: startOfDay(new Date()),
        query: '',
        bestTimes: null,      // cached across modal opens within one page visit
        available: [],        // the add-modal's candidate posts
        availableLoaded: false,
        chosenId: null,
        pendingId: null,      // the entry a confirmation dialog is about to act on
        expanded: new Set()   // month cells the reader opened past the +n cap
    };

    // ------------------------------------------------------------------
    // Helpers
    // ------------------------------------------------------------------

    // All five characters, because every row below builds attributes. The
    // textContent/innerHTML trick leaves " and ' alone.
    function esc(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&#34;')
            .replace(/'/g, '&#39;');
    }

    function toast(type, title, message) {
        if (typeof window.showToast === 'function') {
            window.showToast({ type, title, message, duration: type === 'error' ? 5000 : 3000 });
        }
    }

    function bsModal(id) {
        const el = document.getElementById(id);
        if (!el || typeof bootstrap === 'undefined') return null;
        return bootstrap.Modal.getOrCreateInstance(el);
    }

    function hideModal(id) {
        const m = bsModal(id);
        if (m) m.hide();
    }

    function startOfDay(d) {
        const x = new Date(d);
        x.setHours(0, 0, 0, 0);
        return x;
    }

    function addDays(d, n) {
        const x = new Date(d);
        x.setDate(x.getDate() + n);
        return x;
    }

    function startOfWeek(d) {
        const x = startOfDay(d);
        x.setDate(x.getDate() - x.getDay());
        return x;
    }

    function sameDay(a, b) {
        return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() &&
            a.getDate() === b.getDate();
    }

    // Local wall-clock, never toISOString(): the latter converts to UTC first, so
    // anywhere behind UTC the <input min> would land on yesterday for most of the
    // day and the field would reject a legitimate time.
    function isoLocal(d) {
        const p = (n) => String(n).padStart(2, '0');
        return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) +
            'T' + p(d.getHours()) + ':' + p(d.getMinutes());
    }

    function fmtTime(d) {
        return d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
    }

    function fmtDate(d) {
        return d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
    }

    function fmtDateTime(d) {
        return d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' }) +
            ' at ' + fmtTime(d);
    }

    // "Today" / "Tomorrow" carry more than a weekday does, and are what the
    // agenda's group heads are read for.
    function dayHeading(d) {
        const today = startOfDay(new Date());
        const diff = Math.round((startOfDay(d) - today) / DAY_MS);
        if (diff === 0) return 'Today';
        if (diff === 1) return 'Tomorrow';
        if (diff === -1) return 'Yesterday';
        return d.toLocaleDateString(undefined, { weekday: 'long' });
    }

    function relative(d) {
        const ms = d - new Date();
        const abs = Math.abs(ms);
        const mins = Math.round(abs / 60000);
        if (mins < 1) return ms >= 0 ? 'any moment' : 'just now';

        let value, unit;
        if (mins < 60) { value = mins; unit = 'minute'; }
        else if (abs < DAY_MS) { value = Math.round(abs / 3600000); unit = 'hour'; }
        else if (abs < 30 * DAY_MS) { value = Math.round(abs / DAY_MS); unit = 'day'; }
        else if (abs < 365 * DAY_MS) { value = Math.round(abs / (30 * DAY_MS)); unit = 'month'; }
        else { value = Math.round(abs / (365 * DAY_MS)); unit = 'year'; }

        if (typeof Intl !== 'undefined' && Intl.RelativeTimeFormat) {
            const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });
            return rtf.format(ms >= 0 ? value : -value, unit);
        }
        const plural = value === 1 ? unit : unit + 's';
        return ms >= 0 ? 'in ' + value + ' ' + plural : value + ' ' + plural + ' ago';
    }

    function stateOf(entry) {
        if (entry.status === 'PUBLISHED') return 'published';
        if (entry.date.getTime() < Date.now() - OVERDUE_GRACE_MS) return 'overdue';
        return 'scheduled';
    }

    function isQueued(entry) {
        return entry.status !== 'PUBLISHED';
    }

    function entriesOn(day) {
        return state.entries.filter((e) => sameDay(e.date, day));
    }

    // ------------------------------------------------------------------
    // Data
    // ------------------------------------------------------------------

    function setBodyState(next, message) {
        const body = $('.sched-body');
        if (!body) return;
        body.dataset.state = next;
        if (next === 'error' && message) {
            const el = $('[data-error-text]', body);
            if (el) el.textContent = message;
        }
    }

    function load() {
        setBodyState('loading');

        fetch('/api/schedule/list', { signal })
            .then((res) => res.json())
            .then((data) => {
                if (!data.success) throw new Error(data.error || 'The server could not read your schedule.');

                state.entries = (data.blogs || [])
                    .map((b) => {
                        const date = new Date(b.scheduled_at);
                        if (isNaN(date.getTime())) return null;
                        return {
                            id: b.id,
                            title: b.title || 'Untitled',
                            category: b.category || '',
                            author: b.author || '',
                            status: b.status || 'SCHEDULED',
                            date: date
                        };
                    })
                    .filter(Boolean)
                    .sort((a, b) => a.date - b.date);

                state.byId = Object.create(null);
                state.entries.forEach((e) => { state.byId[e.id] = e; });
                state.loaded = true;

                refreshStats();
                render();
            })
            .catch((err) => {
                // A PJAX navigation aborts the request on the way out. That is not
                // a failure and must not be reported as one.
                if (err.name === 'AbortError') return;
                setBodyState('error', err.message || 'Could not reach the server.');
            });
    }

    // ------------------------------------------------------------------
    // Stat tiles
    // ------------------------------------------------------------------

    function sparkline(values, label) {
        // PAD clears the endpoint marker, not just the line: the dot is r3.5 with
        // a 2px ring, so it reaches 4.5px past its centre.
        const W = 116, H = 40, PAD = 6;
        const n = values.length;
        if (n < 2) return '';

        const max = Math.max.apply(null, values);
        const min = Math.min.apply(null, values);
        const span = max - min || 1;
        const stepX = (W - PAD * 2) / (n - 1);

        const pts = values.map((v, i) => {
            const x = PAD + i * stepX;
            // A flat series sits on the baseline rather than halfway up the box,
            // so "nothing happened" cannot read as "steady at some level".
            const y = max === min
                ? (max === 0 ? H - PAD : H / 2)
                : H - PAD - ((v - min) / span) * (H - PAD * 2);
            return [x, y];
        });

        const line = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
        const area = line + ' L' + pts[n - 1][0].toFixed(1) + ' ' + (H - PAD) +
            ' L' + pts[0][0].toFixed(1) + ' ' + (H - PAD) + ' Z';
        const last = pts[n - 1];

        // A micro-chart has no room for axes, so the series gets a text equivalent
        // rather than depending on a tooltip nobody may reach.
        const summary = label + ': ' + n + ' weeks, from ' + min + ' to ' + max +
            ' per week, most recent ' + values[n - 1] + '.';

        return '<svg class="stat-trend" viewBox="0 0 ' + W + ' ' + H + '" role="img" ' +
            'aria-label="' + esc(summary) + '">' +
            '<title>' + esc(summary) + '</title>' +
            '<path class="stat-trend-area" d="' + area + '"/>' +
            '<path class="stat-trend-line" d="' + line + '"/>' +
            '<circle class="stat-trend-dot" cx="' + last[0].toFixed(1) + '" cy="' +
            last[1].toFixed(1) + '" r="3.5"/>' +
            '</svg>';
    }

    function refreshStats() {
        const now = new Date();
        const queued = state.entries.filter(isQueued);
        const overdue = queued.filter((e) => stateOf(e) === 'overdue');
        const ahead = queued.filter((e) => stateOf(e) !== 'overdue');
        // Counted off `queued`, not `ahead`: an overdue post is by definition
        // inside the next seven days — it should already have gone out. Excluding
        // it from the numerator while leaving it in the denominator made the meter
        // read low by exactly the size of the backlog.
        const within7 = queued.filter((e) => e.date - now <= 7 * DAY_MS);
        const published = state.entries.filter((e) => e.status === 'PUBLISHED');
        const last30 = published.filter((e) => now - e.date <= 30 * DAY_MS);

        // --- Queued -----------------------------------------------------
        const queuedEl = $('[data-stat-queued]');
        if (queuedEl) {
            queuedEl.textContent = queued.length;
            queuedEl.classList.toggle('is-overdue', overdue.length > 0);
            queuedEl.title = queued.length + ' post' + (queued.length === 1 ? '' : 's') + ' waiting';
        }
        const queuedDelta = $('[data-delta-queued]');
        if (queuedDelta) {
            if (overdue.length) {
                queuedDelta.className = 'stat-delta is-late';
                queuedDelta.innerHTML = '<i class="bi bi-exclamation-triangle-fill" aria-hidden="true"></i> ' +
                    esc(overdue.length + ' past due');
            } else if (!queued.length) {
                queuedDelta.className = 'stat-delta';
                queuedDelta.textContent = 'nothing waiting';
            } else {
                queuedDelta.className = 'stat-delta';
                queuedDelta.textContent = within7.length + ' in the next 7 days';
            }
        }

        // The share of the queue landing inside a week — a genuine ratio, and what
        // says whether the pipeline is front-loaded or running thin.
        const meter = $('[data-meter-queued]');
        if (meter) {
            const show = queued.length > 0;
            meter.hidden = !show;
            if (show) {
                const pct = Math.round((within7.length / queued.length) * 100);
                const fill = $('[data-meter-fill]', meter);
                const note = $('[data-meter-note]', meter);
                if (fill) fill.style.width = pct + '%';
                if (note) note.textContent = within7.length + ' of ' + queued.length + ' within 7 days';
            }
        }

        // --- Published --------------------------------------------------
        const pubEl = $('[data-stat-published]');
        if (pubEl) {
            pubEl.textContent = published.length;
            pubEl.title = published.length;
        }
        const pubDelta = $('[data-delta-published]');
        if (pubDelta) {
            pubDelta.className = 'stat-delta';
            pubDelta.textContent = published.length
                ? last30.length + ' in the last 30 days'
                : 'nothing published yet';
        }

        const trendSlot = $('[data-trend-published]');
        if (trendSlot) {
            if (published.length) {
                // Publishes per week over the trailing eight weeks — a real series
                // out of data already on the page, not a decoration.
                const weekStart = startOfWeek(now);
                const buckets = new Array(WEEKS_BACK).fill(0);
                published.forEach((e) => {
                    const back = Math.floor((weekStart - startOfWeek(e.date)) / (7 * DAY_MS));
                    if (back >= 0 && back < WEEKS_BACK) buckets[WEEKS_BACK - 1 - back] += 1;
                });
                trendSlot.innerHTML = sparkline(buckets, 'Posts published per week');
            } else {
                trendSlot.innerHTML = '';
            }
        }

        // --- Next publish -----------------------------------------------
        const nextEl = $('[data-stat-next]');
        const nextDelta = $('[data-delta-next]');
        const next = ahead[0] || null;
        if (nextEl && nextDelta) {
            nextEl.classList.toggle('is-overdue', overdue.length > 0);
            if (overdue.length) {
                nextEl.textContent = 'Overdue';
                nextEl.title = overdue.length + ' post' + (overdue.length === 1 ? '' : 's') + ' past due';
                nextDelta.className = 'stat-delta is-late';
                nextDelta.textContent = overdue.length + ' post' + (overdue.length === 1 ? '' : 's') +
                    ' did not go out';
            } else if (next) {
                nextEl.textContent = relative(next.date);
                nextEl.title = next.title;
                nextDelta.className = 'stat-delta';
                nextDelta.textContent = fmtDateTime(next.date);
            } else {
                nextEl.textContent = '—';
                nextEl.title = '';
                nextDelta.className = 'stat-delta';
                nextDelta.textContent = 'nothing queued';
            }
        }

        // --- Upcoming tab count -----------------------------------------
        const count = $('[data-upcoming-count]');
        if (count) count.textContent = queued.length;

        // --- Overdue banner ---------------------------------------------
        const alert = $('[data-overdue-alert]');
        if (alert) {
            alert.hidden = overdue.length === 0;
            const title = $('[data-overdue-title]', alert);
            if (title && overdue.length) {
                title.textContent = overdue.length === 1
                    ? '1 post is past its publish time'
                    : overdue.length + ' posts are past their publish time';
            }
        }
    }

    // ------------------------------------------------------------------
    // Rendering
    // ------------------------------------------------------------------

    function render() {
        if (!state.loaded) return;

        if (!state.entries.length && !state.query) {
            setBodyState('empty');
            syncToolbar();
            return;
        }

        setBodyState('results');
        syncToolbar();

        const panel = $('[data-view-panel]');
        if (!panel) return;

        if (state.query) {
            panel.innerHTML = renderSearch();
            return;
        }

        if (state.view === 'month') panel.innerHTML = renderMonth();
        else if (state.view === 'upcoming') panel.innerHTML = renderAgenda();
        else panel.innerHTML = renderWeek();
    }

    function syncToolbar() {
        $$('[data-view-tab]').forEach((tab) => {
            const on = tab.dataset.viewTab === state.view;
            tab.classList.toggle('is-active', on);
            tab.setAttribute('aria-selected', on ? 'true' : 'false');
        });

        // Upcoming is a list of everything ahead, so it is bounded by nothing a
        // pair of arrows could step through; a search spans all of time too.
        const nav = $('[data-range-nav]');
        if (nav) nav.hidden = state.view === 'upcoming' || !!state.query;

        const title = $('[data-range-title]');
        if (title) title.textContent = rangeTitle();

        const note = $('[data-search-note]');
        if (note) note.hidden = !state.query;
    }

    function rangeTitle() {
        if (state.view === 'month') {
            return state.anchor.toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
        }
        const start = startOfWeek(state.anchor);
        const end = addDays(start, 6);
        const sameMonth = start.getMonth() === end.getMonth();
        const left = start.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
        const right = sameMonth
            ? end.getDate()
            : end.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
        return left + ' – ' + right + ', ' + end.getFullYear();
    }

    // --- The action menu, identical in all three views ------------------
    // The whole event is the trigger. The old chip hid a ⋮ button behind :hover,
    // which is unreachable on touch and invisible to anyone who does not already
    // know it is there.
    function menuFor(entry) {
        const id = esc(entry.id);
        return '<ul class="dropdown-menu dropdown-menu-end sched-menu">' +
            '<li><p class="sched-menu-head">' + esc(entry.title) + '</p></li>' +
            '<li><button type="button" class="dropdown-item" data-act="reschedule" data-id="' + id + '">' +
            '<i class="bi bi-calendar-event" aria-hidden="true"></i> Move to another time</button></li>' +
            '<li><button type="button" class="dropdown-item" data-act="publish" data-id="' + id + '">' +
            '<i class="bi bi-send" aria-hidden="true"></i> Publish now</button></li>' +
            '<li><hr class="dropdown-divider"></li>' +
            '<li><button type="button" class="dropdown-item is-danger" data-act="cancel" data-id="' + id + '">' +
            '<i class="bi bi-arrow-counterclockwise" aria-hidden="true"></i> Move back to drafts</button></li>' +
            '</ul>';
    }

    // A published entry has no actions left, so it gets no trigger — the
    // affordance matches the capability rather than opening a menu with one
    // disabled row in it.
    function eventCard(entry) {
        const st = stateOf(entry);
        const time = fmtTime(entry.date);
        const glyph = st === 'published' ? '<i class="bi bi-check2" aria-hidden="true"></i> '
            : st === 'overdue' ? '<i class="bi bi-exclamation-triangle-fill" aria-hidden="true"></i> '
                : '';
        const inner = '<span class="sched-event-time">' + glyph + esc(time) + '</span>' +
            '<span class="sched-event-title">' + esc(entry.title) + '</span>';

        if (st === 'published') {
            return '<div class="sched-event is-published" title="' +
                esc(entry.title + ' — published ' + fmtDateTime(entry.date)) + '">' + inner + '</div>';
        }

        const label = entry.title + ' — ' + (st === 'overdue' ? 'was due ' : 'publishes ') +
            fmtDateTime(entry.date) + '. Open actions.';
        return '<div class="dropdown sched-menu-wrap">' +
            '<button type="button" class="sched-event is-' + st + '" data-bs-toggle="dropdown" ' +
            'data-bs-offset="0,4" aria-expanded="false" aria-label="' + esc(label) + '">' + inner +
            '</button>' + menuFor(entry) + '</div>';
    }

    function chipFor(entry) {
        const st = stateOf(entry);
        const time = fmtTime(entry.date).replace(/:00(?=\s|$)/, '');
        const inner = '<span class="sched-chip-time">' + esc(time) + '</span>' +
            '<span class="sched-chip-title">' + esc(entry.title) + '</span>';

        if (st === 'published') {
            return '<div class="sched-chip is-published" title="' +
                esc(entry.title + ' — published ' + fmtDateTime(entry.date)) + '">' + inner + '</div>';
        }

        const label = entry.title + ' — ' + (st === 'overdue' ? 'was due ' : 'publishes ') +
            fmtDateTime(entry.date) + '. Open actions.';
        return '<div class="dropdown sched-menu-wrap">' +
            '<button type="button" class="sched-chip is-' + st + '" data-bs-toggle="dropdown" ' +
            'data-bs-offset="0,4" aria-expanded="false" aria-label="' + esc(label) + '">' + inner +
            '</button>' + menuFor(entry) + '</div>';
    }

    // --- Week ----------------------------------------------------------
    function renderWeek() {
        const start = startOfWeek(state.anchor);
        const today = startOfDay(new Date());
        let total = 0;
        let html = '<div class="sched-week">';

        for (let i = 0; i < 7; i++) {
            const day = addDays(start, i);
            const list = entriesOn(day);
            total += list.length;

            const cls = ['sched-day'];
            if (sameDay(day, today)) cls.push('is-today');
            else if (day < today) cls.push('is-past');
            if (!list.length) cls.push('is-empty');

            html += '<div class="' + cls.join(' ') + '">' +
                '<div class="sched-day-head">' +
                '<span class="sched-day-name">' + DOW[day.getDay()] + '</span>' +
                '<span class="sched-day-num">' + day.getDate() + '</span>' +
                '</div>' +
                '<div class="sched-day-body">' + list.map(eventCard).join('') + '</div>' +
                '</div>';
        }
        html += '</div>';

        if (!total) {
            html += '<p class="sched-note">No posts scheduled for ' + esc(rangeTitle()) + '.</p>';
        }
        return html;
    }

    // --- Month ---------------------------------------------------------
    function renderMonth() {
        const first = new Date(state.anchor.getFullYear(), state.anchor.getMonth(), 1);
        const gridStart = startOfWeek(first);
        const today = startOfDay(new Date());
        const month = first.getMonth();

        let html = '<div class="sched-month">' +
            '<div class="sched-month-dow">' +
            DOW.map((d) => '<span>' + d + '</span>').join('') +
            '</div><div class="sched-month-grid">';

        // Six full weeks always, so the card does not change height between a
        // month that spills over five rows and one that spills over six.
        for (let i = 0; i < 42; i++) {
            const day = addDays(gridStart, i);
            const list = entriesOn(day);
            const cls = ['sched-month-cell'];
            if (day.getMonth() !== month) cls.push('is-outside');
            if (sameDay(day, today)) cls.push('is-today');

            // Kept in state rather than in the DOM alone, so the minute tick can
            // repaint without collapsing a cell the reader has opened.
            const key = startOfDay(day).getTime();
            const open = state.expanded.has(key);

            const chips = list.map((entry, at) => {
                const hidden = (at >= MONTH_CAP && !open) ? ' data-overflow hidden' : '';
                return '<div class="sched-month-slot"' + hidden + '>' + chipFor(entry) + '</div>';
            }).join('');

            const more = (list.length > MONTH_CAP && !open)
                ? '<button type="button" class="sched-more" data-action="expand-day" data-day="' + key +
                '">+' + (list.length - MONTH_CAP) + ' more</button>'
                : '';

            html += '<div class="' + cls.join(' ') + '">' +
                '<span class="sched-month-num">' + day.getDate() + '</span>' +
                '<div class="sched-month-events">' + chips + more + '</div>' +
                '</div>';
        }

        html += '</div></div>';
        return html;
    }

    // --- Agenda --------------------------------------------------------
    // Upcoming is what goes out next, so it carries the queue and nothing else:
    // a published post is finished, and the week and month views are where its
    // record lives.
    function renderAgenda() {
        const queued = state.entries.filter(isQueued);
        const overdue = queued.filter((e) => stateOf(e) === 'overdue');
        const ahead = queued.filter((e) => stateOf(e) !== 'overdue');

        if (!queued.length) {
            return '<div class="list-empty">' +
                '<span class="list-empty-icon"><i class="bi bi-inbox" aria-hidden="true"></i></span>' +
                '<p>Nothing is waiting to publish. Everything you have scheduled has already gone out — ' +
                'the week and month views still hold the record.</p>' +
                '<button type="button" class="app-btn is-primary" data-action="open-add">' +
                '<i class="bi bi-calendar-plus" aria-hidden="true"></i> Schedule a blog</button>' +
                '</div>';
        }

        let html = '<div class="sched-agenda">';
        if (overdue.length) {
            html += group('Past due', 'these did not publish', overdue, true);
        }
        html += groupByDay(ahead);
        html += '</div>';
        return html;
    }

    function renderSearch() {
        const q = state.query;
        const matches = state.entries.filter((e) =>
            (e.title + ' ' + e.category + ' ' + e.author).toLowerCase().indexOf(q) !== -1);

        const note = $('[data-search-text]');
        if (note) {
            note.textContent = matches.length
                ? matches.length + (matches.length === 1 ? ' post' : ' posts') + ' matching “' +
                state.query + '”, across the whole schedule'
                : 'Nothing on the schedule matches “' + state.query + '”';
        }

        if (!matches.length) {
            return '<div class="list-empty">' +
                '<span class="list-empty-icon"><i class="bi bi-search" aria-hidden="true"></i></span>' +
                '<p>No scheduled or published post matches that. Search covers the title, the category ' +
                'and the author.</p></div>';
        }

        return '<div class="sched-agenda">' + groupByDay(matches, true) + '</div>';
    }

    function groupByDay(list, withDate) {
        if (!list.length) return '';
        const out = [];
        let current = null;
        let bucket = [];

        list.forEach((entry) => {
            const key = startOfDay(entry.date).getTime();
            if (key !== current) {
                if (bucket.length) out.push(dayGroup(new Date(current), bucket, withDate));
                current = key;
                bucket = [];
            }
            bucket.push(entry);
        });
        if (bucket.length) out.push(dayGroup(new Date(current), bucket, withDate));
        return out.join('');
    }

    function dayGroup(day, list, withDate) {
        return group(dayHeading(day), fmtDate(day), list, withDate);
    }

    function group(heading, sub, list, withDate) {
        return '<section class="sched-agenda-group">' +
            '<h3 class="sched-agenda-day">' + esc(heading) +
            '<span class="sched-agenda-date">' + esc(sub) + '</span></h3>' +
            '<div class="data-rows">' + list.map((e) => agendaRow(e, withDate)).join('') + '</div>' +
            '</section>';
    }

    function agendaRow(entry, withDate) {
        const st = stateOf(entry);
        // Split so the meridiem can sit under the clock rather than beside it.
        const parts = fmtTime(entry.date).split(/\s+/);
        const hm = parts[0] || '';
        const ap = parts[1] || '';

        const meta = [];
        if (entry.category) meta.push('<span>' + esc(entry.category) + '</span>');
        if (entry.author) meta.push('<span>' + esc(entry.author) + '</span>');
        if (withDate) meta.push('<span class="row-time">' + esc(fmtDate(entry.date)) + '</span>');
        if (!withDate && st === 'scheduled') {
            meta.push('<span class="row-time">' + esc(relative(entry.date)) + '</span>');
        }

        const pill = st === 'published'
            ? '<span class="status-pill status-published">Published</span>'
            : st === 'overdue'
                ? '<span class="status-pill status-overdue">Past due</span>'
                : '<span class="status-pill status-scheduled">Scheduled</span>';

        const trail = st === 'published' ? pill : pill +
            '<div class="dropdown sched-menu-wrap">' +
            '<button type="button" class="row-action" data-bs-toggle="dropdown" data-bs-offset="0,4" ' +
            'aria-expanded="false" aria-label="' + esc('Actions for ' + entry.title) + '">' +
            '<i class="bi bi-three-dots" aria-hidden="true"></i></button>' + menuFor(entry) + '</div>';

        return '<div class="data-row sched-row is-' + st + '">' +
            '<span class="row-mark sched-time-mark" aria-hidden="true">' +
            '<span class="sched-time-hm">' + esc(hm) + '</span>' +
            '<span class="sched-time-ap">' + esc(ap) + '</span></span>' +
            '<div class="row-main">' +
            '<span class="row-title">' + esc(entry.title) + '</span>' +
            (meta.length
                ? '<span class="row-meta">' + meta.join('<span class="row-sep">·</span>') + '</span>'
                : '') +
            '</div>' +
            '<div class="row-trail">' + trail + '</div>' +
            '</div>';
    }

    // ------------------------------------------------------------------
    // Publish-time picker — our own calendar + quarter-hour column
    //
    // Replaces <input type="datetime-local">. That control's popup is drawn by
    // the browser, so it takes none of the product's surfaces, radius or type,
    // and on a forced dark theme over a light OS it lands as a pale OS widget in
    // the middle of a dark dialog. Same reason All Blogs' date range is our own
    // grid. The chosen instant lives on a hidden input, so readWhen() does not
    // care how it was filled in.
    // ------------------------------------------------------------------

    const TIME_STEP_MIN = 15;
    const DOW_FULL = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

    // One record per picker, keyed by its data-when scope.
    const pickers = Object.create(null);

    function pickerFor(scope) {
        if (!pickers[scope]) pickers[scope] = { cursor: null, day: null, minutes: null };
        return pickers[scope];
    }

    function pickerRoot(scope) {
        return $('[data-when="' + scope + '"]');
    }

    // Local wall-clock key, never toISOString(): the latter converts to UTC
    // first, so anywhere ahead of UTC a late-evening day comes back as tomorrow.
    function dayKey(d) {
        const p = (n) => String(n).padStart(2, '0');
        return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate());
    }

    function parseDayKey(key) {
        const bits = String(key || '').split('-');
        if (bits.length !== 3) return null;
        const d = new Date(Number(bits[0]), Number(bits[1]) - 1, Number(bits[2]));
        return isNaN(d.getTime()) ? null : d;
    }

    function minutesLabel(minutes) {
        const d = new Date(2000, 0, 1, Math.floor(minutes / 60), minutes % 60);
        return fmtTime(d);
    }

    // Build and paint are separate for the same reason all_blogs' range calendar
    // splits them: rebuilding innerHTML destroys the very button the pointer is
    // over, losing :hover and losing focus. buildCal runs on a month change;
    // paintCal only rewrites class names and the disabled flag.
    function buildCal(scope) {
        const root = pickerRoot(scope);
        if (!root) return;
        const state_ = pickerFor(scope);
        const grid = $('[data-cal-grid]', root);
        const title = $('[data-cal-title]', root);
        if (!grid) return;

        const cursor = state_.cursor || startOfDay(new Date());
        if (title) {
            title.textContent = cursor.toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
        }

        const first = new Date(cursor.getFullYear(), cursor.getMonth(), 1);
        const start = addDays(startOfDay(first), -first.getDay());

        let html = '';
        for (let i = 0; i < 42; i++) {
            const day = addDays(start, i);
            html += '<div class="cal-cell" data-cell="' + dayKey(day) + '">' +
                '<button type="button" class="cal-day" data-day="' + dayKey(day) + '" ' +
                'aria-label="' + esc(day.toLocaleDateString(undefined, { dateStyle: 'full' })) + '">' +
                day.getDate() + '</button></div>';
        }
        grid.innerHTML = html;
        paintCal(scope);
    }

    function paintCal(scope) {
        const root = pickerRoot(scope);
        if (!root) return;
        const state_ = pickerFor(scope);
        const cursor = state_.cursor || startOfDay(new Date());
        const todayKey = dayKey(new Date());
        const chosen = state_.day ? dayKey(state_.day) : '';
        const month = cursor.getMonth();

        $$('.cal-cell', root).forEach((cell) => {
            const key = cell.dataset.cell;
            const day = parseDayKey(key);
            const button = cell.querySelector('.cal-day');

            cell.classList.toggle('is-outside', !day || day.getMonth() !== month);
            cell.classList.toggle('is-today', key === todayKey);
            cell.classList.toggle('is-selected', !!chosen && key === chosen);

            // A day wholly in the past can hold no future publish time.
            if (button) button.disabled = !!day && key < todayKey;
        });
    }

    function buildTimes(scope) {
        const root = pickerRoot(scope);
        if (!root) return;
        const state_ = pickerFor(scope);
        const list = $('[data-time-list]', root);
        if (!list) return;

        // Until a day is chosen there is nothing to say which slots are still
        // available, so the column asks for the day first.
        if (!state_.day) {
            list.innerHTML = '<p class="sched-times-none">Choose a day and the open slots appear here.</p>';
            return;
        }

        const isToday = sameDay(state_.day, new Date());
        const now = new Date();
        const cutoff = isToday ? now.getHours() * 60 + now.getMinutes() : -1;

        let html = '';
        let open = 0;
        for (let m = 0; m < 24 * 60; m += TIME_STEP_MIN) {
            const past = m <= cutoff;
            if (!past) open++;
            html += '<button type="button" class="sched-time' +
                (state_.minutes === m ? ' is-selected' : '') + '" role="option" ' +
                'aria-selected="' + (state_.minutes === m ? 'true' : 'false') + '" ' +
                'data-minutes="' + m + '"' + (past ? ' disabled' : '') + '>' +
                esc(minutesLabel(m)) + '</button>';
        }

        if (!open) {
            list.innerHTML = '<p class="sched-times-none">Every slot today has gone by. Pick tomorrow ' +
                'or later.</p>';
            return;
        }

        list.innerHTML = html;

        // Bring the choice — or the first slot that is still open — into view,
        // so the column does not open scrolled to midnight.
        const target = list.querySelector('.sched-time.is-selected') ||
            list.querySelector('.sched-time:not([disabled])');
        if (target && target.scrollIntoView) {
            target.scrollIntoView({ block: 'center' });
        }
    }

    function paintSummary(scope) {
        const root = pickerRoot(scope);
        if (!root) return;
        const state_ = pickerFor(scope);
        const summary = $('[data-when-summary]', root);
        const label = $('[data-when-text]', root);
        const holder = $('[data-when-value]', root);
        const when = pickerValue(scope);

        if (holder) holder.value = when ? isoLocal(when) : '';
        if (!summary || !label) return;

        summary.classList.toggle('is-set', !!when);
        if (when) {
            label.innerHTML = 'Publishes <strong>' +
                esc(DOW_FULL[when.getDay()] + ', ' + when.toLocaleDateString(undefined, {
                    day: 'numeric', month: 'long', year: 'numeric'
                }) + ' at ' + fmtTime(when)) + '</strong> — ' + esc(relative(when));
        } else if (state_.day) {
            label.textContent = 'Now pick a time on ' + fmtDate(state_.day) + '.';
        } else {
            label.textContent = 'Pick a day, then a time.';
        }
    }

    function pickerValue(scope) {
        const state_ = pickerFor(scope);
        if (!state_.day || state_.minutes === null) return null;
        const when = new Date(state_.day);
        when.setHours(Math.floor(state_.minutes / 60), state_.minutes % 60, 0, 0);
        return when;
    }

    // Opens on the instant already in force where there is one — an existing
    // schedule should be on screen rather than "today" with the real value
    // scrolled out of sight.
    function resetPicker(scope, from) {
        const state_ = pickerFor(scope);
        if (from && !isNaN(from.getTime())) {
            state_.day = startOfDay(from);
            // Snap to the step grid so the chosen row can actually be marked.
            state_.minutes = Math.round((from.getHours() * 60 + from.getMinutes()) / TIME_STEP_MIN) *
                TIME_STEP_MIN;
            if (state_.minutes >= 24 * 60) state_.minutes = 24 * 60 - TIME_STEP_MIN;
            state_.cursor = new Date(state_.day.getFullYear(), state_.day.getMonth(), 1);
        } else {
            state_.day = null;
            state_.minutes = null;
            state_.cursor = startOfDay(new Date());
        }
        buildCal(scope);
        buildTimes(scope);
        paintSummary(scope);
    }

    /* ------------------------------------------------------------------
       Best-time suggestions — WITHDRAWN, kept whole as the restore point.

       The AI Publish Time Agent panel is commented out of both dialogs (see the
       matching note in schedule.html). GET /api/schedule/best-time is untouched
       and still serves drafts.js and approval.js — only this screen stopped
       calling it.

       Commented rather than left live-but-unreachable: unreferenced functions
       that still compile are the kind of thing a later reader keeps and a linter
       keeps quiet about.

       To restore, all four together:
         1. the two <div class="sched-besttime"> blocks in schedule.html
         2. this block
         3. the two `loadBestTimes(...)` calls marked "withdrawn" in openAdd()
            and openReschedule()
         4. an `apply-slot` case in the [data-action] switch:
              case 'apply-slot': applySlot(button); break;
            plus the handler, which now has to write through the picker rather
            than into a datetime input that no longer exists:

              function applySlot(button) {
                  const panel = button.closest('[data-besttime]');
                  if (!panel) return;
                  resetPicker(panel.dataset.besttime, new Date(button.dataset.when));
              }

       The CSS (.sched-besttime / .sched-slots / .sched-slot) is still in
       schedule.css.
       ------------------------------------------------------------------

    const FALLBACK_SLOTS = [
        {
            day: 'Tuesday', day_index: 2, hour: 10, display_time: 'Tuesday, 10:00 AM',
            reasoning: 'Mid-morning midweek is when most blog audiences are reading'
        },
        {
            day: 'Thursday', day_index: 4, hour: 14, display_time: 'Thursday, 2:00 PM',
            reasoning: 'Thursday afternoons are a second peak for most audiences'
        },
        {
            day: 'Wednesday', day_index: 3, hour: 9, display_time: 'Wednesday, 9:00 AM',
            reasoning: 'Early Wednesday catches readers checking for new content'
        }
    ];

    function nextOccurrence(dayIndex, hour) {
        const now = new Date();
        let until = dayIndex - now.getDay();
        if (until < 0) until += 7;
        if (until === 0 && hour <= now.getHours()) until = 7;
        const target = addDays(now, until);
        target.setHours(hour, 0, 0, 0);
        return target;
    }

    function renderSlots(scope, slots, source, message) {
        const list = $('[data-besttime-list]', scope);
        if (!list) return;

        let head;
        if (source === 'analytics') {
            head = '<p class="sched-besttime-source"><i class="bi bi-check-circle-fill" aria-hidden="true"></i> ' +
                'From your Google Analytics data, last 28 days</p>';
        } else if (message) {
            head = '<p class="sched-besttime-source is-warning">' +
                '<i class="bi bi-exclamation-triangle-fill" aria-hidden="true"></i> ' + esc(message) + '</p>';
        } else {
            head = '<p class="sched-besttime-source"><i class="bi bi-lightbulb-fill" aria-hidden="true"></i> ' +
                'General best practice — connect Analytics for times based on your own readers</p>';
        }

        // Each slot names the actual date it resolves to. "Tuesday, 10:00 AM" on
        // its own leaves the reader to work out which Tuesday the click means.
        list.innerHTML = head + '<div class="sched-slots">' + slots.map((s) => {
            const when = nextOccurrence(s.day_index, s.hour);
            return '<button type="button" class="sched-slot" data-action="apply-slot" ' +
                'data-when="' + esc(isoLocal(when)) + '">' +
                '<span class="sched-slot-when"><i class="bi bi-clock" aria-hidden="true"></i> ' +
                esc(s.display_time) + '</span>' +
                '<span class="sched-slot-date">' + esc(fmtDate(when)) + '</span>' +
                (s.reasoning ? '<span class="sched-slot-why">' + esc(s.reasoning) + '</span>' : '') +
                '</button>';
        }).join('') + '</div>';
    }

    function loadBestTimes(scope) {
        if (!scope) return;

        if (state.bestTimes) {
            renderSlots(scope, state.bestTimes.slots, state.bestTimes.source, state.bestTimes.message);
            return;
        }

        const list = $('[data-besttime-list]', scope);
        if (list) {
            list.innerHTML = '<p class="sched-besttime-loading">' +
                '<span class="spinner-border spinner-border-sm"></span> Reading your traffic data…</p>';
        }

        fetch('/api/schedule/best-time', { signal })
            .then((res) => res.json())
            .then((data) => {
                if (data.success && data.suggestions && data.suggestions.length) {
                    state.bestTimes = { slots: data.suggestions, source: 'analytics', message: null };
                } else {
                    state.bestTimes = {
                        slots: FALLBACK_SLOTS, source: 'fallback', message: data.message || null
                    };
                }
                renderSlots(scope, state.bestTimes.slots, state.bestTimes.source, state.bestTimes.message);
            })
            .catch((err) => {
                if (err.name === 'AbortError') return;
                state.bestTimes = { slots: FALLBACK_SLOTS, source: 'fallback', message: null };
                renderSlots(scope, FALLBACK_SLOTS, 'fallback', null);
            });
    }

       ------------------------------------------------------------------ */

    // ------------------------------------------------------------------
    // Schedule-a-blog modal
    // ------------------------------------------------------------------

    function openAdd() {
        state.chosenId = null;
        const form = $('[data-schedule-form]');
        if (form) form.hidden = true;

        const confirm = $('[data-action="confirm-add"]');
        if (confirm) confirm.disabled = true;

        const search = $('#blogSearchInput');
        if (search) search.value = '';

        const note = $('[data-add-note]');
        if (note) note.textContent = '';

        if (!state.availableLoaded) loadAvailable();
        else renderAvailable('');

        const modal = bsModal('addScheduleModal');
        if (modal) modal.show();
    }

    function loadAvailable() {
        const list = $('[data-blog-list]');
        if (list) {
            list.innerHTML = '<div class="list-empty sched-list-loading">' +
                '<span class="list-empty-icon"><span class="spinner-border spinner-border-sm"></span></span>' +
                '<p>Loading posts…</p></div>';
        }

        fetch('/api/schedule/available-blogs', { signal })
            .then((res) => res.json())
            .then((data) => {
                if (!data.success) throw new Error(data.error || 'Could not load your posts.');
                state.available = data.blogs || [];
                state.availableLoaded = true;
                renderAvailable('');
            })
            .catch((err) => {
                if (err.name === 'AbortError') return;
                if (!list) return;
                list.innerHTML = '<div class="list-empty">' +
                    '<span class="list-empty-icon"><i class="bi bi-exclamation-triangle" aria-hidden="true"></i>' +
                    '</span><p>' + esc(err.message || 'Could not reach the server.') + '</p>' +
                    '<button type="button" class="app-btn is-ghost" data-action="reload-available">Try again' +
                    '</button></div>';
            });
    }

    function renderAvailable(query) {
        const list = $('[data-blog-list]');
        if (!list) return;

        const q = (query || '').trim().toLowerCase();
        const rows = q
            ? state.available.filter((b) =>
                ((b.title || '') + ' ' + (b.author_name || '')).toLowerCase().indexOf(q) !== -1)
            : state.available;

        if (!rows.length) {
            list.innerHTML = '<div class="list-empty">' +
                '<span class="list-empty-icon"><i class="bi bi-file-earmark-x" aria-hidden="true"></i></span>' +
                '<p>' + (q
                    ? 'Nothing matches that.'
                    : 'No drafts and nothing awaiting approval. Write a post first and it will show up here.') +
                '</p></div>';
            return;
        }

        list.innerHTML = rows.map((b) => {
            const status = b.status === 'DRAFT' ? 'draft' : 'under_review';
            const label = b.status === 'DRAFT' ? 'Draft' : 'In review';
            const on = state.chosenId === b.id;
            return '<button type="button" class="sched-blog-item' + (on ? ' is-selected' : '') + '" ' +
                'role="option" aria-selected="' + (on ? 'true' : 'false') + '" ' +
                'data-action="choose-blog" data-id="' + esc(b.id) + '" data-title="' + esc(b.title) + '">' +
                '<span class="sched-blog-main">' +
                '<span class="sched-blog-title">' + esc(b.title) + '</span>' +
                (b.author_name
                    ? '<span class="sched-blog-meta">' + esc(b.author_name) + '</span>'
                    : '') +
                '</span>' +
                '<span class="status-pill status-' + status + '">' + label + '</span>' +
                '</button>';
        }).join('');
    }

    function chooseBlog(id, title) {
        state.chosenId = id;
        renderAvailable($('#blogSearchInput') ? $('#blogSearchInput').value : '');

        const form = $('[data-schedule-form]');
        if (form) form.hidden = false;

        const chosen = $('[data-chosen-blog]');
        if (chosen) {
            chosen.innerHTML = '<i class="bi bi-file-earmark-text" aria-hidden="true"></i><span>' +
                esc(title) + '</span>';
        }

        // A fresh pick, so the picker opens unset rather than holding whatever
        // the previously chosen post was pointed at.
        resetPicker('add', null);

        const confirm = $('[data-action="confirm-add"]');
        if (confirm) confirm.disabled = false;

        // loadBestTimes($('[data-besttime="add"]'));   // Publish Time Agent — withdrawn
        if (form) form.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }

    // ------------------------------------------------------------------
    // Actions
    // ------------------------------------------------------------------

    // Every write follows the same shape: disable the button, name what it is
    // doing, and put the server's own message on screen when it refuses.
    function post(url, body, button, busyLabel, onDone) {
        const original = button ? button.innerHTML : '';
        if (button) {
            button.disabled = true;
            button.innerHTML = '<span class="spinner-border spinner-border-sm"></span> ' + busyLabel;
        }
        if (typeof window.showActionLoader === 'function') window.showActionLoader(busyLabel);

        return fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body || {}),
            signal
        })
            .then((res) => res.json())
            .then((data) => {
                if (!data.success) throw new Error(data.error || 'The server refused that.');
                onDone(data);
            })
            .catch((err) => {
                if (err.name === 'AbortError') return;
                toast('error', 'Not done', err.message || 'Could not reach the server.');
            })
            .finally(() => {
                if (typeof window.hideActionLoader === 'function') window.hideActionLoader();
                if (button) {
                    button.disabled = false;
                    button.innerHTML = original;
                }
            });
    }

    function readWhen(scope) {
        const when = pickerValue(scope);
        if (!when) {
            const state_ = pickerFor(scope);
            toast('error', 'Pick a time', state_.day
                ? 'Choose a time on ' + fmtDate(state_.day) + '.'
                : 'Choose the day and time this should publish.');
            return null;
        }
        // Checked here as well as on the server, so a past time is refused before
        // the round trip rather than coming back as a 400.
        if (when.getTime() <= Date.now()) {
            toast('error', 'Pick a future time', 'A post cannot be scheduled for a moment that has passed.');
            return null;
        }
        return when;
    }

    function confirmAdd(button) {
        if (!state.chosenId) return;
        const when = readWhen('add');
        if (!when) return;

        post('/api/schedule/' + encodeURIComponent(state.chosenId),
            { scheduled_at: when.toISOString() }, button, 'Scheduling…', (data) => {
                hideModal('addScheduleModal');
                toast('success', 'Scheduled', data.message || 'It will publish ' + fmtDateTime(when) + '.');
                state.availableLoaded = false;   // the post has left the candidate list
                load();
            });
    }

    function openReschedule(entry) {
        state.pendingId = entry.id;

        const title = $('[data-reschedule-blog]');
        if (title) {
            title.innerHTML = '<i class="bi bi-file-earmark-text" aria-hidden="true"></i><span>' +
                esc(entry.title) + '</span>';
        }

        const current = $('[data-reschedule-current]');
        if (current) {
            current.innerHTML = '<i class="bi bi-clock-history" aria-hidden="true"></i> ' +
                (stateOf(entry) === 'overdue' ? 'Was due ' : 'Currently set for ') +
                esc(fmtDateTime(entry.date));
        }

        // Opens on the time it is on now, so the dialog shows the value being
        // replaced rather than an empty grid on today's month. An overdue entry
        // opens unset, since its own time is no longer offerable.
        resetPicker('reschedule', stateOf(entry) === 'overdue' ? null : entry.date);

        const modal = bsModal('rescheduleModal');
        if (modal) modal.show();
        // loadBestTimes($('[data-besttime="reschedule"]'));   // Publish Time Agent — withdrawn
    }

    function confirmReschedule(button) {
        if (!state.pendingId) return;
        const when = readWhen('reschedule');
        if (!when) return;

        post('/api/schedule/' + encodeURIComponent(state.pendingId) + '/reschedule',
            { scheduled_at: when.toISOString() }, button, 'Moving…', () => {
                hideModal('rescheduleModal');
                toast('success', 'Moved', 'It will now publish ' + fmtDateTime(when) + '.');
                load();
            });
    }

    function openPublish(entry) {
        state.pendingId = entry.id;
        const who = $('[data-publish-blog]');
        const when = $('[data-publish-when]');
        if (who) who.textContent = entry.title;
        if (when) when.textContent = fmtDateTime(entry.date);
        const modal = bsModal('publishNowModal');
        if (modal) modal.show();
    }

    function confirmPublish(button) {
        if (!state.pendingId) return;
        post('/api/schedule/' + encodeURIComponent(state.pendingId) + '/publish-now', {}, button,
            'Publishing…', () => {
                hideModal('publishNowModal');
                toast('success', 'Published', 'It is live on your site now.');
                load();
            });
    }

    function openCancel(entry) {
        state.pendingId = entry.id;
        const who = $('[data-cancel-blog]');
        const when = $('[data-cancel-when]');
        if (who) who.textContent = entry.title;
        if (when) when.textContent = fmtDateTime(entry.date);
        const modal = bsModal('cancelScheduleModal');
        if (modal) modal.show();
    }

    function confirmCancel(button) {
        if (!state.pendingId) return;
        post('/api/schedule/' + encodeURIComponent(state.pendingId) + '/cancel', {}, button,
            'Moving…', () => {
                hideModal('cancelScheduleModal');
                toast('warning', 'Off the schedule', 'It is back in your drafts.');
                state.availableLoaded = false;   // it is a candidate again
                load();
            });
    }

    function setView(view) {
        state.view = view;
        state.expanded.clear();
        // Stepping through weeks and then switching to Month should land on the
        // month you were looking at, so the anchor is shared between the two.
        render();
    }

    function step(direction) {
        state.anchor = state.view === 'month'
            ? new Date(state.anchor.getFullYear(), state.anchor.getMonth() + direction, 1)
            : addDays(state.anchor, direction * 7);
        state.expanded.clear();
        render();
    }

    // ------------------------------------------------------------------
    // Wiring — one delegated listener, nothing bound by name
    // ------------------------------------------------------------------

    // --- The picker: its own delegated handlers, scoped to the widget --------
    root.addEventListener('click', (e) => {
        const widget = e.target.closest('[data-when]');
        if (!widget) return;
        const scope = widget.dataset.when;
        const state_ = pickerFor(scope);

        if (e.target.closest('[data-cal-prev]') || e.target.closest('[data-cal-next]')) {
            const dir = e.target.closest('[data-cal-next]') ? 1 : -1;
            const cursor = state_.cursor || startOfDay(new Date());
            state_.cursor = new Date(cursor.getFullYear(), cursor.getMonth() + dir, 1);
            buildCal(scope);
            return;
        }

        const day = e.target.closest('.cal-day');
        if (day && !day.disabled) {
            state_.day = parseDayKey(day.dataset.day);
            // Following the click into a spill-over day moves the month with it,
            // so the selection is never left highlighted off-grid.
            if (state_.day) {
                state_.cursor = new Date(state_.day.getFullYear(), state_.day.getMonth(), 1);
            }
            // A time that has already gone by on the newly chosen day cannot
            // stand — dropping it is what stops the summary asserting a past
            // instant the server would then refuse.
            if (state_.minutes !== null && sameDay(state_.day, new Date())) {
                const now = new Date();
                if (state_.minutes <= now.getHours() * 60 + now.getMinutes()) state_.minutes = null;
            }
            buildCal(scope);
            buildTimes(scope);
            paintSummary(scope);
            return;
        }

        const slot = e.target.closest('.sched-time');
        if (slot && !slot.disabled) {
            state_.minutes = Number(slot.dataset.minutes);
            $$('.sched-time', widget).forEach((s) => {
                const on = s === slot;
                s.classList.toggle('is-selected', on);
                s.setAttribute('aria-selected', on ? 'true' : 'false');
            });
            paintSummary(scope);
        }
    }, { signal });

    // Arrow keys walk the grid and follow the focus into the next month, the same
    // contract the range calendar honours.
    root.addEventListener('keydown', (e) => {
        const widget = e.target.closest('[data-when]');
        if (!widget) return;
        const day = e.target.closest('.cal-day');
        if (!day) return;

        const step = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 }[e.key];
        if (!step) return;
        e.preventDefault();

        const scope = widget.dataset.when;
        const state_ = pickerFor(scope);
        const from = parseDayKey(day.dataset.day);
        if (!from) return;
        const next = addDays(from, step);

        const cursor = state_.cursor || startOfDay(new Date());
        if (next.getMonth() !== cursor.getMonth() || next.getFullYear() !== cursor.getFullYear()) {
            state_.cursor = new Date(next.getFullYear(), next.getMonth(), 1);
            buildCal(scope);
        }
        const target = widget.querySelector('.cal-day[data-day="' + dayKey(next) + '"]');
        if (target) target.focus();
    }, { signal });

    root.addEventListener('click', (e) => {
        // Menu rows first: they carry both data-act and an id.
        const act = e.target.closest('[data-act]');
        if (act) {
            const entry = state.byId[act.dataset.id];
            if (!entry) return;
            if (act.dataset.act === 'reschedule') openReschedule(entry);
            else if (act.dataset.act === 'publish') openPublish(entry);
            else if (act.dataset.act === 'cancel') openCancel(entry);
            return;
        }

        const tab = e.target.closest('[data-view-tab]');
        if (tab) {
            // Any tab click leaves a search: the tabs describe the calendar, and a
            // result set spanning all of time is not one of them.
            if (state.query) clearSearch();
            setView(tab.dataset.viewTab);
            return;
        }

        const button = e.target.closest('[data-action]');
        if (!button) return;

        switch (button.dataset.action) {
            case 'prev': step(-1); break;
            case 'next': step(1); break;
            case 'today':
                state.anchor = startOfDay(new Date());
                render();
                break;
            case 'reload': load(); break;
            case 'reload-available': loadAvailable(); break;
            case 'open-add': openAdd(); break;
            case 'choose-blog':
                chooseBlog(button.dataset.id, button.dataset.title);
                break;
            case 'confirm-add': confirmAdd(button); break;
            case 'confirm-reschedule': confirmReschedule(button); break;
            case 'confirm-publish': confirmPublish(button); break;
            case 'confirm-cancel': confirmCancel(button); break;
            case 'clear-search': clearSearch(); break;
            case 'show-overdue':
                if (state.query) clearSearch();
                setView('upcoming');
                break;
            case 'expand-day': expandDay(button); break;
            default: break;
        }
    }, { signal });

    function expandDay(button) {
        const cell = button.closest('.sched-month-cell');
        if (!cell) return;
        state.expanded.add(Number(button.dataset.day));
        $$('[data-overflow]', cell).forEach((el) => {
            el.hidden = false;
            el.removeAttribute('data-overflow');
        });
        button.remove();
    }

    function clearSearch() {
        // Reuse the header's own clear control so the field, its has-value class
        // and the results panel all end up in the state the header expects.
        const clear = document.querySelector('[data-page-search-clear]');
        if (clear) clear.click();
        else { state.query = ''; render(); }
    }

    // The header field owns no behaviour: it emits `page-search` and the page
    // decides what that means. Here a query switches to the list of every match,
    // past and future — filtering a calendar in place just empties cells, and
    // "where did I schedule X" is what someone types into this box.
    document.addEventListener('page-search', (e) => {
        state.query = ((e.detail && e.detail.value) || '').trim().toLowerCase();
        render();
    }, { signal });

    const modalSearch = $('#blogSearchInput');
    if (modalSearch) {
        modalSearch.addEventListener('input', (e) => {
            renderAvailable(e.target.value);
        }, { signal });
    }

    // Clearing the pending id on dismiss stops a stale confirmation from acting on
    // whatever the previous dialog was pointed at.
    ['publishNowModal', 'cancelScheduleModal', 'rescheduleModal'].forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('hidden.bs.modal', () => { state.pendingId = null; }, { signal });
    });

    // "in 3 days" and "Past due" both go stale while the tab sits open, and the
    // publisher moves entries out of the queue on its own minute. A minute is the
    // resolution of everything on screen, so that is the refresh.
    //
    // It stands down while a menu or a dialog is open: re-rendering pulls the DOM
    // out from under whatever the reader is pointing at, and a clock is never a
    // good enough reason to close someone's menu. An expanded month cell needs no
    // such guard — state.expanded survives the repaint.
    const tick = setInterval(() => {
        if (document.hidden || !state.loaded) return;
        if (document.querySelector('.dropdown-menu.show, .modal.show')) return;
        refreshStats();
        render();
    }, 60000);
    signal.addEventListener('abort', () => clearInterval(tick));

    // ------------------------------------------------------------------
    // Both pickers are built once, up front, rather than on first open. A widget
    // that only becomes valid after some other handler has run is a widget that
    // renders an empty grid the first time anything reaches it by another path.
    resetPicker('add', null);
    resetPicker('reschedule', null);

    load();

})();
