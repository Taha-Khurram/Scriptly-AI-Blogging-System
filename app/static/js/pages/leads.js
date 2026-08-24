/**
 * Leads — the contact-form inbox: read a message, answer it, delete it.
 *
 * Was eleven globals (loadLeads, goToPage, toggleDropdown, viewLeadById,
 * viewLead, closeViewModal, showDeleteConfirm, closeDeleteModal, deleteLead,
 * markAsRead, refreshStats) wired to the rows through
 * `onclick="viewLeadById('<id>')"` strings, a hand-rolled dropdown with its own
 * document click handler, a hand-rolled modal overlay with no focus trap and no
 * Esc key, and a `leadsCache` object seeded from a `window.__initialLeads`
 * global. Actions are now delegated off the page root and read their target
 * from data attributes on the row, which is the only copy of the record.
 *
 * One behaviour worth naming: marking a lead read used to find the row with
 * `.lead-row[onclick*="<id>"]`, which matched on a substring of an attribute
 * that no longer exists. Rows carry their id now.
 *
 * Rows are the shared .data-row, so nothing here builds a table. The first
 * page is rendered by Jinja (see the lead_row macro in leads.html) and this
 * file renders the identical structure for every page, filter and search after
 * it — the two have to stay in step.
 *
 * PJAX re-injects this file on every visit to /leads, so nothing holds a
 * reference across navigations and every document-level listener goes through
 * an AbortController the next run aborts.
 */

