/**
 * Users — the team table: members, pending invitations, role and removal.
 *
 * Was an inline <script> in users.html declaring seven globals (loadUsers,
 * openEditRole, confirmDeleteUser, executeDeleteUser, resendInvitation,
 * toggleActionMenu) plus a hand-rolled dropdown with its own open/close state.
 * A second, older copy of the same page lived at static/js/users.js and
 * redefined the app-wide showToast; it was loaded by nothing and has been
 * deleted.
 *
 * Rows are the shared .data-row, so nothing here builds a table. Actions are
 * delegated off the page root and read their target from data attributes on
 * the row rather than from arguments interpolated into an onclick — the old
 * markup escaped names for HTML but not for the single-quoted JS string it
 * then dropped them into, so anyone called O'Brien had an Edit Role button
 * that threw a SyntaxError.
 *
 * PJAX re-injects this file on every visit to /users/manage-users, so nothing
 * holds a reference across navigations and every document-level listener goes
 * through an AbortController the next run aborts.
 */

(function usersPage() {
    'use strict';

    if (window.__usersAbort) {
        try { window.__usersAbort.abort(); } catch (e) { /* already gone */ }
    }
    const controller = new AbortController();
    const signal = controller.signal;
    window.__usersAbort = controller;

    const $ = (sel, root) => (root || document).querySelector(sel);
    const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

    const root = $('.dashboard-main');
    const list = $('#usersList');
    if (!root || !list) return;

    const state = {
        query: '',
        pendingDelete: null   // { uid, name }
    };

    // ----------------------------------------------------------------------
    // Helpers
    // ----------------------------------------------------------------------

    // Escapes all five characters Jinja's autoescape does, not just the three
    // the `div.textContent -> div.innerHTML` trick covers: every value below
    // also lands in an attribute, and names and email addresses are supplied
    // by whoever signed up.
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

    // The server stores roles uppercase and /users/list hands back whatever is
    // on the document, so anything that is not ADMIN is treated as an editor.
    function normRole(role) {
        return String(role || '').trim().toUpperCase() === 'ADMIN' ? 'ADMIN' : 'EDITOR';
    }

    const ROLE_LABEL = { ADMIN: 'Admin', EDITOR: 'Editor' };

    const ROLE_HINT = {
        ADMIN: 'Publishes without review, and can invite, promote and remove other users.',
        EDITOR: 'Writes and submits posts for an admin to review. No access to this screen.'
    };

    function formatDate(value) {
        if (!value) return '';
        const d = new Date(value);
        if (isNaN(d.getTime())) return '';
        return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
    }

    // Two letters from the name where there is one, otherwise one from the
    // address. `Array.from` rather than slice(0, 2) so an emoji or an accented
    // pair does not get cut through the middle of a surrogate.
    function initials(name, email) {
        const source = (name || '').trim() || (email || '').trim();
        if (!source) return '?';
        const parts = source.split(/\s+/).filter(Boolean);
        const chars = parts.length > 1
            ? [parts[0][0], parts[1][0]]
            : Array.from(source).slice(0, 2);
        return chars.join('').toUpperCase();
    }

    function plural(n, word) {
        return n + ' ' + word + (n === 1 ? '' : 's');
    }

    // ----------------------------------------------------------------------
    // Row rendering
    // ----------------------------------------------------------------------

    function rolePill(role) {
        const cls = role === 'ADMIN' ? 'is-admin' : 'is-editor';
        return '<span class="role-pill ' + cls + '">' + ROLE_LABEL[role] + '</span>';
    }

    // Rendered on every row and revealed by CSS only once the role column has
    // been dropped on a narrow canvas — the role is what this screen is for,
    // so it is the one thing that must not vanish with the column.
    function roleMeta(role) {
        return '<span class="row-sep row-meta-role" aria-hidden="true">·</span>' +
            '<span class="row-meta-role">' + ROLE_LABEL[role] + '</span>';
    }

    function rowMenu(label, items) {
        return '<div class="dropdown">' +
            '<button type="button" class="btn-dropdown-trigger" data-bs-toggle="dropdown" aria-expanded="false" ' +
            'aria-label="More actions for ' + label + '">' +
            '<i class="bi bi-three-dots-vertical" aria-hidden="true"></i>' +
            '</button>' +
            '<ul class="dropdown-menu dropdown-menu-end">' + items.join('') + '</ul>' +
            '</div>';
    }

    function menuItem(action, icon, text, danger) {
        return '<li><button type="button" class="dropdown-item' + (danger ? ' text-danger' : '') + '" ' +
            'data-action="' + action + '">' +
            '<i class="bi bi-' + icon + '" aria-hidden="true"></i> ' + text +
            '</button></li>';
    }

    function memberRow(user) {
        const uid = String(user.uid || '');
        const name = String(user.name || user.username || '').trim();
        const email = String(user.email || '').trim();
        const role = normRole(user.role);
        const title = name || email || 'Unknown user';
        const joined = formatDate(user.created_at);

        // Where there is no name the address is already the title, so the meta
        // line would otherwise print it twice.
        const meta = [];
        if (name && email) meta.push('<span>' + esc(email) + '</span>');
        if (joined) {
            if (meta.length) meta.push('<span class="row-sep" aria-hidden="true">·</span>');
            meta.push('<span>Joined ' + esc(joined) + '</span>');
        }

        const label = esc(title);

        return '<div class="data-row" data-uid="' + esc(uid) + '" data-name="' + label + '" ' +
            'data-email="' + esc(email) + '" data-role="' + role + '" ' +
            'data-search="' + esc((title + ' ' + email).toLowerCase()) + '">' +

            '<span class="row-mark" aria-hidden="true">' + esc(initials(name, email)) + '</span>' +

            '<span class="row-main">' +
            '<span class="row-title">' + label + '</span>' +
            '<span class="row-meta">' + meta.join('') + roleMeta(role) + '</span>' +
            '</span>' +

            rolePill(role) +
            '<span class="status-pill status-active">Active</span>' +

            '<div class="row-trail">' +
            '<button type="button" class="row-action" data-action="edit-role" ' +
            'aria-label="Change role for ' + label + '" title="Change role">' +
            '<i class="bi bi-person-gear" aria-hidden="true"></i>' +
            '</button>' +
            rowMenu(label, [
                menuItem('edit-role', 'person-gear', 'Change role'),
                menuItem('copy-email', 'envelope', 'Copy email address'),
                '<li><hr class="dropdown-divider"></li>',
                menuItem('delete', 'trash3', 'Remove user', true)
            ]) +
            '</div>' +
            '</div>';
    }

    function inviteRow(inv) {
        const email = String(inv.email || '').trim();
        const role = normRole(inv.role);
        const invited = formatDate(inv.invited_at);
        const label = esc(email || 'Invitation');

        // The address is the title. The old row split it at the @ and printed
        // the local part as a username, which read as an account that exists.
        const meta = invited
            ? '<span>Invited ' + esc(invited) + '</span>'
            : '<span>Invitation not accepted yet</span>';

        return '<div class="data-row is-invite" data-email="' + esc(email) + '" data-name="' + label + '" ' +
            'data-role="' + role + '" data-search="' + esc(email.toLowerCase()) + '">' +

            '<span class="row-mark" aria-hidden="true"><i class="bi bi-envelope"></i></span>' +

            '<span class="row-main">' +
            '<span class="row-title">' + label + '</span>' +
            '<span class="row-meta">' + meta + roleMeta(role) + '</span>' +
            '</span>' +

            rolePill(role) +
            '<span class="status-pill status-invited">Invited</span>' +

            '<div class="row-trail">' +
            '<button type="button" class="row-action" data-action="resend" ' +
            'aria-label="Resend the invitation to ' + label + '" title="Resend invitation">' +
            '<i class="bi bi-arrow-clockwise" aria-hidden="true"></i>' +
            '</button>' +
            rowMenu(label, [
                menuItem('resend', 'send', 'Resend invitation'),
                // /users/invite answers with a signup_url and a message telling
                // the admin to "share the link below" — a link the page never
                // showed them. It is derivable, so the row offers it.
                menuItem('copy-link', 'link-45deg', 'Copy signup link')
            ]) +
            '</div>' +
            '</div>';
    }

    // ----------------------------------------------------------------------
    // States
    // ----------------------------------------------------------------------

    function loadingState() {
        return '<div class="users-state">' +
            '<div class="spinner-border spinner-border-sm text-primary opacity-50" role="status"></div>' +
            '<p>Loading people…</p>' +
            '</div>';
    }

    function emptyState() {
        return '<div class="list-empty">' +
            '<span class="list-empty-icon"><i class="bi bi-people" aria-hidden="true"></i></span>' +
            '<p>No one else has been added yet. Invite an editor to write alongside you, or another ' +
            'admin to share the running of the site.</p>' +
            '<button type="button" class="app-btn is-primary" data-bs-toggle="modal" ' +
            'data-bs-target="#inviteUserModal">Invite your first user</button>' +
            '</div>';
    }

    function errorState(message) {
        return '<div class="list-empty">' +
            '<span class="list-empty-icon"><i class="bi bi-exclamation-triangle" aria-hidden="true"></i></span>' +
            '<p>' + esc(message || 'The list of people could not be loaded.') + '</p>' +
            '<button type="button" class="app-btn is-ghost" data-action="retry">Try again</button>' +
            '</div>';
    }

    // ----------------------------------------------------------------------
    // Painting
    // ----------------------------------------------------------------------

    function paintSummary(users, invites) {
        const count = $('[data-users-count]');
        const note = $('[data-users-note]');
        const total = users.length + invites.length;

        if (count) count.textContent = String(total);
        if (!note) return;

        if (!total) {
            note.textContent = '';
            return;
        }

        const admins = users.filter((u) => normRole(u.role) === 'ADMIN').length;
        const parts = [];
        if (admins) parts.push(plural(admins, 'admin'));
        if (users.length - admins) parts.push(plural(users.length - admins, 'editor'));
        if (invites.length) parts.push(plural(invites.length, 'invitation') + ' pending');
        note.textContent = parts.join(' · ');
    }

    function render(data) {
        const users = (data && data.users) || [];
        const invites = ((data && data.invitations) || []).filter((inv) => inv.status === 'pending');

        paintSummary(users, invites);
        list.setAttribute('aria-busy', 'false');

        if (!users.length && !invites.length) {
            list.innerHTML = emptyState();
            applyQuery();
            return;
        }

        // Members first, then the invitations: one group is the team and the
        // other is the waiting room, and sorting by name across both would
        // interleave them.
        const byName = (a, b) => String(a.name || a.username || a.email || '')
            .localeCompare(String(b.name || b.username || b.email || ''), undefined, { sensitivity: 'base' });

        list.innerHTML = '<div class="data-rows">' +
            users.slice().sort(byName).map(memberRow).join('') +
            invites.map(inviteRow).join('') +
            '</div>';

        applyQuery();
    }

    // ----------------------------------------------------------------------
    // Loading
    //
    // app.js starts a /users/list fetch on load and again on hover or focus of
    // any link to this page, and parks the result on window.__usersListPrefetch*.
    // Reuse it when it is there; fall back to the network when it is not.
    // ----------------------------------------------------------------------

    const CACHE_TTL = 2 * 60 * 1000;

    function cached() {
        const cache = window.__usersListPrefetchCache;
        if (!cache || !cache.data || !cache.fetchedAt) return null;
        if ((Date.now() - cache.fetchedAt) > CACHE_TTL) return null;
        return cache.data;
    }

    // Every write below invalidates it: the next paint must come from the
    // server, not from a snapshot taken before the change.
    function clearCache() {
        window.__usersListPrefetchCache = null;
        window.__usersListPrefetchPromise = null;
    }

    async function fetchList() {
        const res = await fetch('/users/list', {
            method: 'GET',
            headers: { Accept: 'application/json' },
            credentials: 'same-origin'
        });

        // A session that has expired is answered with the login page, not with
        // JSON, so a bare res.json() would throw a parse error instead of
        // saying what actually happened.
        const type = res.headers.get('content-type') || '';
        if (!type.includes('application/json')) {
            throw new Error('Your session has expired. Reload the page to sign in again.');
        }
        if (!res.ok) throw new Error('The server returned ' + res.status + '.');

        const data = await res.json();
        if (!data.success) throw new Error(data.error || 'The list of people could not be loaded.');

        window.__usersListPrefetchCache = { data, fetchedAt: Date.now() };
        return data;
    }

    async function load() {
        const hit = cached();
        if (hit) {
            render(hit);
            return;
        }

        list.setAttribute('aria-busy', 'true');
        list.innerHTML = loadingState();

        try {
            let data;
            if (window.__usersListPrefetchPromise) {
                try {
                    data = await window.__usersListPrefetchPromise;
                } catch (e) {
                    data = await fetchList();
                }
            } else {
                data = await fetchList();
            }
            render(data);
        } catch (error) {
            list.setAttribute('aria-busy', 'false');
            list.innerHTML = errorState(error.message);
        }
    }

    async function reload() {
        clearCache();
        await load();
    }

    // ----------------------------------------------------------------------
    // Search
    //
    // Driven by the page header's `page-search` event, filtering rows in place
    // through the `hidden` attribute rather than an inline display, which would
    // fight .data-row's own grid.
    // ----------------------------------------------------------------------

    function applyQuery() {
        const rows = $$('#usersList .data-row');
        const none = $('[data-noresults]');
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
    // Role picker
    //
    // .seg-tabs wearing role="radiogroup": one button is pressed, and the hint
    // underneath says what that role can do. Roving tabindex so the group is a
    // single tab stop and the arrow keys move within it, as a radio group has
    // to be.
    // ----------------------------------------------------------------------

    function setRole(group, role) {
        if (!group) return;
        const wanted = normRole(role);

        $$('.seg-tab', group).forEach((tab) => {
            const on = tab.dataset.role === wanted;
            tab.classList.toggle('is-active', on);
            tab.setAttribute('aria-checked', on ? 'true' : 'false');
            tab.tabIndex = on ? 0 : -1;
        });

        const hint = group.parentElement && $('[data-role-hint]', group.parentElement);
        if (hint) hint.textContent = ROLE_HINT[wanted];
    }

    function roleOf(group) {
        const on = group && $('.seg-tab.is-active', group);
        return normRole(on && on.dataset.role);
    }

    root.addEventListener('click', (e) => {
        const tab = e.target.closest('[data-role-choice] .seg-tab');
        if (!tab) return;
        setRole(tab.closest('[data-role-choice]'), tab.dataset.role);
    }, { signal });

    root.addEventListener('keydown', (e) => {
        const tab = e.target.closest('[data-role-choice] .seg-tab');
        if (!tab) return;
        if (['ArrowRight', 'ArrowDown', 'ArrowLeft', 'ArrowUp'].indexOf(e.key) === -1) return;

        e.preventDefault();
        const group = tab.closest('[data-role-choice]');
        const tabs = $$('.seg-tab', group);
        const step = (e.key === 'ArrowRight' || e.key === 'ArrowDown') ? 1 : -1;
        const next = tabs[(tabs.indexOf(tab) + step + tabs.length) % tabs.length];

        setRole(group, next.dataset.role);
        next.focus();
    }, { signal });

    // ----------------------------------------------------------------------
    // Modals
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

    // Both of these can be reached from the row's ⋮ menu, which would otherwise
    // still be open behind the backdrop when the dialog appears.
    function openEditRole(row) {
        if (typeof window.closeAllDropdowns === 'function') window.closeAllDropdowns();
        const idField = $('#editUserId');
        const nameField = $('#editUserName');
        if (idField) idField.value = row.dataset.uid || '';
        if (nameField) nameField.textContent = row.dataset.name || 'this user';
        setRole($('#editRoleForm [data-role-choice]'), row.dataset.role);
        openModal('editRoleModal');
    }

    function openDelete(row) {
        if (typeof window.closeAllDropdowns === 'function') window.closeAllDropdowns();
        state.pendingDelete = { uid: row.dataset.uid || '', name: row.dataset.name || 'This user' };
        const nameEl = $('#deleteUserName');
        if (nameEl) nameEl.textContent = state.pendingDelete.name;
        openModal('deleteUserModal');
    }

    // ----------------------------------------------------------------------
    // Actions
    // ----------------------------------------------------------------------

    // Buttons that talk to the server say so while they are doing it, and are
    // restored on whichever branch finishes.
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

    async function post(url, body) {
        // The CSRF header is added by the fetch wrapper in app.js.
        const res = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify(body)
        });

        const type = res.headers.get('content-type') || '';
        if (!type.includes('application/json')) {
            throw new Error('Your session has expired. Reload the page to sign in again.');
        }

        const data = await res.json();
        if (!data.success) throw new Error(data.error || 'The server rejected that.');
        return data;
    }

    async function copy(text, what) {
        try {
            await navigator.clipboard.writeText(text);
            toast('success', 'Copied', what + ' is on your clipboard.');
        } catch (e) {
            // Denied permission, or an insecure origin. Better to show the
            // value than to claim a copy that did not happen.
            toast('info', 'Copy it by hand', text);
        }
    }

    // The shared overlay rather than an inline busy state: resend is reachable
    // from a 32px icon button, which has no room for a spinner and a word.
    async function resend(email) {
        if (typeof window.showActionLoader === 'function') {
            window.showActionLoader('Resending invitation…');
        }
        try {
            await post('/users/resend-invite', { email });
            toast('success', 'Invitation resent', 'A fresh invitation is on its way to ' + email + '.');
        } catch (error) {
            toast('error', 'Not resent', error.message);
        } finally {
            if (typeof window.hideActionLoader === 'function') window.hideActionLoader();
        }
    }

    root.addEventListener('click', async (e) => {
        const btn = e.target.closest('[data-action]');
        if (!btn || !root.contains(btn)) return;

        const action = btn.dataset.action;

        if (action === 'retry') {
            e.preventDefault();
            reload();
            return;
        }

        const row = btn.closest('.data-row');
        if (!row) return;
        e.preventDefault();

        if (action === 'edit-role') {
            openEditRole(row);
        } else if (action === 'delete') {
            openDelete(row);
        } else if (action === 'copy-email') {
            copy(row.dataset.email || '', 'The email address');
        } else if (action === 'copy-link') {
            // The same URL /users/invite builds server-side.
            copy(window.location.origin + '/signup?invite=' +
                encodeURIComponent(row.dataset.email || ''), 'The signup link');
        } else if (action === 'resend') {
            resend(row.dataset.email || '');
        }
    }, { signal });

    // --- Invite -----------------------------------------------------------

    const inviteForm = $('#inviteUserForm');
    if (inviteForm) {
        inviteForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const submit = $('button[type="submit"]', inviteForm);
            const done = busy(submit, 'Sending…');

            try {
                const data = await post('/users/invite', {
                    email: ($('#inviteEmail').value || '').trim(),
                    role: roleOf($('[data-role-choice]', inviteForm))
                });

                closeModal('inviteUserModal');
                inviteForm.reset();
                setRole($('[data-role-choice]', inviteForm), 'EDITOR');
                await reload();

                // The invitation exists either way; whether the email actually
                // went out is the part the admin has to know about, because it
                // decides whether they need to send the link themselves.
                if (data.email_sent === false && data.signup_url) {
                    toast('warning', 'Invitation created',
                        'The email could not be delivered. Copy the signup link from the row to send it yourself.');
                } else {
                    toast('success', 'Invitation sent', data.message || 'The invitation is on its way.');
                }
            } catch (error) {
                toast('error', 'Not invited', error.message);
            } finally {
                done();
            }
        }, { signal });
    }

    // --- Change role ------------------------------------------------------

    const editRoleForm = $('#editRoleForm');
    if (editRoleForm) {
        editRoleForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const submit = $('button[type="submit"]', editRoleForm);
            const done = busy(submit, 'Saving…');
            const name = ($('#editUserName') || {}).textContent || 'That user';
            const role = roleOf($('[data-role-choice]', editRoleForm));

            try {
                // `username` is not read by the update itself — it is what the
                // activity log records the change against.
                await post('/users/update-role', {
                    userId: ($('#editUserId').value || ''),
                    role,
                    username: name
                });

                closeModal('editRoleModal');
                await reload();
                toast('success', 'Role changed', name + ' is now ' +
                    (role === 'ADMIN' ? 'an admin' : 'an editor') + '.');
            } catch (error) {
                toast('error', 'Role unchanged', error.message);
            } finally {
                done();
            }
        }, { signal });
    }

    // --- Remove -----------------------------------------------------------

    const confirmDelete = $('#confirmDeleteBtn');
    if (confirmDelete) {
        confirmDelete.addEventListener('click', async () => {
            if (!state.pendingDelete) return;
            const { uid, name } = state.pendingDelete;
            const done = busy(confirmDelete, 'Removing…');

            try {
                await post('/users/delete-user', { userId: uid, username: name });
                closeModal('deleteUserModal');
                state.pendingDelete = null;
                await reload();
                toast('success', 'User removed', name + ' no longer has access.');
            } catch (error) {
                toast('error', 'Not removed', error.message);
            } finally {
                done();
            }
        }, { signal });
    }

    // ----------------------------------------------------------------------
    // Boot
    // ----------------------------------------------------------------------

    $$('[data-role-choice]').forEach((group) => setRole(group, roleOf(group)));
    load();

    // syncThemeControls only runs on DOMContentLoaded, which PJAX never fires.
    if (typeof window.syncThemeControls === 'function') window.syncThemeControls();
})();
