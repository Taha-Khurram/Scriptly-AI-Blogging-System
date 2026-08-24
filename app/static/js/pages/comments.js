/**
 * Comments — the moderation queue: read what was written, take it down, put it
 * back, edit it or destroy it.
 *
 * Was ten globals (loadComments, goToPage, openCommentModal, saveCommentEdit,
 * removeComment, restoreComment, deleteComment, confirmDelete, refreshStats,
 * escapeHtml) wired to the rows through `onclick="removeComment('<id>')"`
 * strings, plus its own `formatDate` and `escapeHtml` shadowing the ones every
 * other page defines. Actions are now delegated off the page root and read
 * their target from data attributes on the row.
 *
 * Rows are the shared .data-row, so nothing here builds a table. The first
 * page is rendered by Jinja (see the comment_row macro in comments.html) and
 * this file renders the identical structure for every page and filter after
 * it — the two have to stay in step.
 *
 * PJAX re-injects this file on every visit to /comments, so nothing holds a
 * reference across navigations and every document-level listener goes through
 * an AbortController the next run aborts.
 */

(function commentsPage() {
    'use strict';

    if (window.__commentsAbort) {
        try { window.__commentsAbort.abort(); } catch (e) { /* already gone */ }
    }
    const controller = new AbortController();
    const signal = controller.signal;
    window.__commentsAbort = controller;

    const $ = (sel, root) => (root || document).querySelector(sel);
    const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

    const root = $('.dashboard-main');
    const list = $('#commentsList');
    if (!root || !list) return;

    const state = {
        filter: 'all',
        page: parseInt(list.dataset.page, 10) || 1,
        perPage: parseInt(list.dataset.perPage, 10) || 10,
        total: parseInt(list.dataset.total, 10) || 0,
        query: '',
        current: null,        // the comment open in the dialog
        pendingDelete: null   // { id, text }
    };

    // ----------------------------------------------------------------------
    // Helpers
    // ----------------------------------------------------------------------

    // Escapes all five characters Jinja's autoescape does, not just the three
    // the `div.textContent -> div.innerHTML` trick covers: comment text, names
    // and email addresses all land in attributes here, and every one of them
    // is supplied by an anonymous visitor.
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

    // The same wording drafts.js and home.js use for their rows, so a
    // timestamp reads the same wherever it appears.
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

    function paintTimes(scope) {
        $$('time[data-relative]', scope || list).forEach((el) => {
            const text = relative(el.getAttribute('datetime'));
            if (!text) return;
            el.title = absolute(el.getAttribute('datetime'));
            el.textContent = text;
        });
    }

    function statusOf(comment) {
        return String(comment.status || 'published').toLowerCase() === 'removed' ? 'removed' : 'published';
    }

    const AI_LABEL = { edited: 'AI edited', removed: 'AI removed' };

    function aiOf(comment) {
        return String(comment.ai_action || 'approved').toLowerCase();
    }

    // ----------------------------------------------------------------------
    // Row rendering — mirrors the comment_row macro in comments.html
    // ----------------------------------------------------------------------

    function menuItem(action, icon, text, danger) {
        return '<li><button type="button" class="dropdown-item' + (danger ? ' text-danger' : '') + '" ' +
            'data-action="' + action + '">' +
            '<i class="bi bi-' + icon + '" aria-hidden="true"></i> ' + text +
            '</button></li>';
    }

    function commentRow(c) {
        const st = statusOf(c);
        const removed = st === 'removed';
        const text = String(c.display_text || c.original_text || '');
        const name = String(c.commenter_name || 'Anonymous');
        const email = String(c.commenter_email || '');
        const post = String(c.blog_title || 'Unknown post');
        const ai = aiOf(c);
        const aiLabel = AI_LABEL[ai] || '';
        const stLabel = removed ? 'Removed' : 'Visible';
        const label = esc(name);

        const meta = ['<span>' + label + '</span>',
            '<span class="row-sep" aria-hidden="true">·</span>',
            '<span>' + esc(post) + '</span>'];

        if (c.created_at) {
            meta.push('<span class="row-sep" aria-hidden="true">·</span>');
            meta.push('<time class="row-time" datetime="' + esc(c.created_at) + '" data-relative>' +
                esc(String(c.created_at).slice(0, 10)) + '</time>');
        }
        if (aiLabel) {
            meta.push('<span class="row-sep row-meta-ai" aria-hidden="true">·</span>');
            meta.push('<span class="row-meta-ai">' + aiLabel + '</span>');
        }
        meta.push('<span class="row-sep row-meta-status" aria-hidden="true">·</span>');
        meta.push('<span class="row-meta-status">' + stLabel + '</span>');

        const mark = name.trim() ? esc(name.trim()[0].toUpperCase()) : '?';

        // The verdict cell is rendered even when there is no verdict: the two
        // pill columns are fixed tracks, and a missing child would slide the
        // status pill left on that row alone.
        const verdict = aiLabel
            ? '<span class="ai-pill is-' + ai + '"><i class="bi bi-robot" aria-hidden="true"></i> ' + aiLabel + '</span>'
            : '<span class="ai-pill-blank"></span>';

        const trailAction = removed
            ? '<button type="button" class="row-action" data-action="restore" title="Put back on the site" ' +
              'aria-label="Put the comment from ' + label + ' back on the site">' +
              '<i class="bi bi-arrow-counterclockwise" aria-hidden="true"></i></button>'
            : '<button type="button" class="row-action" data-action="remove" title="Take off the site" ' +
              'aria-label="Take the comment from ' + label + ' off the site">' +
              '<i class="bi bi-eye-slash" aria-hidden="true"></i></button>';

        const menu = [
            menuItem('view', 'eye', 'Open comment'),
            removed
                ? menuItem('restore', 'arrow-counterclockwise', 'Put back on the site')
                : menuItem('remove', 'eye-slash', 'Take off the site'),
            menuItem('copy-email', 'envelope', 'Copy email address'),
            '<li><hr class="dropdown-divider"></li>',
            menuItem('delete', 'trash3', 'Delete permanently', true)
        ];

        return '<div class="data-row' + (removed ? ' is-removed' : '') + '" id="comment-row-' + esc(c.id) + '" ' +
            'data-id="' + esc(c.id) + '" data-status="' + st + '" data-name="' + label + '" ' +
            'data-email="' + esc(email) + '" data-text="' + esc(text) + '" ' +
            'data-search="' + esc((text + ' ' + name + ' ' + post).toLowerCase()) + '">' +

            '<span class="row-mark" aria-hidden="true">' + mark + '</span>' +

            '<button type="button" class="row-open" data-action="view" ' +
            'title="Open the comment from ' + label + '">' +
            '<span class="row-title comment-row-text">' + (esc(text) || 'Empty comment') + '</span>' +
            '<span class="row-meta">' + meta.join('') + '</span>' +
            '</button>' +

            verdict +
            '<span class="status-pill status-' + (removed ? 'hidden' : 'live') + '">' + stLabel + '</span>' +

            '<div class="row-trail">' + trailAction +
            '<div class="dropdown">' +
            '<button type="button" class="btn-dropdown-trigger" data-bs-toggle="dropdown" aria-expanded="false" ' +
            'aria-label="More actions for the comment from ' + label + '">' +
            '<i class="bi bi-three-dots-vertical" aria-hidden="true"></i>' +
            '</button>' +
            '<ul class="dropdown-menu dropdown-menu-end">' + menu.join('') + '</ul>' +
            '</div>' +
            '</div>' +
            '</div>';
    }

    // ----------------------------------------------------------------------
    // States
    // ----------------------------------------------------------------------

    const EMPTY_COPY = {
        all: 'No comments yet. When a reader writes one on a published post it lands here, already ' +
            'checked by the moderation filter, for you to keep or take down.',
        published: 'Nothing is on the site at the moment.',
        removed: 'Nothing has been taken down. Comments you remove collect here, and can be put back.',
        edited: 'The moderation filter has not had to change anything yet.'
    };

    function loadingState() {
        return '<div class="comments-state">' +
            '<div class="spinner-border spinner-border-sm text-primary opacity-50" role="status"></div>' +
            '<p>Loading comments…</p>' +
            '</div>';
    }

    function emptyState() {
        return '<div class="list-empty">' +
            '<span class="list-empty-icon"><i class="bi bi-chat-square-text" aria-hidden="true"></i></span>' +
            '<p>' + esc(EMPTY_COPY[state.filter] || EMPTY_COPY.all) + '</p>' +
            '</div>';
    }

    function errorState(message) {
        return '<div class="list-empty">' +
            '<span class="list-empty-icon"><i class="bi bi-exclamation-triangle" aria-hidden="true"></i></span>' +
            '<p>' + esc(message || 'The comments could not be loaded.') + '</p>' +
            '<button type="button" class="app-btn is-ghost" data-action="retry">Try again</button>' +
            '</div>';
    }

    // ----------------------------------------------------------------------
    // Pager
    //
    // Owned entirely by this file — the template renders an empty <nav> — so
    // there is one implementation of the page window rather than two that
    // drift. A window of three around the current page, with the first and
    // last always reachable.
    // ----------------------------------------------------------------------

    function pages() {
        return Math.max(1, Math.ceil(state.total / state.perPage));
    }

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

        const shown = Array.from(wanted).sort((a, b) => a - b);
        let html = '';
        let previous = 0;

        shown.forEach((p) => {
            if (previous && p - previous > 1) html += '<span class="pager-dots">…</span>';
            html += '<button type="button" class="pager-btn' + (p === state.page ? ' is-active' : '') + '" ' +
                'data-page="' + p + '"' + (p === state.page ? ' aria-current="page"' : '') + '>' + p + '</button>';
            previous = p;
        });

        nav.innerHTML = html;
    }

    // ----------------------------------------------------------------------
    // Painting
    // ----------------------------------------------------------------------

    const FILTER_NOTE = {
        all: '',
        published: 'Showing what is on the site',
        removed: 'Showing what has been taken down',
        edited: 'Showing comments the filter changed'
    };

    function paintHead() {
        const count = $('[data-list-count]', root);
        const note = $('[data-list-note]', root);
        const last = pages();

        if (count) count.textContent = String(state.total);
        if (!note) return;

        const parts = [];
        if (last > 1) parts.push('Page ' + state.page + ' of ' + last);
        if (FILTER_NOTE[state.filter]) parts.push(FILTER_NOTE[state.filter]);
        note.textContent = parts.join(' · ');
    }

    function render(data) {
        const comments = (data && data.comments) || [];

        state.total = Number(data && data.total) || 0;
        state.page = Number(data && data.page) || state.page;
        state.perPage = Number(data && data.per_page) || state.perPage;

        list.setAttribute('aria-busy', 'false');
        list.innerHTML = comments.length ? comments.map(commentRow).join('') : emptyState();

        paintTimes();
        paintHead();
        paintPager();
        applyQuery();
    }

    // ----------------------------------------------------------------------
    // Loading
    // ----------------------------------------------------------------------

    async function getJSON(url, options) {
        const res = await fetch(url, Object.assign({ credentials: 'same-origin' }, options || {}));

        // A session that has expired is answered with the login page, not with
        // JSON, so a bare res.json() would throw a parse error instead of
        // saying what actually happened.
        const type = res.headers.get('content-type') || '';
        if (!type.includes('application/json')) {
            throw new Error('Your session has expired. Reload the page to sign in again.');
        }

        const data = await res.json();
        if (!data.success) throw new Error(data.error || 'The server rejected that.');
        return data;
    }

    async function load() {
        list.setAttribute('aria-busy', 'true');
        list.innerHTML = loadingState();

        const params = new URLSearchParams({ page: state.page, per_page: state.perPage });
        if (state.filter !== 'all') params.set('status', state.filter);

        try {
            render(await getJSON('/api/comments?' + params.toString()));
        } catch (error) {
            list.setAttribute('aria-busy', 'false');
            list.innerHTML = errorState(error.message);
            paintPager();
        }
    }

    // ----------------------------------------------------------------------
    // Stats
    //
    // Refreshed after every write, because taking a comment down moves it
    // between two of the tiles and one of the tab counts — leaving them stale
    // would show a figure that contradicts the list right beside it.
    // ----------------------------------------------------------------------

    function paintStats(stats) {
        const total = Number(stats.total) || 0;
        const live = Number(stats.published) || 0;
        const hidden = Number(stats.removed) || 0;
        const edited = Number(stats.ai_edited) || 0;

        const set = (sel, value) => {
            const el = $(sel, root);
            if (el) el.textContent = String(value);
        };

        set('[data-stat-total]', total);
        set('[data-stat-live]', live);
        set('[data-stat-hidden]', hidden);
        set('[data-count-all]', total);
        set('[data-count-published]', live);
        set('[data-count-removed]', hidden);
        set('[data-count-edited]', edited);

        const meter = $('[data-meter]', root);
        const fill = $('[data-meter-fill]', root);
        const note = $('[data-meter-note]', root);
        const share = total ? Math.round((live / total) * 100) : 0;

        if (meter) meter.hidden = total === 0;
        if (fill) fill.style.width = share + '%';
        if (note) note.textContent = share + '% of all comments';
    }

    async function refreshStats() {
        try {
            const data = await getJSON('/api/comments/stats');
            if (data.stats) paintStats(data.stats);
        } catch (e) {
            // Non-critical: the list beside them is the source of truth.
        }
    }

    // ----------------------------------------------------------------------
    // Search
    //
    // Filters the rendered page in place. /api/comments takes no search
    // parameter, so this deliberately does not claim to search everything —
    // the no-results line says "on this page".
    // ----------------------------------------------------------------------

    function applyQuery() {
        const rows = $$('.data-row', list);
        const none = $('[data-noresults]', root);
        if (!rows.length) {
            if (none) none.hidden = true;
            return;
        }

        let shown = 0;
        rows.forEach((row) => {
            const hit = !state.query || (row.dataset.search || '').indexOf(state.query) !== -1;
            row.hidden = !hit;
            if (hit) shown++;
        });

        if (none) none.hidden = shown !== 0;
    }

    document.addEventListener('page-search', (e) => {
        state.query = ((e.detail && e.detail.value) || '').trim().toLowerCase();
        applyQuery();
    }, { signal });

    // ----------------------------------------------------------------------
    // Filter tabs and pager
    // ----------------------------------------------------------------------

    root.addEventListener('click', (e) => {
        const tab = e.target.closest('.comments-filter .seg-tab');
        if (tab) {
            if (tab.dataset.filter === state.filter) return;
            $$('.comments-filter .seg-tab', root).forEach((t) => {
                t.classList.toggle('is-active', t === tab);
            });
            state.filter = tab.dataset.filter || 'all';
            state.page = 1;
            load();
            return;
        }

        const pageBtn = e.target.closest('[data-pager] .pager-btn');
        if (pageBtn) {
            const page = parseInt(pageBtn.dataset.page, 10);
            if (!page || page === state.page) return;
            state.page = page;
            load();
        }
    }, { signal });

    // ----------------------------------------------------------------------
    // Dialog
    // ----------------------------------------------------------------------

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

    const detail = document.getElementById('commentModal');

    function selectTab(name) {
        if (!detail) return;
        $$('.seg-tab', detail).forEach((tab) => {
            const on = tab.dataset.tab === name;
            tab.classList.toggle('is-active', on);
            tab.setAttribute('aria-selected', on ? 'true' : 'false');
            tab.tabIndex = on ? 0 : -1;
        });
        $$('[data-tab-panel]', detail).forEach((panel) => {
            panel.hidden = panel.dataset.tabPanel !== name;
        });

        // Save belongs to the Edit tab only: a Save button on a read-only tab
        // invites a click that cannot do anything.
        const save = $('[data-save-edit]', detail);
        if (save) save.hidden = name !== 'edit';
    }

    const VERDICT_COPY = {
        approved: 'The moderation filter passed this unchanged',
        edited: 'The moderation filter changed this',
        removed: 'The moderation filter took this down'
    };

    function fillDetails(c) {
        const name = String(c.commenter_name || 'Anonymous');
        const email = String(c.commenter_email || '');
        const removed = statusOf(c) === 'removed';
        const ai = aiOf(c);

        const set = (sel, value) => {
            const el = $(sel, detail);
            if (el) el.textContent = value;
        };

        set('[data-modal-mark]', name.trim() ? name.trim()[0].toUpperCase() : '?');
        set('[data-modal-name]', name);
        set('[data-modal-post]', c.blog_title || 'Unknown post');
        set('[data-modal-when]', absolute(c.created_at) || 'Date unknown');
        set('[data-modal-display-label]', removed ? 'Text on file — not shown to visitors' : 'On the site now');
        set('[data-modal-display]', c.display_text || c.original_text || 'The comment is empty.');
        set('[data-modal-original]', c.original_text || '');

        const mail = $('[data-modal-email]', detail);
        if (mail) {
            mail.textContent = email || 'No email address given';
            if (email) {
                mail.href = 'mailto:' + email;
                mail.removeAttribute('aria-disabled');
            } else {
                mail.removeAttribute('href');
                mail.setAttribute('aria-disabled', 'true');
            }
        }

        const status = $('[data-modal-status]', detail);
        if (status) {
            status.innerHTML = '<span class="status-pill status-' + (removed ? 'hidden' : 'live') + '">' +
                (removed ? 'Removed' : 'Visible') + '</span>';
        }

        const chip = $('[data-modal-verdict-chip]', detail);
        if (chip) {
            chip.hidden = false;
            set('[data-modal-verdict]', VERDICT_COPY[ai] || VERDICT_COPY.approved);
        }

        // The "as submitted" block is only worth its space when it differs from
        // what is on the site; otherwise it is the same paragraph printed twice.
        const originalBlock = $('[data-modal-original-block]', detail);
        if (originalBlock) {
            originalBlock.hidden = !c.original_text || c.original_text === c.display_text;
        }

        const moderatedBlock = $('[data-modal-moderated-block]', detail);
        if (moderatedBlock) {
            const worth = ai === 'edited' && c.moderated_text &&
                c.moderated_text !== c.original_text && c.moderated_text !== c.display_text;
            moderatedBlock.hidden = !worth;
            if (worth) set('[data-modal-moderated]', c.moderated_text);
        }
    }

    function logEntry(mark, markClass, title, when, reason, diff) {
        return '<div class="mod-entry">' +
            '<span class="mod-mark ' + markClass + '"><i class="bi bi-' + mark + '" aria-hidden="true"></i></span>' +
            '<div class="mod-body">' +
            '<p class="mod-title">' + title + '</p>' +
            (when ? '<p class="mod-when">' + esc(when) + '</p>' : '') +
            (reason ? '<p class="mod-reason">' + esc(reason) + '</p>' : '') +
            (diff || '') +
            '</div>' +
            '</div>';
    }

    function diffBlock(label, cls, text) {
        return '<div class="text-block ' + cls + '">' +
            '<p class="text-block-label">' + label + '</p>' +
            '<div class="text-body">' + esc(text || '') + '</div>' +
            '</div>';
    }

    const AI_TITLE = {
        approved: 'Passed by the moderation filter',
        edited: 'Changed by the moderation filter',
        removed: 'Taken down by the moderation filter'
    };

    function fillLog(c) {
        const log = $('[data-modal-log]', detail);
        if (!log) return;

        const ai = aiOf(c);
        const entries = [logEntry(
            'robot', 'is-ai',
            esc(AI_TITLE[ai] || AI_TITLE.approved),
            c.ai_moderated_at ? absolute(c.ai_moderated_at) : 'When the comment was submitted',
            c.ai_reason,
            ai === 'edited' && c.moderated_text && c.moderated_text !== c.original_text
                ? '<div class="mod-diff">' +
                  diffBlock('Before', 'is-before', c.original_text) +
                  diffBlock('After', 'is-after', c.moderated_text) +
                  '</div>'
                : ''
        )];

        (c.admin_edits || []).forEach((edit) => {
            entries.push(logEntry(
                'person-gear', 'is-admin',
                'Edited by <strong>' + esc(edit.admin_name || 'an admin') + '</strong>',
                edit.edited_at ? absolute(edit.edited_at) : '',
                edit.reason,
                '<div class="mod-diff">' +
                diffBlock('Before', 'is-before', edit.previous_text) +
                diffBlock('After', 'is-after', edit.new_text) +
                '</div>'
            ));
        });

        log.innerHTML = entries.join('');
    }

    async function openDetail(id) {
        if (typeof window.closeAllDropdowns === 'function') window.closeAllDropdowns();
        if (typeof window.showActionLoader === 'function') window.showActionLoader('Opening comment…');

        try {
            const data = await getJSON('/api/comments/' + encodeURIComponent(id));
            const c = data.comment;
            state.current = c;

            fillDetails(c);
            fillLog(c);

            const text = $('#commentEditText');
            const reason = $('#commentEditReason');
            if (text) text.value = c.display_text || '';
            if (reason) reason.value = '';

            selectTab('details');
            openModal('commentModal');
        } catch (error) {
            toast('error', 'Could not open it', error.message);
        } finally {
            if (typeof window.hideActionLoader === 'function') window.hideActionLoader();
        }
    }

    if (detail) {
        detail.addEventListener('click', (e) => {
            const tab = e.target.closest('.seg-tab[data-tab]');
            if (tab) selectTab(tab.dataset.tab);
        }, { signal });
    }

    // ----------------------------------------------------------------------
    // Actions
    // ----------------------------------------------------------------------

    function busy(btn, label) {
        if (!btn) return () => { };
        const html = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm" aria-hidden="true"></span> ' + label;
        return () => {
            btn.disabled = false;
            btn.innerHTML = html;
        };
    }

    // The shared overlay rather than an inline busy state: remove and restore
    // are reachable from a 32px icon button with no room for a spinner and a
    // word.
    async function mutate(url, method, working, done, message) {
        if (typeof window.closeAllDropdowns === 'function') window.closeAllDropdowns();
        if (typeof window.showActionLoader === 'function') window.showActionLoader(working);

        try {
            // The CSRF header is added by the fetch wrapper in app.js. The
            // empty JSON body is not decoration: /remove reads request body
            // through get_json(), which raises 415 on a bodyless POST under
            // Flask 3 — the old page sent nothing and the route answered every
            // "Remove from Site" with a 500.
            await getJSON(url, method === 'POST'
                ? { method, headers: { 'Content-Type': 'application/json' }, body: '{}' }
                : { method });
            await Promise.all([load(), refreshStats()]);
            toast('success', done, message);
        } catch (error) {
            toast('error', 'Nothing changed', error.message);
        } finally {
            if (typeof window.hideActionLoader === 'function') window.hideActionLoader();
        }
    }

    async function copy(text, what) {
        if (!text) {
            toast('info', 'Nothing to copy', 'This commenter left no email address.');
            return;
        }
        try {
            await navigator.clipboard.writeText(text);
            toast('success', 'Copied', what + ' is on your clipboard.');
        } catch (e) {
            // Denied permission, or an insecure origin. Better to show the
            // value than to claim a copy that did not happen.
            toast('info', 'Copy it by hand', text);
        }
    }

    function openDelete(row) {
        if (typeof window.closeAllDropdowns === 'function') window.closeAllDropdowns();
        state.pendingDelete = { id: row.dataset.id || '', text: row.dataset.text || '' };

        const quote = $('[data-delete-quote]', root);
        if (quote) quote.textContent = state.pendingDelete.text || 'This comment is empty.';
        openModal('deleteCommentModal');
    }

    root.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-action]');
        if (!btn || !root.contains(btn)) return;

        const action = btn.dataset.action;

        if (action === 'retry') {
            e.preventDefault();
            load();
            return;
        }

        const row = btn.closest('.data-row');
        if (!row) return;
        e.preventDefault();

        const id = row.dataset.id || '';

        if (action === 'view') {
            openDetail(id);
        } else if (action === 'remove') {
            mutate('/api/comments/' + encodeURIComponent(id) + '/remove', 'POST',
                'Taking it down…', 'Taken down', 'The comment is no longer on your site.');
        } else if (action === 'restore') {
            mutate('/api/comments/' + encodeURIComponent(id) + '/restore', 'POST',
                'Putting it back…', 'Back on the site', 'Readers can see the comment again.');
        } else if (action === 'copy-email') {
            copy(row.dataset.email || '', 'The email address');
        } else if (action === 'delete') {
            openDelete(row);
        }
    }, { signal });

    // --- Save an edit -----------------------------------------------------

    const saveBtn = $('[data-save-edit]', root);
    if (saveBtn) {
        saveBtn.addEventListener('click', async () => {
            if (!state.current) return;

            const text = ($('#commentEditText').value || '').trim();
            const reason = ($('#commentEditReason').value || '').trim();

            if (!text) {
                toast('error', 'Nothing to save', 'A comment cannot be left empty. Take it down instead.');
                return;
            }
            if (text === (state.current.display_text || '')) {
                toast('info', 'Nothing to save', 'The text is unchanged.');
                return;
            }

            const done = busy(saveBtn, 'Saving…');
            try {
                await getJSON('/api/comments/' + encodeURIComponent(state.current.id) + '/edit', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ text, reason })
                });

                closeModal('commentModal');
                state.current = null;
                await load();
                toast('success', 'Comment updated', 'Visitors now see the edited text.');
            } catch (error) {
                toast('error', 'Not saved', error.message);
            } finally {
                done();
            }
        }, { signal });
    }

    // --- Delete -----------------------------------------------------------

    const deleteBtn = $('[data-delete-confirm]', root);
    if (deleteBtn) {
        deleteBtn.addEventListener('click', async () => {
            if (!state.pendingDelete) return;
            const { id } = state.pendingDelete;
            const done = busy(deleteBtn, 'Deleting…');

            try {
                await getJSON('/api/comments/' + encodeURIComponent(id) + '/delete', { method: 'DELETE' });
                closeModal('deleteCommentModal');
                state.pendingDelete = null;
                await Promise.all([load(), refreshStats()]);
                toast('success', 'Comment deleted', 'It is gone, along with its moderation history.');
            } catch (error) {
                toast('error', 'Not deleted', error.message);
            } finally {
                done();
            }
        }, { signal });
    }

    // ----------------------------------------------------------------------
    // Boot
    //
    // The first page is already on screen from the server, so nothing is
    // fetched here: the timestamps are converted in place, the pager is drawn
    // from the counts the template published on #commentsList, and the list is
    // only re-fetched once a filter, a page or a write asks for it.
    // ----------------------------------------------------------------------

    paintTimes();
    paintHead();
    paintPager();
    selectTab('details');

    // syncThemeControls only runs on DOMContentLoaded, which PJAX never fires.
    if (typeof window.syncThemeControls === 'function') window.syncThemeControls();
})();
