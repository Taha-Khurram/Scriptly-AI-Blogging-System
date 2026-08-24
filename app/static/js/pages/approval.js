/**
 * Approvals — the review queue: read a submission, publish it, schedule it, or
 * send it back.
 *
 * Was eleven globals (openViewModal, openReviewModal, openScheduleModal,
 * approveBlog, rejectToDraft, checkEmptyState, loadBestTimeSuggestions,
 * renderSuggestionChips, applyBestTime, currentBlogId, currentBlogTitle) wired
 * to the rows through `onclick="openViewModal('<id>')"` strings, driving two
 * dialogs that fetched the same blog from the same endpoint and rendered it
 * into two parallel sets of element ids. Actions are delegated off the page
 * root now and read their target from data attributes on the row, which is the
 * only copy of the record.
 *
 * Two behaviours worth naming:
 *
 *   - checkEmptyState() used to look for the pagination block with
 *     `.querySelector('.p-4.border-top')` — a match on the Bootstrap utility
 *     classes the pager happened to be wearing. It also rebuilt the empty state
 *     by overwriting the container's innerHTML, which destroyed the header
 *     along with it and left nothing for a later action to remove itself from.
 *
 *   - Every action re-rendered the ⋮ button's innerHTML to show a spinner and
 *     restored it from a hardcoded icon string on failure, so a row that failed
 *     twice ended up with two nested <i> tags.
 *
 * The whole queue arrives in the first response, so search and paging happen
 * here rather than over the network: a review queue is short by definition, and
 * the old server-side pager sliced a list the route had already fetched in full.
 *
 * PJAX re-injects this file on every visit to /approval, so nothing holds a
 * reference across navigations and every document-level listener goes through
 * an AbortController the next run aborts.
 */

