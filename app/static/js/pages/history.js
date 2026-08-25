/**
 * History — reading a past conversation with the agent.
 *
 * The screen is an index and a reader. The index is server-rendered and grows
 * by keyset paging; the reader fetches one transcript at a time and paints it
 * with the same thread component the create screen paints a live run in.
 *
 * Three things this file is careful about:
 *
 *   - **It survives PJAX.** Everything is bound with an AbortController and a
 *     fresh visit aborts the previous instance, the pattern the rest of the
 *     page scripts use. A listener left behind by a screen that has been
 *     swapped out is the bug this app has already had once, on the create
 *     screen's poller.
 *   - **The selection lives in the URL.** `?run=<id>` is written with
 *     `replaceState`, so a reload, a copied link and the browser's back button
 *     all land on the conversation the reader was actually reading, and PJAX's
 *     own history entries are not disturbed.
 *   - **Requests are single-flight.** Clicking three rows quickly used to be
 *     three responses racing to paint the same pane, and the last one to arrive
 *     wins regardless of which row is selected. Each fetch carries its own
 *     AbortController and the previous one is aborted, so the pane can only
 *     ever show what is selected now.
 *
 * All rendered text is model or user output. It goes in through `textContent`,
 * except the draft excerpt, which goes through the shared markdown renderer —
 * escape first, tags after (see js/components/draft-markdown.js).
 */