(function leadsPage() {
    'use strict';

    if (window.__leadsAbort) {
        try { window.__leadsAbort.abort(); } catch (e) { /* already gone */ }
    }
    const controller = new AbortController();
    const signal = controller.signal;
    window.__leadsAbort = controller;

    const $ = (sel, root) => (root || document).querySelector(sel);
    const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

    const root = $('.dashboard-main');
    const list = $('#leadsList');
    if (!root || !list) return;

    const state = {
        filter: 'all',
        query: '',
        page: parseInt(list.dataset.page, 10) || 1,
        total: parseInt(list.dataset.total, 10) || 0,
        totalPages: parseInt(list.dataset.totalPages, 10) || 1,
        current: null,        // the lead open in the dialog, as a plain object
        pendingDelete: null   // { id, name }
    };

    let searchTimer = null;

    // ----------------------------------------------------------------------
    // Helpers
    // ----------------------------------------------------------------------

    // Escapes all five characters Jinja's autoescape does, not just the three
    // the `div.textContent -> div.innerHTML` trick covers: names, addresses,
    // subjects and message bodies all land in attributes here, and every one of
    // them was typed by an anonymous visitor into a public form.
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

    function paintTimes() {
        $$('time[data-relative]', list).forEach((el) => {
            const text = relative(el.getAttribute('datetime'));
            if (!text) return;
            el.title = absolute(el.getAttribute('datetime'));
            el.textContent = text;
        });
    }

    // The one place a reply address is assembled, so the row shortcut, the menu
    // item and the dialog's Reply button all produce the same thing.
    function mailtoFor(lead) {
        if (!lead.email) return '';
        const subject = lead.subject ? 'Re: ' + lead.subject : 'Re: your message';
        return 'mailto:' + encodeURIComponent(lead.email).replace(/%40/g, '@') +
            '?subject=' + encodeURIComponent(subject);
    }

    // A row is the record. Reading the dialog's contents out of the DOM rather
    // than from a parallel cache means there is only one thing that can be
    // stale.
    function leadFrom(row) {
        return {
            id: row.dataset.id || '',
            name: row.dataset.name || 'Unknown sender',
            email: row.dataset.email || '',
            subject: row.dataset.subject || '',
            message: row.dataset.message || '',
            when: row.dataset.when || '',
            read: row.dataset.read === 'true'
        };
    }

    // ----------------------------------------------------------------------
    // Row rendering — mirrors the lead_row macro in leads.html
    // ----------------------------------------------------------------------

    function menuItem(action, icon, text, danger) {
        return '<li><button type="button" class="dropdown-item' + (danger ? ' text-danger' : '') + '" ' +
            'data-action="' + action + '">' +
            '<i class="bi bi-' + icon + '" aria-hidden="true"></i> ' + text +
            '</button></li>';
    }

    function leadRow(lead) {
        const unread = !lead.read;
        const name = String(lead.name || 'Unknown sender');
        const email = String(lead.email || '');
        const subject = String(lead.subject || '');
        const when = lead.created_at || '';
        const label = esc(name);

        const meta = ['<span>' + label + '</span>'];
        if (email) {
            meta.push('<span class="row-sep" aria-hidden="true">·</span>');
            meta.push('<span>' + esc(email) + '</span>');
        }
        if (when) {
            meta.push('<span class="row-sep row-meta-when" aria-hidden="true">·</span>');
            meta.push('<time class="row-meta-when" datetime="' + esc(when) + '" data-relative>' +
                esc(String(when).slice(0, 10)) + '</time>');
        }
        if (unread) {
            meta.push('<span class="row-sep row-meta-unread" aria-hidden="true">·</span>');
            meta.push('<span class="row-meta-unread">Unread</span>');
        }

        const mark = name.trim() ? esc(name.trim()[0].toUpperCase()) : '?';

        // Both of these cells are rendered even when empty: the flag and date
        // columns are fixed tracks, and a missing child would slide everything
        // after it left on that row alone.
        const flag = unread
            ? '<span class="status-pill status-unread">Unread</span>'
            : '<span class="status-pill-blank"></span>';
        const date = when
            ? '<time class="row-time" datetime="' + esc(when) + '" data-relative>' +
              esc(String(when).slice(0, 10)) + '</time>'
            : '<span class="row-time-blank"></span>';

        const menu = [menuItem('view', 'envelope-open', 'Open message')];
        if (email) {
            menu.push(menuItem('reply', 'reply', 'Reply by email'));
            menu.push(menuItem('copy-email', 'envelope', 'Copy email address'));
        }
        if (unread) menu.push(menuItem('mark-read', 'check2-all', 'Mark as read'));
        menu.push('<li><hr class="dropdown-divider"></li>');
        menu.push(menuItem('delete', 'trash3', 'Delete lead', true));

        return '<div class="data-row' + (unread ? ' is-unread' : '') + '" id="lead-row-' + esc(lead.id) + '" ' +
            'data-id="' + esc(lead.id) + '" data-name="' + label + '" data-email="' + esc(email) + '" ' +
            'data-subject="' + esc(subject) + '" data-message="' + esc(lead.message || '') + '" ' +
            'data-when="' + esc(when) + '" data-read="' + (unread ? 'false' : 'true') + '" ' +
            'data-search="' + esc((name + ' ' + email + ' ' + subject).toLowerCase()) + '">' +

            '<span class="row-mark" aria-hidden="true">' + mark + '</span>' +

            '<button type="button" class="row-open" data-action="view" ' +
            'title="Open the message from ' + label + '">' +
            '<span class="row-title' + (subject ? '' : ' is-placeholder') + '">' +
            (esc(subject) || 'No subject') + '</span>' +
            '<span class="row-meta">' + meta.join('') + '</span>' +
            '</button>' +

            flag + date +

            '<div class="row-trail">' +
            (email
                ? '<button type="button" class="row-action" data-action="reply" title="Reply by email" ' +
                  'aria-label="Reply to ' + label + ' by email">' +
                  '<i class="bi bi-reply" aria-hidden="true"></i></button>'
                : '') +
            '<div class="dropdown">' +
            '<button type="button" class="btn-dropdown-trigger" data-bs-toggle="dropdown" aria-expanded="false" ' +
            'aria-label="More actions for the message from ' + label + '">' +
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
        all: 'Nothing yet. When a visitor fills in the contact form on your site, their message ' +
            'arrives here.',
        unread: 'Nothing unread — you have been through the whole inbox.',
        read: 'Nothing has been read yet.'
    };

    function loadingState() {
        return '<div class="leads-state">' +
            '<div class="spinner-border spinner-border-sm text-primary opacity-50" role="status"></div>' +
            '<p>Loading leads…</p>' +
            '</div>';
    }

    function emptyState() {
        const copy = state.query
            ? 'Nothing matches “' + esc(state.query) + '”. The search covers the sender, their ' +
              'address and the subject line.'
            : esc(EMPTY_COPY[state.filter] || EMPTY_COPY.all);

        return '<div class="list-empty">' +
            '<span class="list-empty-icon"><i class="bi bi-inbox" aria-hidden="true"></i></span>' +
            '<p>' + copy + '</p>' +
            '</div>';
    }

    function errorState(message) {
        return '<div class="list-empty">' +
            '<span class="list-empty-icon"><i class="bi bi-exclamation-triangle" aria-hidden="true"></i></span>' +
            '<p>' + esc(message || 'The inbox could not be loaded.') + '</p>' +
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

    function paintPager() {
        const nav = $('[data-pager]', root);
        if (!nav) return;

        const last = Math.max(1, state.totalPages);
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

    const FILTER_NOTE = { all: '', unread: 'Showing unread only', read: 'Showing read only' };

    function paintHead() {
        const count = $('[data-list-count]', root);
        const note = $('[data-list-note]', root);

        if (count) count.textContent = String(state.total);
        if (!note) return;

        const parts = [];
        if (state.totalPages > 1) parts.push('Page ' + state.page + ' of ' + state.totalPages);
        if (state.query) parts.push('Matching “' + state.query + '”');
        else if (FILTER_NOTE[state.filter]) parts.push(FILTER_NOTE[state.filter]);
        note.textContent = parts.join(' · ');
    }

    function render(data) {
        const leads = (data && data.submissions) || [];

        state.total = Number(data && data.total) || 0;
        state.page = Number(data && data.page) || state.page;
        state.totalPages = Math.max(1, Number(data && data.total_pages) || 1);

        list.setAttribute('aria-busy', 'false');
        list.innerHTML = leads.length ? leads.map(leadRow).join('') : emptyState();

        paintTimes();
        paintHead();
        paintPager();
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

        const params = new URLSearchParams({ page: state.page });
        if (state.filter !== 'all') params.set('status', state.filter);
        if (state.query) params.set('search', state.query);

        try {
            render(await getJSON('/api/leads?' + params.toString()));
        } catch (error) {
            list.setAttribute('aria-busy', 'false');
            list.innerHTML = errorState(error.message);
            paintPager();
        }
    }

    // ----------------------------------------------------------------------
    // Stats
    //
    // Refreshed after every write, because opening a lead moves it between two
    // of the tiles and two of the tab counts — leaving them stale would show a
    // figure that contradicts the list right beside it.
    // ----------------------------------------------------------------------

    function paintStats(stats) {
        const total = Number(stats.total) || 0;
        const unread = Number(stats.unread) || 0;
        const read = Number(stats.read) || 0;

        const set = (sel, value) => {
            const el = $(sel, root);
            if (el) el.textContent = String(value);
        };

        set('[data-stat-total]', total);
        set('[data-stat-unread]', unread);
        set('[data-stat-read]', read);
        set('[data-count-all]', total);
        set('[data-count-unread]', unread);
        set('[data-count-read]', read);

        const meter = $('[data-meter]', root);
        const fill = $('[data-meter-fill]', root);
        const note = $('[data-meter-note]', root);
        const share = total ? Math.round((unread / total) * 100) : 0;

        if (meter) meter.hidden = total === 0;
        if (fill) fill.style.width = share + '%';
        if (note) note.textContent = share + '% of the inbox';
    }

    async function refreshStats() {
        try {
            const data = await getJSON('/api/leads/stats');
            if (data.stats) paintStats(data.stats);
        } catch (e) {
            // Non-critical: the list beside them is the source of truth.
        }
    }

    // ----------------------------------------------------------------------
    // Search
    //
    // Goes to the API rather than filtering the rendered rows, because the
    // server searches the sender, the address and the subject across every
    // page — an inbox you can only search one page of is not searchable.
    // ----------------------------------------------------------------------

    document.addEventListener('page-search', (e) => {
        const value = ((e.detail && e.detail.value) || '').trim();

        // The no-change check has to happen inside the timer, not before it:
        // typing a letter and deleting it again lands back on the current query
        // but leaves a scheduled fetch for the letter behind, and an early
        // return here would let that one run.
        clearTimeout(searchTimer);
        searchTimer = setTimeout(() => {
            if (value === state.query) return;
            state.query = value;
            state.page = 1;
            load();
        }, 300);
    }, { signal });

    // ----------------------------------------------------------------------
    // Filter tabs and pager
    // ----------------------------------------------------------------------

    root.addEventListener('click', (e) => {
        const tab = e.target.closest('.leads-filter .seg-tab');
        if (tab) {
            if (tab.dataset.filter === state.filter) return;
            $$('.leads-filter .seg-tab', root).forEach((t) => {
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
    // Dialogs
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

    const detail = document.getElementById('leadModal');

    function openDetail(row) {
        if (typeof window.closeAllDropdowns === 'function') window.closeAllDropdowns();
        if (!detail) return;

        const lead = leadFrom(row);
        state.current = lead;

        const set = (sel, value) => {
            const el = $(sel, detail);
            if (el) el.textContent = value;
        };

        set('[data-modal-subject]', lead.subject || 'No subject');
        set('[data-modal-name]', lead.name);
        set('[data-modal-when]', absolute(lead.when) || 'Date unknown');
        set('[data-modal-message]', lead.message || 'They left the message empty.');

        const mail = $('[data-modal-email]', detail);
        if (mail) {
            mail.textContent = lead.email || 'No address given';
            if (lead.email) {
                mail.href = 'mailto:' + lead.email;
                mail.removeAttribute('aria-disabled');
            } else {
                mail.removeAttribute('href');
                mail.setAttribute('aria-disabled', 'true');
            }
        }

        // No address, nothing to reply to — so the button goes rather than
        // sitting there as the primary action and doing nothing.
        const reply = $('[data-modal-reply]', detail);
        if (reply) {
            reply.hidden = !lead.email;
            if (lead.email) reply.href = mailtoFor(lead);
        }

        openModal('leadModal');

        // Opening it is reading it. Fire-and-forget: the row and the tiles are
        // repainted from the response, and a failure here is not worth a toast
        // over a dialog the reader has just opened.
        if (!lead.read) markRead(lead.id).catch(() => { });
    }

    function openDelete(row) {
        if (typeof window.closeAllDropdowns === 'function') window.closeAllDropdowns();
        const lead = leadFrom(row);
        state.pendingDelete = { id: lead.id, name: lead.name };

        const name = $('[data-delete-name]', root);
        if (name) name.textContent = lead.name;
        openModal('deleteLeadModal');
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

    // Marking read is repainted in place rather than by reloading the list: on
    // the Unread tab a reload would pull the row out from under the dialog the
    // reader has just opened on top of it.
    async function markRead(id) {
        // The CSRF header is added by the fetch wrapper in app.js.
        await getJSON('/api/leads/' + encodeURIComponent(id) + '/read', { method: 'POST' });

        const row = document.getElementById('lead-row-' + id);
        if (row) {
            row.classList.remove('is-unread');
            row.dataset.read = 'true';

            // An empty span, not nothing: the flag column is a fixed track, so
            // removing the cell outright would slide the date left on this row
            // alone.
            const pill = $('.status-pill.status-unread', row);
            if (pill) {
                const blank = document.createElement('span');
                blank.className = 'status-pill-blank';
                pill.replaceWith(blank);
            }

            $$('.row-meta-unread', row).forEach((el) => el.remove());

            const item = $('[data-action="mark-read"]', row);
            if (item && item.closest('li')) item.closest('li').remove();
        }

        await refreshStats();
    }

    async function copy(text, what) {
        if (!text) {
            toast('info', 'Nothing to copy', 'This lead came in without an address.');
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

    function reply(row) {
        if (typeof window.closeAllDropdowns === 'function') window.closeAllDropdowns();
        const lead = leadFrom(row);
        if (!lead.email) {
            toast('info', 'No address', 'This lead came in without an address to reply to.');
            return;
        }
        window.location.href = mailtoFor(lead);
        // Answering it counts as reading it.
        if (!lead.read) markRead(lead.id).catch(() => { });
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

        if (action === 'view') {
            openDetail(row);
        } else if (action === 'reply') {
            reply(row);
        } else if (action === 'copy-email') {
            copy(row.dataset.email || '', 'The email address');
        } else if (action === 'mark-read') {
            if (typeof window.closeAllDropdowns === 'function') window.closeAllDropdowns();
            markRead(row.dataset.id || '')
                .then(() => toast('success', 'Marked as read', 'It is out of your unread list.'))
                .catch((error) => toast('error', 'Nothing changed', error.message));
        } else if (action === 'delete') {
            openDelete(row);
        }
    }, { signal });

    // Delete from inside the message dialog: the confirmation replaces it
    // rather than stacking on top, so there is only ever one dialog open.
    const modalDelete = $('[data-modal-delete]', root);
    if (modalDelete) {
        modalDelete.addEventListener('click', () => {
            if (!state.current) return;
            const row = document.getElementById('lead-row-' + state.current.id);
            closeModal('leadModal');
            if (row) setTimeout(() => openDelete(row), 200);
        }, { signal });
    }

    const deleteBtn = $('[data-delete-confirm]', root);
    if (deleteBtn) {
        deleteBtn.addEventListener('click', async () => {
            if (!state.pendingDelete) return;
            const { id, name } = state.pendingDelete;
            const done = busy(deleteBtn, 'Deleting…');

            try {
                await getJSON('/api/leads/' + encodeURIComponent(id) + '/delete', { method: 'POST' });

                closeModal('deleteLeadModal');
                state.pendingDelete = null;
                state.current = null;

                // Deleting the last lead on the last page would otherwise
                // leave the reader looking at an empty one. 10 is not a guess:
                // /api/leads fixes per_page at 10 server-side.
                const remaining = Math.max(1, Math.ceil((state.total - 1) / 10));
                if (state.page > remaining) state.page = remaining;

                await Promise.all([load(), refreshStats()]);
                toast('success', 'Lead deleted', 'The message from ' + name + ' is gone.');
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
    // fetched here: the timestamps are converted in place and the pager is
    // drawn from the counts the template published on #leadsList. The list is
    // only re-fetched once a filter, a search, a page or a write asks for it.
    // ----------------------------------------------------------------------

    paintTimes();
    paintHead();
    paintPager();

    // syncThemeControls only runs on DOMContentLoaded, which PJAX never fires.
    if (typeof window.syncThemeControls === 'function') window.syncThemeControls();
})();