(function approvalPage() {
    'use strict';

    if (window.__approvalAbort) {
        try { window.__approvalAbort.abort(); } catch (e) { /* already gone */ }
    }
    const controller = new AbortController();
    const signal = controller.signal;
    window.__approvalAbort = controller;

    const $ = (sel, root) => (root || document).querySelector(sel);
    const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

    const root = $('.dashboard-main');
    const list = $('#approvalList');
    if (!root || !list) return;

    const PER_PAGE = 10;

    const state = {
        query: '',
        page: 1,
        current: null   // the submission the open dialog is about
    };

    let searchTimer = null;

    // ----------------------------------------------------------------------
    // Helpers
    // ----------------------------------------------------------------------

    // Escapes all five characters Jinja's autoescape does, not just the three
    // the `div.textContent -> div.innerHTML` trick covers: titles and author
    // names land in attributes here too, and both are typed by a person.
    function esc(str) {
        return String(str == null ? '' : str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&#34;')
            .replace(/'/g, '&#39;');
    }

    function toast(type, title, message) {
        if (typeof window.showToast === 'function') {
            window.showToast({ type, title, message });
        }
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

    // How long something has been sitting there, as a duration rather than as a
    // point in time — "4 days", not "4 days ago". The Waiting-longest tile is
    // measuring an age, and "ago" in a tile value reads as a caption.
    function age(value) {
        const then = new Date(value);
        if (isNaN(then.getTime())) return '';

        const mins = Math.max(0, Math.round((Date.now() - then.getTime()) / 60000));
        if (mins < 60) return mins <= 1 ? 'a minute' : mins + ' min';

        const hours = Math.round(mins / 60);
        if (hours < 24) return hours === 1 ? '1 hour' : hours + ' hours';

        const days = Math.round(hours / 24);
        if (days < 14) return days === 1 ? '1 day' : days + ' days';

        const weeks = Math.round(days / 7);
        if (weeks < 9) return weeks + ' weeks';
        return Math.round(days / 30) + ' months';
    }

    function shortDate(value) {
        const d = new Date(value);
        if (isNaN(d.getTime())) return '';
        return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
    }

    function longDate(value) {
        const d = new Date(value);
        if (isNaN(d.getTime())) return '';
        return d.toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' }) +
            ' at ' + d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
    }

    function rows() {
        return $$('.queue-row', list);
    }

    function rowById(id) {
        return $('#row-' + CSS.escape(String(id)), list);
    }

    function openModal(id) {
        const el = document.getElementById(id);
        if (!el || typeof bootstrap === 'undefined') return null;
        const modal = bootstrap.Modal.getInstance(el) || new bootstrap.Modal(el);
        modal.show();
        return modal;
    }

    function closeModal(id) {
        const el = document.getElementById(id);
        if (!el || typeof bootstrap === 'undefined') return;
        const modal = bootstrap.Modal.getInstance(el);
        if (modal) modal.hide();
    }

    // Preview -> Schedule is one gesture, but two Bootstrap dialogs. Showing the
    // second while the first is still running its hide transition leaves the
    // backdrop of the one that is going away stacked over the one arriving, and
    // `modal-open` gets removed from <body> by the late `hidden` event — so the
    // page underneath scrolls behind a dialog that is still up. Waiting for the
    // first to finish costs one transition and cannot deadlock: if the dialog is
    // not actually open, `next` runs immediately.
    function afterClose(id, next) {
        const el = document.getElementById(id);
        if (!el || !el.classList.contains('show') || typeof bootstrap === 'undefined') {
            next();
            return;
        }
        el.addEventListener('hidden.bs.modal', next, { once: true });
        closeModal(id);
    }

    // A row is the record. Reading the dialogs' contents out of the DOM rather
    // than from a parallel cache means there is only one thing that can be
    // stale, and removing the row removes it.
    function submissionFrom(row) {
        if (!row) return null;
        return {
            id: row.dataset.id,
            title: row.dataset.title || 'Untitled',
            author: row.dataset.author || '',
            category: row.dataset.category || '',
            submitted: row.dataset.submitted || '',
            requested: row.dataset.requested || ''
        };
    }

    // ----------------------------------------------------------------------
    // Timestamps
    // ----------------------------------------------------------------------

    function paintTimes() {
        $$('time[data-relative]', list).forEach((el) => {
            const iso = el.getAttribute('datetime');
            const text = relative(iso);
            if (!text) return;
            el.title = longDate(iso);
            el.textContent = text;
        });

        $$('time[data-day]', list).forEach((el) => {
            const iso = el.getAttribute('datetime');
            const text = shortDate(iso);
            if (!text) return;
            el.title = 'Requested for ' + longDate(iso);
            el.textContent = text;
        });
    }

    // ----------------------------------------------------------------------
    // Stats
    //
    // Recomputed from the rows still on the page rather than re-fetched, so the
    // tiles and the list can never disagree after a publish or a rejection. The
    // old page left all four figures frozen at whatever the server rendered.
    // ----------------------------------------------------------------------

    function paintStats() {
        const all = rows();
        const total = all.length;
        const requested = all.filter((r) => r.dataset.requested).length;

        const totalEl = $('[data-stat-total]', root);
        if (totalEl) totalEl.textContent = String(total);

        const requestedEl = $('[data-stat-requested]', root);
        if (requestedEl) requestedEl.textContent = String(requested);

        const share = total ? Math.round((requested / total) * 100) : 0;
        const meter = $('[data-meter]', root);
        if (meter) meter.hidden = total === 0;
        const fill = $('[data-meter-fill]', root);
        if (fill) fill.style.width = share + '%';
        const note = $('[data-meter-note]', root);
        if (note) note.textContent = share + '% of the queue';

        const waiting = $('[data-stat-waiting]', root);
        if (waiting) {
            // The oldest submission still on the page, not the one the server
            // measured: publishing the one that had been waiting longest should
            // move this tile, and it is the whole reason to look at it.
            const oldest = all
                .map((r) => r.dataset.submitted)
                .filter(Boolean)
                .sort()[0] || '';
            const text = oldest ? age(oldest) : '';
            waiting.textContent = text || '—';
            waiting.title = oldest ? 'Submitted ' + longDate(oldest) : '';
            waiting.classList.toggle('is-empty', !text);
        }
    }

    // ----------------------------------------------------------------------
    // Search, paging and the card head
    //
    // One pass: the search decides which rows are eligible, the pager windows
    // what is left, and the head reports both. Anything that changes the list —
    // typing, paging, publishing, rejecting — ends here.
    // ----------------------------------------------------------------------

    function paint() {
        const all = rows();
        const matches = state.query
            ? all.filter((r) => (r.dataset.search || '').includes(state.query))
            : all;

        const pages = Math.max(1, Math.ceil(matches.length / PER_PAGE));
        if (state.page > pages) state.page = pages;

        const first = (state.page - 1) * PER_PAGE;
        const window_ = matches.slice(first, first + PER_PAGE);
        const visible = new Set(window_);

        all.forEach((row) => { row.hidden = !visible.has(row); });

        // Only when a search excluded everything. An empty *queue* already has
        // its own empty state inside the list, and printing both would tell the
        // reader twice that there is nothing here for two different reasons.
        const noresults = $('[data-noresults]', root);
        if (noresults) noresults.hidden = !(state.query && all.length > 0 && matches.length === 0);

        const count = $('[data-list-count]', root);
        if (count) count.textContent = String(matches.length);

        const note = $('[data-list-note]', root);
        if (note) {
            const parts = [];
            if (state.query) parts.push('Matching “' + state.query + '”');
            if (pages > 1) parts.push('Page ' + state.page + ' of ' + pages);
            note.textContent = parts.join(' · ');
        }

        paintPager(pages);
    }

    // A window of three around the current page, with the first and last always
    // reachable — the same shape leads.js draws.
    function paintPager(pages) {
        const nav = $('[data-pager]', root);
        if (!nav) return;

        if (pages <= 1) {
            nav.innerHTML = '';
            return;
        }

        const wanted = new Set([1, pages, state.page]);
        if (state.page - 1 > 1) wanted.add(state.page - 1);
        if (state.page + 1 < pages) wanted.add(state.page + 1);

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

    // A row leaves the queue for good once it is published, scheduled or sent
    // back — none of the three is still "under review". The empty state is the
    // markup the template already ships, restated once here rather than
    // rebuilt by overwriting the container.
    function removeRow(id) {
        const row = rowById(id);
        if (!row) return;

        row.style.transition = 'opacity var(--dur-base) var(--ease-standard)';
        row.style.opacity = '0';

        window.setTimeout(() => {
            row.remove();

            if (rows().length === 0) {
                list.innerHTML =
                    '<div class="list-empty">' +
                    '<span class="list-empty-icon"><i class="bi bi-check2-all" aria-hidden="true"></i></span>' +
                    '<p>Nothing is waiting on you. Posts your writers submit for review arrive here.</p>' +
                    '</div>';
            }

            paintStats();
            paint();
        }, 200);
    }

    document.addEventListener('page-search', (e) => {
        const value = ((e.detail && e.detail.value) || '').trim().toLowerCase();

        // The no-change check belongs inside the timer, not before it: typing a
        // letter and deleting it again lands back on the current query but
        // leaves a scheduled repaint for the letter behind.
        clearTimeout(searchTimer);
        searchTimer = window.setTimeout(() => {
            if (value === state.query) return;
            state.query = value;
            state.page = 1;
            paint();
        }, 200);
    }, { signal });

    // ----------------------------------------------------------------------
    // Writes
    //
    // One place that talks to the status endpoint, so publishing from the row,
    // from the ⋮ menu and from inside the preview dialog are the same act with
    // the same disabled state, the same toast and the same removal.
    // ----------------------------------------------------------------------

    function busy(id, on) {
        const row = rowById(id);
        if (!row) return;
        row.classList.toggle('is-busy', on);
        $$('button', row).forEach((btn) => { btn.disabled = on; });
    }

    async function setStatus(id, status, copy) {
        busy(id, true);
        if (typeof window.showActionLoader === 'function') window.showActionLoader(copy.loading);

        try {
            const res = await fetch('/api/update_status/' + encodeURIComponent(id), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ status }),
                signal
            });
            const data = await res.json();
            if (!data.success) throw new Error(data.error || copy.failed);

            toast(copy.tone, copy.title, copy.message);
            removeRow(id);
        } catch (error) {
            if (error.name === 'AbortError') return;
            busy(id, false);
            toast('error', copy.failedTitle, error.message || copy.failed);
        } finally {
            if (typeof window.hideActionLoader === 'function') window.hideActionLoader();
        }
    }

    function publish(id) {
        setStatus(id, 'PUBLISHED', {
            loading: 'Publishing…',
            tone: 'success',
            title: 'Published',
            message: 'It is live on your site now.',
            failedTitle: 'Not published',
            failed: 'The blog could not be published.'
        });
    }

    function reject(id) {
        setStatus(id, 'DRAFT', {
            loading: 'Sending back…',
            tone: 'warning',
            title: 'Sent back to drafts',
            message: 'The writer can pick it up and resubmit it.',
            failedTitle: 'Not sent back',
            failed: 'The blog could not be moved back to drafts.'
        });
    }

    // ----------------------------------------------------------------------
    // Preview
    // ----------------------------------------------------------------------

    const preview = document.getElementById('previewModal');

    function previewSet(sel, text) {
        const el = $(sel, preview);
        if (el) el.textContent = text;
    }

    function countWords(html) {
        const text = String(html || '').replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
        return text ? text.split(' ').length : 0;
    }

    // The API hands content back as either a string or an object carrying
    // `html` / `body`, depending on how the post was made.
    function bodyHtml(content) {
        if (!content) return '';
        if (typeof content === 'string') return content;
        return content.html || content.body || '';
    }

    // The contents block, from either the structured list or the pre-rendered
    // fragment — both shapes exist in the database.
    function tocHtml(content) {
        if (!content || typeof content !== 'object') return '';

        if (Array.isArray(content.toc) && content.toc.length) {
            return '<ul>' + content.toc.map((item) =>
                '<li class="toc-level-' + esc(item.level) + '">' +
                '<a href="#' + esc(item.slug) + '">' + esc(item.text) + '</a></li>'
            ).join('') + '</ul>';
        }
        return content.toc_html || '';
    }

    async function openPreview(row) {
        const item = submissionFrom(row);
        if (!item || !preview) return;
        if (typeof window.closeAllDropdowns === 'function') window.closeAllDropdowns();

        state.current = item;

        // Everything the row already knows goes up before the fetch, so the
        // dialog opens with the submission in it rather than with five dashes.
        previewSet('[data-preview-title]', item.title);
        previewSet('[data-preview-author]', item.author || 'Unknown author');
        previewSet('[data-preview-category]', item.category || 'Uncategorized');
        previewSet('[data-preview-reading]', '—');
        previewSet('[data-preview-words]', '—');
        previewSet('[data-preview-submitted]', item.submitted ? 'Submitted ' + relative(item.submitted) : '—');

        const request = $('[data-preview-request]', preview);
        if (request) {
            request.hidden = !item.requested;
            if (item.requested) previewSet('[data-preview-request-when]', longDate(item.requested));
        }

        const toc = $('[data-preview-toc]', preview);
        if (toc) { toc.hidden = true; toc.open = false; }

        const body = $('[data-preview-body]', preview);
        if (body) body.innerHTML = '<p class="preview-placeholder">Loading…</p>';

        openModal('previewModal');

        try {
            const res = await fetch('/api/get_blog/' + encodeURIComponent(item.id), { signal });
            const data = await res.json();
            if (!data.success) throw new Error(data.message || 'The blog could not be loaded.');

            // Someone can open one submission, close it and open another while
            // the first is still in flight; the late response must not paint
            // over the dialog that is on screen now.
            if (!state.current || state.current.id !== item.id) return;

            const blog = data.blog || {};
            const html = bodyHtml(blog.content);
            const words = countWords(html);

            previewSet('[data-preview-reading]', Math.max(1, Math.ceil(words / 200)) + ' min read');
            previewSet('[data-preview-words]', words.toLocaleString() + ' words');
            if (blog.author || blog.created_by) {
                previewSet('[data-preview-author]', blog.author || blog.created_by);
            }
            if (blog.category) previewSet('[data-preview-category]', blog.category);

            const contents = tocHtml(blog.content);
            if (toc) {
                toc.hidden = !contents;
                const tocBody = $('[data-preview-toc-body]', toc);
                if (tocBody) tocBody.innerHTML = contents;
            }

            if (body) {
                body.innerHTML = html || '<p class="preview-placeholder">This submission has no content yet.</p>';
            }
        } catch (error) {
            if (error.name === 'AbortError') return;
            if (body) {
                body.innerHTML = '<p class="preview-placeholder">' + esc(error.message) + '</p>';
            }
        }
    }

    // ----------------------------------------------------------------------
    // Publish-time picker
    //
    // The shared .cal grid from dashboard.css §12 over a hidden input, plus a
    // column of quarter-hours. It replaces <input type="datetime-local">, whose
    // popup is drawn by the browser and so takes none of the product's
    // surfaces, radius or type.
    //
    // The Schedule screen carries the same control (see .sched-when in
    // schedule.css and the picker block in schedule.js). The two are not shared
    // yet: that one is a scope-keyed pair of pickers driven from a file this
    // redesign does not touch. A third one should trigger the extraction of all
    // three rather than a fourth copy.
    // ----------------------------------------------------------------------

    const scheduleModal = document.getElementById('scheduleModal');
    const when = scheduleModal ? $('[data-when]', scheduleModal) : null;

    // 15-minute slots. Publishing a blog is not a to-the-minute act, and the
    // list can grey out what has already gone by — which a free-text field
    // cannot do until you have already typed it.
    const SLOT_MINUTES = 15;

    let calCursor = new Date();
    let pickedDay = '';    // YYYY-MM-DD
    let pickedTime = '';   // HH:MM, 24h

    // Local, not toISOString(). toISOString() converts to UTC first, so anywhere
    // behind UTC "today" comes back as yesterday for most of the day.
    function isoLocal(date) {
        const m = String(date.getMonth() + 1).padStart(2, '0');
        const d = String(date.getDate()).padStart(2, '0');
        return date.getFullYear() + '-' + m + '-' + d;
    }

    function parseIso(iso) {
        if (!iso) return null;
        const [y, m, d] = String(iso).split('-').map(Number);
        if (!y || !m || !d) return null;
        return new Date(y, m - 1, d);
    }

    function combined() {
        if (!pickedDay || !pickedTime) return null;
        const day = parseIso(pickedDay);
        const [h, min] = pickedTime.split(':').map(Number);
        day.setHours(h, min, 0, 0);
        return day;
    }

    function buildCalendar() {
        if (!when) return;
        const grid = $('[data-cal-grid]', when);
        if (!grid) return;

        const title = $('[data-cal-title]', when);
        if (title) {
            title.textContent = calCursor.toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
        }

        // Start on the Sunday on or before the 1st, then lay out six full weeks
        // so the grid never changes height as the month changes — a picker that
        // resizes under the pointer loses the day you were about to click.
        const first = new Date(calCursor.getFullYear(), calCursor.getMonth(), 1);
        const start = new Date(first);
        start.setDate(1 - first.getDay());

        const todayIso = isoLocal(new Date());
        let html = '';

        for (let i = 0; i < 42; i++) {
            const day = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i);
            const iso = isoLocal(day);
            // A publish date in the past is not a schedule, it is a mistake.
            const past = iso < todayIso;
            html += '<div class="cal-cell" data-cell="' + iso + '">' +
                '<button type="button" class="cal-day" data-iso="' + iso + '"' +
                (past ? ' disabled' : '') +
                ' aria-label="' + esc(day.toLocaleDateString(undefined, { dateStyle: 'full' })) + '">' +
                day.getDate() + '</button></div>';
        }

        grid.innerHTML = html;
        paintCalendar();
    }

    function paintCalendar() {
        if (!when) return;
        const grid = $('[data-cal-grid]', when);
        if (!grid) return;

        const todayIso = isoLocal(new Date());
        const month = calCursor.getMonth();

        $$('.cal-cell', grid).forEach((cell) => {
            const iso = cell.dataset.cell;
            const day = parseIso(iso);
            cell.classList.toggle('is-outside', day.getMonth() !== month);
            cell.classList.toggle('is-today', iso === todayIso);
            cell.classList.toggle('is-selected', !!pickedDay && iso === pickedDay);
        });
    }

    function buildTimes() {
        if (!when) return;
        const listEl = $('[data-time-list]', when);
        if (!listEl) return;

        // Only today's slots can have gone by, and only relative to now.
        const isToday = pickedDay === isoLocal(new Date());
        const now = new Date();
        const nowMinutes = now.getHours() * 60 + now.getMinutes();

        let html = '';
        let live = 0;

        for (let minutes = 0; minutes < 24 * 60; minutes += SLOT_MINUTES) {
            const h = Math.floor(minutes / 60);
            const m = minutes % 60;
            const value = String(h).padStart(2, '0') + ':' + String(m).padStart(2, '0');
            const past = isToday && minutes <= nowMinutes;
            if (!past) live++;

            const label = new Date(2000, 0, 1, h, m)
                .toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });

            html += '<button type="button" class="when-time' + (value === pickedTime ? ' is-selected' : '') + '" ' +
                'role="option" aria-selected="' + (value === pickedTime ? 'true' : 'false') + '" ' +
                'data-time="' + value + '"' + (past ? ' disabled' : '') + '>' + esc(label) + '</button>';
        }

        if (!live) {
            listEl.innerHTML = '<p class="when-times-none">Every slot today has gone by. Pick tomorrow.</p>';
            return;
        }

        listEl.innerHTML = html;
        scrollTimes();
    }

    // Bring the chosen slot — or the first one still available — into view,
    // rather than leaving the column parked at midnight with every live row
    // below the fold.
    //
    // Called again on `shown.bs.modal` and not only from buildTimes(): the
    // column is built while the dialog is still `display: none`, and a hidden
    // element measures 0 for both offsetTop and clientHeight, so the sum on
    // first open is always "scroll to the top". `.when-times-list` is
    // positioned in approval.css so offsetTop is measured against the column
    // rather than against whatever ancestor happens to be positioned.
    function scrollTimes() {
        if (!when) return;
        const listEl = $('[data-time-list]', when);
        if (!listEl || !listEl.clientHeight) return;

        const target = $('.when-time.is-selected', listEl) || $('.when-time:not([disabled])', listEl);
        if (target) listEl.scrollTop = Math.max(0, target.offsetTop - listEl.clientHeight / 3);
    }

    function paintWhen() {
        if (!when) return;

        const holder = $('[data-when-value]', when);
        const summary = $('[data-when-summary]', when);
        const text = $('[data-when-text]', when);
        const confirm = $('[data-schedule-confirm]', scheduleModal);

        const at = combined();
        const valid = !!at && at.getTime() > Date.now();

        if (holder) holder.value = valid ? at.toISOString() : '';
        if (confirm) confirm.disabled = !valid;

        if (summary) summary.classList.toggle('is-set', valid);
        if (!text) return;

        if (!pickedDay) {
            text.textContent = 'Pick a day, then a time.';
        } else if (!pickedTime) {
            text.textContent = 'Now pick a time on ' +
                parseIso(pickedDay).toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' }) + '.';
        } else if (!valid) {
            text.textContent = 'That moment has already passed.';
        } else {
            text.innerHTML = 'Publishes <strong>' + esc(longDate(at)) + '</strong>';
        }
    }

    function setWhen(date) {
        pickedDay = date ? isoLocal(date) : '';
        pickedTime = date
            ? String(date.getHours()).padStart(2, '0') + ':' +
              String(Math.floor(date.getMinutes() / SLOT_MINUTES) * SLOT_MINUTES).padStart(2, '0')
            : '';
        calCursor = date ? new Date(date.getFullYear(), date.getMonth(), 1) : new Date();
        buildCalendar();
        buildTimes();
        paintWhen();
    }

    if (when) {
        when.addEventListener('click', (e) => {
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
            if (day && !day.disabled) {
                pickedDay = day.dataset.iso;
                paintCalendar();
                // A time already chosen may have gone by on the newly chosen
                // day, so the column is rebuilt before the summary is written.
                buildTimes();
                if (pickedTime && $('.when-time[data-time="' + pickedTime + '"][disabled]', when)) {
                    pickedTime = '';
                    buildTimes();
                }
                paintWhen();
                return;
            }

            const slot = e.target.closest('.when-time');
            if (slot && !slot.disabled) {
                pickedTime = slot.dataset.time;
                buildTimes();
                paintWhen();
            }
        }, { signal });

        // Arrow keys walk the month, following the focus into the next one when
        // it steps off the edge.
        when.addEventListener('keydown', (e) => {
            const day = e.target.closest('.cal-day');
            if (!day) return;

            const step = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 }[e.key];
            if (!step) return;
            e.preventDefault();

            const next = parseIso(day.dataset.iso);
            next.setDate(next.getDate() + step);

            if (next.getMonth() !== calCursor.getMonth() || next.getFullYear() !== calCursor.getFullYear()) {
                calCursor = new Date(next.getFullYear(), next.getMonth(), 1);
                buildCalendar();
            }

            const target = $('.cal-day[data-iso="' + isoLocal(next) + '"]', when);
            if (target) target.focus();
        }, { signal });
    }

    // ----------------------------------------------------------------------
    // Publish Time Agent
    //
    // GET /api/schedule/best-time is untouched. What changed is the rendering:
    // the reason a time is being recommended is printed beside it instead of
    // being hidden in a `title=` attribute, and the chips are built from data
    // rather than from a template string with `onclick="applyBestTime(2, 10,
    // 'scheduleDateTime')"` interpolated into it.
    // ----------------------------------------------------------------------

    const FALLBACK_SLOTS = [
        { day_index: 2, hour: 10, display_time: 'Tuesday, 10:00 AM', reasoning: 'Mid-morning midweek is the broadest engagement window across most blogs.' },
        { day_index: 4, hour: 14, display_time: 'Thursday, 2:00 PM', reasoning: 'Thursday afternoons are peak reading time for most audiences.' },
        { day_index: 3, hour: 9, display_time: 'Wednesday, 9:00 AM', reasoning: 'Mid-week mornings catch readers checking new content first thing.' }
    ];

    // The next occurrence of a weekday-and-hour, as a real Date — so the chip
    // and the picker agree about what "Tuesday, 10:00 AM" means.
    function nextOccurrence(dayIndex, hour) {
        const now = new Date();
        let ahead = dayIndex - now.getDay();
        if (ahead < 0) ahead += 7;
        if (ahead === 0 && hour <= now.getHours()) ahead = 7;

        const target = new Date(now);
        target.setDate(target.getDate() + ahead);
        target.setHours(hour, 0, 0, 0);
        return target;
    }

    function renderSlots(slots, source) {
        const listEl = scheduleModal ? $('[data-besttime-list]', scheduleModal) : null;
        if (!listEl) return;

        listEl.innerHTML = source + slots.map((slot) => {
            const at = nextOccurrence(slot.day_index, slot.hour);
            return '<button type="button" class="besttime-slot" data-slot="' + esc(at.toISOString()) + '">' +
                '<span class="besttime-when">' + esc(slot.display_time || longDate(at)) + '</span>' +
                '<span class="besttime-why">' + esc(slot.reasoning || '') + '</span>' +
                '</button>';
        }).join('');
    }

    function sourceLine(kind, message) {
        if (kind === 'analytics') {
            return '<p class="besttime-source"><i class="bi bi-check-circle-fill" aria-hidden="true"></i> ' +
                'From your Google Analytics traffic over the last 28 days.</p>';
        }
        if (message) {
            return '<p class="besttime-source is-warning"><i class="bi bi-exclamation-triangle-fill" ' +
                'aria-hidden="true"></i> ' + esc(message) + '</p>';
        }
        return '<p class="besttime-source"><i class="bi bi-lightbulb-fill" aria-hidden="true"></i> ' +
            'General best practice — connect Analytics for times based on your own readers.</p>';
    }

    async function loadSlots() {
        const listEl = scheduleModal ? $('[data-besttime-list]', scheduleModal) : null;
        if (!listEl) return;

        listEl.innerHTML = '<p class="besttime-loading">' +
            '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> ' +
            'Reading your traffic…</p>';

        try {
            const res = await fetch('/api/schedule/best-time', { signal });
            const data = await res.json();

            if (data.success && Array.isArray(data.suggestions) && data.suggestions.length) {
                renderSlots(data.suggestions, sourceLine('analytics'));
            } else {
                renderSlots(FALLBACK_SLOTS, sourceLine('fallback', data.message));
            }
        } catch (error) {
            if (error.name === 'AbortError') return;
            renderSlots(FALLBACK_SLOTS, sourceLine('fallback'));
        }
    }

    // ----------------------------------------------------------------------
    // Schedule dialog
    // ----------------------------------------------------------------------

    function openSchedule(item, at) {
        if (!item || !scheduleModal) return;
        if (typeof window.closeAllDropdowns === 'function') window.closeAllDropdowns();

        state.current = item;

        const mark = $('[data-schedule-mark]', scheduleModal);
        if (mark) mark.textContent = (item.title || '?').trim().charAt(0).toUpperCase() || '?';
        const title = $('[data-schedule-title]', scheduleModal);
        if (title) title.textContent = item.title;

        // Opening on the date the writer asked for, when there is one: the
        // request is the reason this dialog is open, so making the reviewer
        // navigate to it is asking them to do the same work twice.
        const requested = at || (item.requested ? new Date(item.requested) : null);
        setWhen(requested && !isNaN(requested.getTime()) && requested.getTime() > Date.now() ? requested : null);

        openModal('scheduleModal');
        loadSlots();
    }

    async function confirmSchedule() {
        const item = state.current;
        const at = combined();
        if (!item || !at) return;

        const btn = $('[data-schedule-confirm]', scheduleModal);
        if (btn) btn.disabled = true;
        if (typeof window.showActionLoader === 'function') window.showActionLoader('Scheduling…');

        try {
            const res = await fetch('/api/schedule/' + encodeURIComponent(item.id), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ scheduled_at: at.toISOString() }),
                signal
            });
            const data = await res.json();
            if (!data.success) throw new Error(data.error || 'The blog could not be scheduled.');

            closeModal('scheduleModal');
            toast('success', 'Scheduled', 'It publishes ' + longDate(at) + '.');
            removeRow(item.id);
        } catch (error) {
            if (error.name === 'AbortError') return;
            if (btn) btn.disabled = false;
            toast('error', 'Not scheduled', error.message);
        } finally {
            if (typeof window.hideActionLoader === 'function') window.hideActionLoader();
        }
    }

    // ----------------------------------------------------------------------
    // Actions
    //
    // Delegated off the page root, so the row menu, the row shortcut and the
    // dialogs' footers all reach the same four functions. `state.current` is
    // what a click inside a dialog acts on; a click on a row acts on that row.
    // ----------------------------------------------------------------------

    root.addEventListener('click', (e) => {
        const pageBtn = e.target.closest('[data-pager] .pager-btn');
        if (pageBtn) {
            const page = parseInt(pageBtn.dataset.page, 10);
            if (!page || page === state.page) return;
            state.page = page;
            paint();
            return;
        }

        const trigger = e.target.closest('[data-action]');
        if (!trigger) return;

        const row = trigger.closest('.queue-row');
        const item = row ? submissionFrom(row) : state.current;
        if (!item) return;

        switch (trigger.dataset.action) {
            case 'preview':
                if (row) openPreview(row);
                break;

            case 'publish':
                closeModal('previewModal');
                publish(item.id);
                break;

            case 'reject':
                closeModal('previewModal');
                reject(item.id);
                break;

            case 'schedule':
                afterClose('previewModal', () => openSchedule(item, null));
                break;

            case 'schedule-requested':
                afterClose('previewModal', () =>
                    openSchedule(item, item.requested ? new Date(item.requested) : null));
                break;
        }
    }, { signal });

    if (scheduleModal) {
        scheduleModal.addEventListener('click', (e) => {
            if (e.target.closest('[data-schedule-confirm]')) {
                confirmSchedule();
                return;
            }

            const slot = e.target.closest('.besttime-slot');
            if (slot) setWhen(new Date(slot.dataset.slot));
        }, { signal });

        scheduleModal.addEventListener('shown.bs.modal', scrollTimes, { signal });
    }

    // ----------------------------------------------------------------------
    // Boot
    //
    // The queue is already on screen from the server, so nothing is fetched
    // here: the timestamps are converted in place, the tiles are recomputed
    // from the rows, and the first page window is applied.
    // ----------------------------------------------------------------------

    paintTimes();
    paintStats();
    paint();

    // syncThemeControls only runs on DOMContentLoaded, which PJAX never fires.
    if (typeof window.syncThemeControls === 'function') window.syncThemeControls();
})();