(function historyPage() {
    'use strict';

    if (window.__historyAbort) {
        try { window.__historyAbort.abort(); } catch (e) { /* already gone */ }
    }
    const controller = new AbortController();
    const signal = controller.signal;
    window.__historyAbort = controller;

    const root = document.querySelector('.dashboard-main');
    const shell = root && root.querySelector('[data-history]');
    if (!shell) return;

    // The empty state has no rail and no pane — there is nothing here to wire.
    if (shell.dataset.state !== 'ready') return;

    const $ = (sel) => shell.querySelector(sel);

    const el = {
        list: $('[data-list]'),
        filter: $('[data-filter]'),
        noresults: $('[data-noresults]'),
        more: $('[data-more]'),
        clearAll: $('[data-clear-all]'),

        pane: $('[data-pane]'),
        paneErrorText: $('[data-pane-error-text]'),

        prompt: $('[data-run-prompt]'),
        reason: $('[data-reason]'),
        reasonToggle: $('[data-reason-toggle]'),
        reasonTitle: $('[data-reason-title]'),
        reasonList: $('[data-reason-list]'),
        fail: $('[data-run-fail]'),
        failText: $('[data-run-fail-text]'),
        title: $('[data-run-title]'),
        excerpt: $('[data-excerpt]'),
        excerptBody: $('[data-excerpt-body]'),
        excerptNote: $('[data-excerpt-note]'),
        openBlog: $('[data-open-blog]'),
        trail: $('[data-trail]'),
        openBlogBtn: $('[data-open-blog-btn]'),
        reuseLabel: $('[data-reuse-label]')
    };
    if (!el.list || !el.pane) return;

    // Where a reused prompt is left for the create screen. It reads this on
    // boot and restores the composer from it, so "Reuse" is a write and a
    // navigation rather than a second way of starting a run.
    const CREATE_DRAFT_KEY = 'scriptly-create-draft';

    const state = {
        selectedId: shell.dataset.selected || '',
        // The in-flight detail request, so a second click can cancel the first.
        pending: null,
        // Kept so the retry button knows what to retry.
        lastRequested: '',
        loadingMore: false
    };

    // ------------------------------------------------------------------
    // Small helpers
    // ------------------------------------------------------------------

    function paneState(name) {
        el.pane.dataset.paneState = name;
        el.pane.setAttribute('aria-busy', name === 'loading' ? 'true' : 'false');
    }

    function toast(message, type) {
        if (window.showToast) window.showToast({ message: message, type: type || 'info' });
    }

    function plural(n, word) {
        return n + ' ' + word + (n === 1 ? '' : 's');
    }

    // `1043` reads as a serial number; `1,043` reads as a quantity.
    function group(n) {
        return Number(n || 0).toLocaleString();
    }

    // The same wording every other listing in the dashboard uses (activity,
    // approvals, comments, leads each carry this pair). Kept local rather than
    // reached for on `window`, because none of those screens exports it and a
    // page that silently prints ISO dates when its neighbour is not loaded is
    // worse than eighteen duplicated lines.
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

    // The exact date stays on the element's `title`: "3 days ago" is the right
    // thing to read at a glance and the wrong thing to have to work backwards
    // from when it matters.
    function stampRow(row) {
        row.querySelectorAll('time[data-relative]').forEach((node) => {
            const raw = node.getAttribute('datetime');
            const text = relative(raw);
            if (!text) return;
            node.textContent = text;
            node.title = absolute(raw);
        });
    }

    // ------------------------------------------------------------------
    // The rail
    // ------------------------------------------------------------------

    function rows() {
        return Array.from(el.list.querySelectorAll('[data-row]'));
    }

    function markSelected(id) {
        rows().forEach((row) => {
            row.setAttribute('aria-pressed', String(row.dataset.id === id));
        });
    }

    /**
     * Reflect the open conversation in the address bar.
     *
     * `replaceState`, not `pushState`: selecting rows is reading within one
     * screen, not navigating between screens, and pushing an entry per row
     * would make Back mean "the previous row I glanced at" for as long as the
     * reader browsed. PJAX owns the real history entries for this page.
     */
    function syncUrl(id) {
        try {
            const url = new URL(window.location.href);
            if (id) url.searchParams.set('run', id);
            else url.searchParams.delete('run');
            history.replaceState(history.state, '', url.toString());
        } catch (e) { /* a URL we cannot rewrite is not worth failing over */ }
    }

    function applyFilter() {
        const query = (el.filter.value || '').trim().toLowerCase();
        let shown = 0;

        rows().forEach((row) => {
            const hit = !query || (row.dataset.search || '').indexOf(query) !== -1;
            row.closest('.hist-item').hidden = !hit;
            if (hit) shown += 1;
        });

        el.noresults.hidden = shown !== 0;
        // "Load older" against a filtered list would fetch a page the filter
        // then hides, which reads as a broken button. The filter searches what
        // is loaded; loading more is a separate act.
        if (el.more) el.more.hidden = Boolean(query) || !el.more.dataset.cursor;
    }

    /** Build one rail row. Mirrors the markup in history.html — see the note there. */
    function buildRow(run) {
        const failed = run.status === 'failed';
        const label = (!failed && run.title) ? run.title : run.prompt;

        const item = document.createElement('li');
        item.className = 'hist-item';

        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'hist-row';
        button.dataset.row = '';
        button.dataset.id = run.id;
        button.dataset.search = [run.title, run.prompt, run.category]
            .filter(Boolean).join(' ').toLowerCase();
        button.setAttribute('aria-pressed', 'false');

        const mark = document.createElement('span');
        mark.className = 'hist-row-mark' + (failed ? ' is-failed' : '');
        mark.setAttribute('aria-hidden', 'true');
        const icon = document.createElement('i');
        icon.className = 'material-symbols-outlined icon-inline';
        icon.textContent = failed ? 'error' : 'auto_awesome';
        mark.appendChild(icon);

        const body = document.createElement('span');
        body.className = 'hist-row-body';

        const title = document.createElement('span');
        title.className = 'hist-row-title';
        title.textContent = (label || 'Untitled run').replace(/\*\*/g, '');

        const meta = document.createElement('span');
        meta.className = 'hist-row-meta';
        const time = document.createElement('time');
        time.setAttribute('datetime', run.created_at || '');
        time.dataset.relative = '';
        time.textContent = relative(run.created_at) || '';
        meta.appendChild(time);

        if (failed) {
            const flag = document.createElement('span');
            flag.className = 'hist-row-flag';
            flag.textContent = 'Stopped';
            meta.appendChild(flag);
        } else if (run.category) {
            const cat = document.createElement('span');
            cat.className = 'hist-row-cat';
            cat.textContent = run.category;
            meta.appendChild(cat);
        }

        body.appendChild(title);
        body.appendChild(meta);
        button.appendChild(mark);
        button.appendChild(body);
        stampRow(button);
        item.appendChild(button);
        return item;
    }

    async function loadMore() {
        if (state.loadingMore || !el.more) return;
        const cursor = el.more.dataset.cursor;
        if (!cursor) return;

        state.loadingMore = true;
        el.more.disabled = true;
        el.more.textContent = 'Loading…';

        try {
            const res = await fetch(
                '/api/history?before=' + encodeURIComponent(cursor),
                { signal }
            );
            const data = await res.json();
            if (!res.ok || !data.success) throw new Error(data.error || 'Request failed');

            (data.items || []).forEach((run) => el.list.appendChild(buildRow(run)));

            // The cursor advances even when has_more is false, so the button
            // can be re-shown later without carrying a stale position.
            el.more.dataset.cursor = data.next_cursor || '';
            el.more.hidden = !data.has_more;
            markSelected(state.selectedId);
            applyFilter();
        } catch (err) {
            if (signal.aborted) return;
            toast('Could not load older conversations.', 'error');
        } finally {
            state.loadingMore = false;
            el.more.disabled = false;
            el.more.textContent = 'Load older';
        }
    }

    // ------------------------------------------------------------------
    // The reader
    // ------------------------------------------------------------------

    function renderReason(thoughts) {
        el.reasonList.textContent = '';
        if (!thoughts || !thoughts.length) {
            el.reason.hidden = true;
            return;
        }

        thoughts.forEach((thought) => {
            const li = document.createElement('li');
            li.className = 'reason-item';
            li.textContent = thought.text || '';
            el.reasonList.appendChild(li);
        });

        // Named by what it is rather than by a duration: the live screen can say
        // "Thought for 4s" because it watched; this one is reading a record and
        // the number of steps is the honest thing it knows.
        el.reasonTitle.textContent = 'Its thinking · ' + plural(thoughts.length, 'step');
        el.reason.hidden = false;

        // Collapsed by default here, open by default on the create screen.
        // While a run works its plan is the only thing to read; afterwards the
        // outcome is, and the plan is for the reader who wants to know why.
        el.reasonToggle.setAttribute('aria-expanded', 'false');
    }

    // How a blog status reads in a sentence. Unknown values fall through to
    // the raw string rather than to "undefined".
    const STATUS_WORD = {
        DRAFT: 'a draft',
        UNDER_REVIEW: 'submitted for review',
        PUBLISHED: 'published',
        REJECTED: 'rejected'
    };

    function renderTrail(run) {
        el.trail.textContent = '';
        if (run.status !== 'completed') return;

        const facts = [];
        if (run.word_count) {
            facts.push(
                group(run.word_count) + ' words'
                + (run.reading_time ? ' · ' + run.reading_time : '')
            );
        }
        if (run.section_count) {
            facts.push('Table of contents built from ' + plural(run.section_count, 'heading'));
        }
        if (run.category) facts.push('Filed under ' + run.category);
        if (run.blog_status) {
            facts.push('Saved as ' + (STATUS_WORD[run.blog_status] || run.blog_status));
        }
        if (run.duration_seconds) facts.push('Written in ' + formatDuration(run.duration_seconds));

        facts.forEach((text) => {
            const li = document.createElement('li');
            li.className = 'trail-item';

            const tick = document.createElement('i');
            tick.className = 'material-symbols-outlined icon-inline trail-tick';
            tick.setAttribute('aria-hidden', 'true');
            tick.textContent = 'check';

            li.appendChild(tick);
            li.appendChild(document.createTextNode(text));
            el.trail.appendChild(li);
        });
    }

    function formatDuration(seconds) {
        const total = Math.round(Number(seconds) || 0);
        if (total < 60) return total + 's';
        return Math.floor(total / 60) + 'm ' + (total % 60) + 's';
    }

    /**
     * Where a blog produced by a run can be reached.
     *
     * All Blogs, filtered to its title, rather than a link straight at the
     * post: the dashboard has no per-blog page — every screen that opens one
     * does it in a modal keyed by id — so a deep link would have to be to a
     * route that does not exist. The listing reads `?search=` on load, so this
     * lands the reader on the row.
     */
    function blogHref(run) {
        return '/all-blogs?search=' + encodeURIComponent(run.title || '');
    }

    /**
     * Point the action row at this run.
     *
     * The buttons are in the template (see history.html) and never rebuilt —
     * this only sets what changes between one conversation and the next. The
     * icons in particular have to be literal markup: the icon font is subset
     * to the glyph names a scan of the templates and scripts can see, so a
     * name that only ever exists as a variable is left out of the font and
     * renders as its own text.
     */
    function renderActions(run) {
        const openable = run.status === 'completed' && Boolean(run.blog_id);
        el.openBlogBtn.hidden = !openable;
        if (openable) el.openBlogBtn.href = blogHref(run);

        el.reuseLabel.textContent = run.status === 'failed'
            ? 'Try this prompt again'
            : 'Reuse this prompt';
    }

    function renderRun(run) {
        el.prompt.textContent = run.prompt || '';

        renderReason(run.thoughts);

        const failed = run.status === 'failed';
        el.fail.hidden = !failed;
        el.failText.textContent = failed
            ? (run.error || 'The run stopped before it produced a draft.')
            : '';

        const title = (run.title || '').replace(/\*\*/g, '');
        el.title.hidden = failed || !title;
        el.title.textContent = title;

        const excerpt = run.excerpt || '';
        el.excerpt.hidden = !excerpt;
        el.excerptBody.innerHTML = excerpt ? window.DraftMarkdown.render(excerpt) : '';

        // The note only appears when there is somewhere for its link to go.
        // A run whose blog was deleted still has its transcript, and offering
        // "read all of it" for a post that is gone is worse than saying
        // nothing.
        const linkable = Boolean(excerpt && run.blog_id);
        el.excerptNote.hidden = !linkable;
        if (linkable) el.openBlog.href = blogHref(run);

        renderTrail(run);
        renderActions(run);

        paneState('ready');
    }

    async function openRun(id) {
        if (!id) return;

        state.selectedId = id;
        state.lastRequested = id;
        markSelected(id);
        syncUrl(id);

        // One request at a time. Three quick clicks used to be three responses
        // racing to paint one pane, where the slowest wins whatever is selected.
        if (state.pending) state.pending.abort();
        const local = new AbortController();
        state.pending = local;
        signal.addEventListener('abort', () => local.abort(), { once: true });

        paneState('loading');

        try {
            const res = await fetch('/api/history/' + encodeURIComponent(id),
                                    { signal: local.signal });
            const data = await res.json();

            // A response for a row that is no longer selected is dropped rather
            // than painted: the abort above covers the common case, but a
            // response already in flight can still land after it.
            if (state.selectedId !== id) return;

            if (!res.ok || !data.success) throw new Error(data.error || 'Request failed');
            renderRun(data.run);
        } catch (err) {
            if (local.signal.aborted || signal.aborted) return;
            el.paneErrorText.textContent = err.message || 'That conversation could not be loaded.';
            paneState('error');
        } finally {
            if (state.pending === local) state.pending = null;
        }
    }

    // ------------------------------------------------------------------
    // Actions on a conversation
    // ------------------------------------------------------------------

    function reusePrompt() {
        const prompt = el.prompt.textContent || '';
        if (!prompt.trim()) return;

        try {
            sessionStorage.setItem(CREATE_DRAFT_KEY, JSON.stringify({
                text: prompt, at: Date.now()
            }));
        } catch (e) {
            // Private mode, or a full quota. The navigation is still worth
            // doing — the reader lands on the composer, just empty — but they
            // should not be told the prompt came with them when it did not.
            toast('Could not carry the prompt over. Copy it across by hand.', 'warning');
            return;
        }

        window.location.href = '/create';
    }

    async function deleteRun(id) {
        if (!id) return;
        if (!window.confirm('Remove this conversation from your history? The blog it produced is not affected.')) {
            return;
        }

        try {
            const res = await fetch('/api/history/' + encodeURIComponent(id),
                                    { method: 'DELETE', signal });
            const data = await res.json();
            if (!res.ok || !data.success) throw new Error(data.error || 'Request failed');

            const row = el.list.querySelector('[data-row][data-id="' + id + '"]');
            if (row) row.closest('.hist-item').remove();

            state.selectedId = '';
            syncUrl('');
            paneState('resting');
            applyFilter();
            toast(data.message || 'Conversation removed.', 'success');

            // The rail emptying out is a different screen, and rebuilding it
            // here would mean duplicating the empty state in two languages.
            if (!rows().length) window.location.reload();
        } catch (err) {
            if (signal.aborted) return;
            toast('Could not remove that conversation.', 'error');
        }
    }

    async function clearAll() {
        if (!window.confirm('Delete every conversation in your history? Your blogs are not affected.')) {
            return;
        }

        try {
            const res = await fetch('/api/history', { method: 'DELETE', signal });
            const data = await res.json();
            if (!res.ok || !data.success) throw new Error(data.error || 'Request failed');

            toast(data.message || 'History cleared.', 'success');
            window.location.reload();
        } catch (err) {
            if (signal.aborted) return;
            toast('Could not clear your history.', 'error');
        }
    }

    // ------------------------------------------------------------------
    // Wiring
    // ------------------------------------------------------------------

    // Delegated off the shell, so rows appended by "Load older" work without
    // being bound one at a time.
    shell.addEventListener('click', (e) => {
        const row = e.target.closest('[data-row]');
        if (row) {
            openRun(row.dataset.id);
            return;
        }

        const action = e.target.closest('[data-action]');
        if (action) {
            if (action.dataset.action === 'reuse') reusePrompt();
            else if (action.dataset.action === 'delete') deleteRun(state.selectedId);
            return;
        }

        if (e.target.closest('[data-more]')) { loadMore(); return; }
        if (e.target.closest('[data-clear-all]')) { clearAll(); return; }
        if (e.target.closest('[data-pane-retry]')) { openRun(state.lastRequested); return; }

        const toggle = e.target.closest('[data-reason-toggle]');
        if (toggle) {
            const open = toggle.getAttribute('aria-expanded') === 'true';
            toggle.setAttribute('aria-expanded', String(!open));
        }
    }, { signal });

    // The excerpt's "read all of it" is a real link; it just must not also
    // count as a click on the row behind it.
    el.openBlog.addEventListener('click', (e) => e.stopPropagation(), { signal });

    el.filter.addEventListener('input', applyFilter, { signal });

    // Escape clears the filter from inside the field, which is where a reader
    // whose list has gone empty already has their hands.
    el.filter.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && el.filter.value) {
            el.filter.value = '';
            applyFilter();
        }
    }, { signal });

    document.addEventListener('pjax:complete', () => {
        if (!document.contains(shell)) controller.abort();
    }, { signal });

    // ------------------------------------------------------------------
    // Boot
    // ------------------------------------------------------------------

    // The server rendered ISO dates; turn them into the relative stamps the
    // rest of the dashboard shows, the same way the rows built in JS are.
    rows().forEach(stampRow);
    applyFilter();

    // `?run=` deep-links a conversation. Falling back to the newest one rather
    // than to the resting state: the reader came to a history screen, and the
    // most recent exchange is what they almost always want.
    const first = rows()[0];
    const wanted = state.selectedId
        && el.list.querySelector('[data-row][data-id="' + state.selectedId + '"]');
    if (wanted) openRun(state.selectedId);
    else if (first) openRun(first.dataset.id);

    if (typeof window.syncThemeControls === 'function') window.syncThemeControls();
})();
