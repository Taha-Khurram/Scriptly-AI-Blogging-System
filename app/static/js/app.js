/**
 * Scriptly AI - Main Application Logic
 * Handles: Toast notifications, page transitions (pjax), session management
 */

// --------------------------------------------------------------------------
// DOM Elements
// --------------------------------------------------------------------------

const pageLoader = document.getElementById("page-loader");
const navProgress = document.getElementById("nav-progress");
const toastContainer = document.getElementById("toast-container");

// --------------------------------------------------------------------------
// Toast Notification System
// --------------------------------------------------------------------------

// Defined as a function declaration (not `const`) so that if a page script ever
// declares a top-level name that collides with a global helper, it overwrites it
// gracefully instead of throwing "Identifier already declared" — which would
// abort the entire page script and leave the page dead. See the PJAX notes below.
function showToast(options) {
    const { type = 'success', title, message, duration = 4000 } = options || {};

    const icons = {
        success: 'bi-check-circle-fill',
        error: 'bi-x-circle-fill',
        warning: 'bi-exclamation-triangle-fill',
        info: 'bi-info-circle-fill'
    };

    const toast = document.createElement('div');
    toast.className = 'custom-toast';
    toast.innerHTML = `
        <div class="toast-icon ${type}">
            <i class="bi ${icons[type]}"></i>
        </div>
        <div class="toast-content">
            <div class="toast-title">${title}</div>
            <div class="toast-message">${message}</div>
        </div>
        <button class="toast-close" onclick="this.closest('.custom-toast').remove()">
            <i class="bi bi-x"></i>
        </button>
        <div class="toast-progress" style="animation-duration: ${duration}ms;"></div>
    `;

    if (toastContainer) {
        toastContainer.appendChild(toast);

        requestAnimationFrame(() => {
            toast.classList.add('show');
        });

        setTimeout(() => {
            toast.classList.add('hiding');
            toast.classList.remove('show');
            setTimeout(() => toast.remove(), 400);
        }, duration);
    }

    return toast;
};

window.showToast = showToast;

// --------------------------------------------------------------------------
// Theme (light / dark)
//
// The stored choice is applied to <html data-theme> by an inline script in
// base.html before first paint; this layer only handles switching at runtime
// and keeping every visible toggle in sync. No stored value means "follow the
// OS", which is the default the token layer already implements.
// --------------------------------------------------------------------------

const THEME_KEY = 'scriptly-theme';

function getStoredTheme() {
    try {
        return localStorage.getItem(THEME_KEY);
    } catch (e) {
        return null;
    }
}

/** The theme actually on screen right now, resolving "system" to what it means. */
function getActiveTheme() {
    const explicit = document.documentElement.getAttribute('data-theme');
    if (explicit === 'dark' || explicit === 'light') return explicit;
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function syncThemeControls() {
    const active = getActiveTheme();
    const nextLabel = active === 'dark' ? 'Switch to light theme' : 'Switch to dark theme';

    document.querySelectorAll('[data-theme-toggle]').forEach((el) => {
        el.setAttribute('aria-label', nextLabel);
        el.setAttribute('title', nextLabel);
        el.setAttribute('aria-pressed', active === 'dark' ? 'true' : 'false');

        // The icon shows the theme you would switch *to*, which is the
        // convention users read fastest.
        const icon = el.querySelector('.material-symbols-outlined');
        if (icon) icon.textContent = active === 'dark' ? 'light_mode' : 'dark_mode';

        const label = el.querySelector('[data-theme-label]');
        if (label) label.textContent = active === 'dark' ? 'Light theme' : 'Dark theme';
    });

    // Keep the browser chrome (address bar / title bar) on the same surface.
    const chrome = active === 'dark' ? '#131314' : '#F0F4F9';
    document.querySelectorAll('meta[name="theme-color"]').forEach((m) => {
        m.setAttribute('content', chrome);
    });
}

function setTheme(theme) {
    if (theme === 'system') {
        document.documentElement.removeAttribute('data-theme');
        try { localStorage.removeItem(THEME_KEY); } catch (e) { }
    } else {
        document.documentElement.setAttribute('data-theme', theme);
        try { localStorage.setItem(THEME_KEY, theme); } catch (e) { }
    }
    syncThemeControls();
}

function toggleTheme() {
    setTheme(getActiveTheme() === 'dark' ? 'light' : 'dark');
}

window.setTheme = setTheme;
window.toggleTheme = toggleTheme;
window.getActiveTheme = getActiveTheme;

document.addEventListener('click', (e) => {
    if (e.target.closest('[data-theme-toggle]')) {
        e.preventDefault();
        toggleTheme();
    }
});

// While the user is on "system", follow the OS live.
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (!getStoredTheme()) syncThemeControls();
});

document.addEventListener('DOMContentLoaded', syncThemeControls);

// --------------------------------------------------------------------------
// Page header
//
// Drives partials/page_header.html: the condense-on-scroll state and the
// search field's keyboard affordances.
//
// The header lives *inside* .dashboard-main, so PJAX destroys and rebuilds it
// on every navigation. Everything here is therefore either delegated off
// document/window or re-read at event time — nothing holds a reference to the
// element across a page change. The search field owns no behaviour of its own:
// it emits `page-search` and the page decides what that means.
// --------------------------------------------------------------------------

(function initPageHeader() {
    const STUCK_ON = 12;   // px scrolled before the bar condenses…
    const STUCK_OFF = 4;   // …and where it relaxes again. The gap is hysteresis,
    // so a header sitting exactly on the threshold cannot
    // flicker as its own height change nudges the scroll.
    let ticking = false;

    function applyStuck() {
        ticking = false;
        const header = document.querySelector('[data-page-header]');
        if (!header) return;

        const y = window.scrollY || document.documentElement.scrollTop || 0;
        if (y > STUCK_ON) header.classList.add('is-stuck');
        else if (y < STUCK_OFF) header.classList.remove('is-stuck');
    }

    function onScroll() {
        if (ticking) return;
        ticking = true;
        requestAnimationFrame(applyStuck);
    }

    window.addEventListener('scroll', onScroll, { passive: true });

    function getSearchInput() {
        return document.querySelector('[data-page-search] .page-search-input');
    }

    function emitSearch(value, submit) {
        document.dispatchEvent(new CustomEvent('page-search', {
            detail: { value: value, submit: !!submit }
        }));
    }

    function syncSearchState(input) {
        const box = input.closest('[data-page-search]');
        if (box) box.classList.toggle('has-value', input.value.trim() !== '');
    }

    // ---- Results dropdown --------------------------------------------------
    //
    // The header renders it but does not know what a result *is*: a page
    // answers `page-search` with `page-search-results` and this fills the
    // panel from that. Pages that stay silent never open it, which is why the
    // dropdown can ship in the shared partial without touching every screen.

    const RESULT_LIMIT = 8;

    function getSearchPanel() {
        return document.querySelector('[data-page-search-panel]');
    }

    function resultsOpen() {
        const panel = getSearchPanel();
        return !!panel && !panel.hidden;
    }

    function setExpanded(open, active) {
        const input = getSearchInput();
        if (!input) return;
        input.setAttribute('aria-expanded', open ? 'true' : 'false');
        if (open && active && active.id) input.setAttribute('aria-activedescendant', active.id);
        else input.removeAttribute('aria-activedescendant');
    }

    function closeResults() {
        const panel = getSearchPanel();
        if (!panel || panel.hidden) return;
        panel.hidden = true;
        panel.innerHTML = '';
        setExpanded(false);
    }

    function resultOptions() {
        const panel = getSearchPanel();
        if (!panel || panel.hidden) return [];
        return Array.from(panel.querySelectorAll('.page-search-result, .page-search-foot'));
    }

    function activeOption() {
        return resultOptions().find((el) => el.classList.contains('is-active')) || null;
    }

    function activate(target) {
        resultOptions().forEach((el) => {
            const on = el === target;
            el.classList.toggle('is-active', on);
            if (el.getAttribute('role') === 'option') {
                el.setAttribute('aria-selected', on ? 'true' : 'false');
            }
        });
        setExpanded(true, target);
        if (target) target.scrollIntoView({ block: 'nearest' });
    }

    function moveActive(step) {
        const opts = resultOptions();
        if (!opts.length) return;
        const at = opts.indexOf(activeOption());
        // Wrap in both directions: from nothing selected, Down lands on the
        // first row and Up on the last.
        const next = ((at === -1 ? (step > 0 ? -1 : 0) : at) + step + opts.length) % opts.length;
        activate(opts[next]);
    }

    // The query is user content coming back through the DOM, so matches are
    // marked up by splicing text nodes rather than by building HTML.
    function withHighlight(text, query) {
        const frag = document.createDocumentFragment();
        const needle = query.toLowerCase();
        const hay = text.toLowerCase();

        let from = 0;
        let at = needle ? hay.indexOf(needle) : -1;
        while (at !== -1) {
            if (at > from) frag.appendChild(document.createTextNode(text.slice(from, at)));
            const hit = document.createElement('mark');
            hit.className = 'page-search-hit';
            hit.textContent = text.slice(at, at + needle.length);
            frag.appendChild(hit);
            from = at + needle.length;
            at = hay.indexOf(needle, from);
        }
        frag.appendChild(document.createTextNode(text.slice(from)));
        return frag;
    }

    function resultNode(item, id, query) {
        const row = document.createElement('a');
        row.className = 'page-search-result';
        row.id = id;
        row.href = item.href || '#';
        row.setAttribute('role', 'option');
        row.setAttribute('aria-selected', 'false');

        if (item.mark) {
            const mark = document.createElement('span');
            mark.className = 'page-search-result-mark';
            mark.setAttribute('aria-hidden', 'true');
            mark.textContent = item.mark;
            row.appendChild(mark);
        }

        const main = document.createElement('span');
        main.className = 'page-search-result-main';

        const title = document.createElement('span');
        title.className = 'page-search-result-title';
        title.appendChild(withHighlight(String(item.title || 'Untitled'), query));
        main.appendChild(title);

        if (item.meta) {
            const meta = document.createElement('span');
            meta.className = 'page-search-result-meta';
            meta.textContent = item.meta;
            main.appendChild(meta);
        }

        row.appendChild(main);

        if (item.status) {
            const pill = document.createElement('span');
            pill.className = 'status-pill status-' + String(item.status).toLowerCase();
            pill.textContent = item.statusLabel || item.status;
            row.appendChild(pill);
        }

        return row;
    }

    function renderResults(detail) {
        const panel = getSearchPanel();
        const input = getSearchInput();
        if (!panel || !input) return;

        const query = String(detail.query != null ? detail.query : input.value).trim();
        if (!query) {
            closeResults();
            return;
        }

        const items = (detail.items || []).slice(0, detail.limit || RESULT_LIMIT);
        panel.innerHTML = '';

        if (items.length) {
            items.forEach((item, i) => {
                panel.appendChild(resultNode(item, panel.id + '-opt-' + i, query));
            });
        } else {
            const empty = document.createElement('p');
            empty.className = 'page-search-empty';
            empty.textContent = detail.empty || 'Nothing here matches “' + query + '”.';
            panel.appendChild(empty);
        }

        if (detail.footer && detail.footer.href) {
            const foot = document.createElement('a');
            foot.className = 'page-search-foot';
            foot.id = panel.id + '-foot';
            foot.href = detail.footer.href;
            foot.textContent = detail.footer.label || 'See all results';
            panel.appendChild(foot);
        }

        panel.hidden = false;
        setExpanded(true);
        activate(null);

        // Bound on the panel rather than the document so it costs nothing while
        // the dropdown is closed. Re-adding the same function to the same
        // element is a no-op, and PJAX throws the element away with its
        // listener, so this stays a single live binding.
        panel.addEventListener('mouseover', onPanelHover);
    }

    // Pointer and keyboard share one cursor: hovering a row makes it the row
    // Enter would open, so the two cannot disagree about what is selected.
    function onPanelHover(e) {
        const row = e.target.closest('.page-search-result, .page-search-foot');
        if (row) activate(row);
    }

    document.addEventListener('page-search-results', (e) => {
        renderResults((e && e.detail) || {});
    });

    document.addEventListener('input', (e) => {
        const input = e.target.closest('[data-page-search] .page-search-input');
        if (!input) return;
        syncSearchState(input);
        if (!input.value.trim()) closeResults();
        emitSearch(input.value, false);
    });

    // Re-ask on focus so returning to a field that still holds a query brings
    // its results back instead of leaving the reader staring at a dead box.
    document.addEventListener('focusin', (e) => {
        const box = e.target.closest && e.target.closest('[data-page-search]');
        if (!box) {
            closeResults();
            return;
        }
        const input = getSearchInput();
        if (input && e.target === input && input.value.trim() && !resultsOpen()) {
            emitSearch(input.value, false);
        }
    });

    document.addEventListener('click', (e) => {
        if (e.target.closest('[data-page-search-clear]')) {
            const input = getSearchInput();
            if (!input) return;
            input.value = '';
            syncSearchState(input);
            closeResults();
            emitSearch('', false);
            input.focus();
            return;
        }

        // Closing tears the anchor out of the document, so it has to wait for
        // the delegated PJAX handler further down this file to see the click.
        if (e.target.closest('.page-search-result, .page-search-foot')) {
            setTimeout(closeResults, 0);
            return;
        }

        // A click on the panel's own padding or scrollbar is not a dismissal.
        if (!e.target.closest('[data-page-search]')) closeResults();
    });

    document.addEventListener('keydown', (e) => {
        const input = getSearchInput();
        if (!input) return;

        const inField = document.activeElement === input;

        if (inField && resultsOpen() && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
            e.preventDefault();
            moveActive(e.key === 'ArrowDown' ? 1 : -1);
            return;
        }

        if (inField && e.key === 'Enter') {
            e.preventDefault();
            // A highlighted row wins over the page's own submit handling —
            // the reader picked that result, not the query.
            const chosen = activeOption();
            if (chosen) {
                chosen.click();
                closeResults();
                return;
            }
            emitSearch(input.value, true);
            return;
        }

        if (inField && e.key === 'Escape') {
            // One dismissal per press: the panel first, the query second.
            if (resultsOpen()) {
                closeResults();
                return;
            }
            if (input.value) {
                input.value = '';
                syncSearchState(input);
                emitSearch('', false);
            } else {
                input.blur();
            }
            return;
        }

        // ⌘K / Ctrl+K from anywhere, and a bare "/" when the user is not
        // already typing into something.
        const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)
            || document.activeElement.isContentEditable;

        if ((e.key === 'k' || e.key === 'K') && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            input.focus();
            input.select();
        } else if (e.key === '/' && !typing && !e.metaKey && !e.ctrlKey && !e.altKey) {
            e.preventDefault();
            input.focus();
        }
    });

    // The shortcut hint has to name the key the reader actually has.
    function labelShortcut() {
        const platform = (navigator.userAgentData && navigator.userAgentData.platform)
            || navigator.platform || '';
        if (!/mac|iphone|ipad|ipod/i.test(platform)) return;
        document.querySelectorAll('[data-search-hint]').forEach((el) => {
            el.textContent = '⌘ K';
        });
    }

    function refresh() {
        labelShortcut();
        applyStuck();
    }

    document.addEventListener('DOMContentLoaded', refresh);
    document.addEventListener('pjax:complete', refresh);
})();

// --------------------------------------------------------------------------
// Select pill
//
// Drives the .select-pill / .menu pair: a listbox we own, wrapped around a
// native <select> that stays the value holder. Choosing an item writes through
// to the select and fires a real `change` event, so page code keeps reading
// `.value` and listening for `change` exactly as it did with a bare select.
//
// Everything is delegated off `document` and re-read at event time — the pills
// live inside .dashboard-main, which PJAX rebuilds on every navigation.
// --------------------------------------------------------------------------

(function initSelectPill() {

    function pillOf(el) { return el.closest('[data-select-pill]'); }
    function menuOf(pill) { return pill.querySelector('.menu'); }
    function selectOf(pill) { return pill.querySelector('select'); }
    function triggerOf(pill) { return pill.querySelector('[data-select-trigger]'); }

    function closeAll(except) {
        document.querySelectorAll('[data-select-pill]').forEach((pill) => {
            if (pill === except) return;
            const menu = menuOf(pill);
            if (menu && !menu.hidden) {
                menu.hidden = true;
                const trigger = triggerOf(pill);
                if (trigger) trigger.setAttribute('aria-expanded', 'false');
            }
        });
    }

    // Mark the item matching the select's current value. Done on every open so
    // the menu is correct even when the value was changed elsewhere — a "Clear
    // all" button, say, or a query string on load.
    function syncItems(pill) {
        const select = selectOf(pill);
        if (!select) return;
        pill.querySelectorAll('.menu-item').forEach((item) => {
            const on = item.dataset.value === select.value;
            item.classList.toggle('is-selected', on);
            item.setAttribute('aria-selected', on ? 'true' : 'false');
        });
    }

    function open(pill) {
        const menu = menuOf(pill);
        const trigger = triggerOf(pill);
        if (!menu) return;

        closeAll(pill);
        syncItems(pill);
        menu.hidden = false;
        if (trigger) trigger.setAttribute('aria-expanded', 'true');

        const selected = menu.querySelector('.menu-item.is-selected') || menu.querySelector('.menu-item');
        if (selected) selected.focus();
    }

    function close(pill, refocus) {
        const menu = menuOf(pill);
        const trigger = triggerOf(pill);
        if (menu) menu.hidden = true;
        if (trigger) {
            trigger.setAttribute('aria-expanded', 'false');
            if (refocus) trigger.focus();
        }
    }

    function choose(pill, item) {
        const select = selectOf(pill);
        if (select && select.value !== item.dataset.value) {
            select.value = item.dataset.value;
            select.dispatchEvent(new Event('change', { bubbles: true }));
        }
        syncItems(pill);
        close(pill, true);
    }

    document.addEventListener('click', (e) => {
        const trigger = e.target.closest('[data-select-trigger]');
        if (trigger) {
            e.preventDefault();
            const pill = pillOf(trigger);
            const menu = menuOf(pill);
            if (menu && menu.hidden) open(pill); else close(pill, false);
            return;
        }

        const item = e.target.closest('.menu-item');
        if (item && pillOf(item)) {
            e.preventDefault();
            choose(pillOf(item), item);
            return;
        }

        // Anywhere else dismisses.
        if (!e.target.closest('[data-select-pill]')) closeAll(null);
    });

    document.addEventListener('keydown', (e) => {
        const pill = pillOf(e.target);
        if (!pill) return;

        const menu = menuOf(pill);
        if (!menu) return;

        if (e.key === 'Escape') {
            if (!menu.hidden) { e.preventDefault(); close(pill, true); }
            return;
        }

        const onTrigger = !!e.target.closest('[data-select-trigger]');

        if (menu.hidden) {
            if (onTrigger && (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ')) {
                e.preventDefault();
                open(pill);
            }
            return;
        }

        const items = Array.from(menu.querySelectorAll('.menu-item'));
        const at = items.indexOf(e.target.closest('.menu-item'));

        if (e.key === 'ArrowDown') {
            e.preventDefault();
            (items[at + 1] || items[0]).focus();
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            (items[at - 1] || items[items.length - 1]).focus();
        } else if (e.key === 'Home') {
            e.preventDefault();
            items[0].focus();
        } else if (e.key === 'End') {
            e.preventDefault();
            items[items.length - 1].focus();
        }
    });

    // A menu left open across a PJAX navigation would be orphaned mid-air.
    document.addEventListener('pjax:complete', () => closeAll(null));
})();

// --------------------------------------------------------------------------
// Account menu
//
// Lives in the sidebar, which sits outside .dashboard-main and so survives
// PJAX navigation — everything here is delegated off `document` and bound once,
// rather than re-wired per page.
// --------------------------------------------------------------------------

function getUserMenu() {
    return document.getElementById('userMenu');
}

function setUserMenuOpen(open) {
    const menu = getUserMenu();
    if (!menu) return;

    menu.hidden = !open;
    document.querySelectorAll('[data-user-menu]').forEach((el) => {
        if (el.hasAttribute('aria-expanded')) {
            el.setAttribute('aria-expanded', open ? 'true' : 'false');
        }
    });

    if (open) {
        // Send focus into the menu so keyboard users land somewhere useful.
        const first = menu.querySelector('a, button');
        if (first) first.focus();
    }
}

function closeUserMenu(refocus) {
    const menu = getUserMenu();
    if (!menu || menu.hidden) return;
    setUserMenuOpen(false);
    if (refocus) {
        const trigger = document.querySelector('.user-card-avatar[data-user-menu]');
        if (trigger) trigger.focus();
    }
}

document.addEventListener('click', (e) => {
    const menu = getUserMenu();
    if (!menu) return;

    if (e.target.closest('[data-user-menu]')) {
        e.preventDefault();
        setUserMenuOpen(menu.hidden);
        return;
    }

    if (e.target.closest('[data-user-menu-close]')) {
        e.preventDefault();
        closeUserMenu(true);
        return;
    }

    // A click on a link inside the menu should navigate, not just close.
    if (menu.contains(e.target)) {
        if (e.target.closest('a')) closeUserMenu(false);
        return;
    }

    closeUserMenu(false);
});

document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeUserMenu(true);
});

// Deliberately no resize handler: the menu is anchored to the rail in CSS, so
// it follows the rail width on its own. Closing it on resize only meant it
// vanished whenever the browser fired a spurious resize — a device-pixel-ratio
// change, or a mobile address bar sliding away mid-interaction.

// --------------------------------------------------------------------------
// Page Loader Functions
// --------------------------------------------------------------------------

function showLoader() {
    if (pageLoader) pageLoader.classList.remove("hidden");
    if (navProgress) navProgress.classList.add("loading");
}

function hideLoader() {
    if (pageLoader) pageLoader.classList.add("hidden");
    if (navProgress) {
        navProgress.classList.remove("loading");
        navProgress.style.width = "100%";
        setTimeout(() => { navProgress.style.width = "0%"; }, 300);
    }
}

window.showLoader = showLoader;
window.hideLoader = hideLoader;

// --------------------------------------------------------------------------
// Global Dropdown + Action Loader Helpers
//
// These give every page a single, consistent loading pattern (mirroring the
// blog-generation loader): whenever a user triggers an async action — most
// often by picking an option from a dropdown menu — the dropdown is closed and
// a full-screen overlay with a spinner + message is shown until the action
// finishes.
// --------------------------------------------------------------------------

// Close any open Bootstrap dropdown menu on the page.
function closeAllDropdowns() {
    document.querySelectorAll('.dropdown-menu.show').forEach((menu) => {
        const trigger = menu.parentElement
            ? menu.parentElement.querySelector('[data-bs-toggle="dropdown"]')
            : null;
        if (trigger && window.bootstrap && bootstrap.Dropdown) {
            try {
                bootstrap.Dropdown.getOrCreateInstance(trigger).hide();
                return;
            } catch (e) { /* fall through to manual removal */ }
        }
        menu.classList.remove('show');
    });
}

window.closeAllDropdowns = closeAllDropdowns;

// Lazily create (once) and return the shared action-loader overlay element.
function getActionLoader() {
    let overlay = document.getElementById('action-loader');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'action-loader';
        overlay.className = 'hidden';
        overlay.setAttribute('role', 'status');
        overlay.setAttribute('aria-live', 'polite');
        overlay.innerHTML = `
            <div class="action-loader-card">
                <div class="spinner-border text-primary" role="status"></div>
                <span class="action-loader-text">Working...</span>
            </div>`;
        document.body.appendChild(overlay);
    }
    return overlay;
}

// Show the shared full-screen action loader with an optional message.
function showActionLoader(message) {
    const overlay = getActionLoader();
    const text = overlay.querySelector('.action-loader-text');
    if (text) text.textContent = message || 'Working...';
    // Force reflow so the CSS transition runs even right after creation.
    void overlay.offsetWidth;
    overlay.classList.remove('hidden');
}

// Update the message shown on the action loader without toggling visibility.
function updateActionLoader(message) {
    const overlay = document.getElementById('action-loader');
    if (!overlay) return;
    const text = overlay.querySelector('.action-loader-text');
    if (text && message) text.textContent = message;
}

// Hide the shared full-screen action loader.
function hideActionLoader() {
    const overlay = document.getElementById('action-loader');
    if (overlay) overlay.classList.add('hidden');
}

window.showActionLoader = showActionLoader;
window.updateActionLoader = updateActionLoader;
window.hideActionLoader = hideActionLoader;

// --------------------------------------------------------------------------
// Pjax Navigation System (SPA-like transitions)
// --------------------------------------------------------------------------

// Patch DOMContentLoaded to support dynamically loaded scripts (pjax)
// When scripts are loaded after initial page load, their DOMContentLoaded
// listeners would never fire. This makes them fire immediately instead.
const _origAddEventListener = Document.prototype.addEventListener;
Document.prototype.addEventListener = function(type, fn, options) {
    if (type === 'DOMContentLoaded' && document.readyState !== 'loading') {
        setTimeout(fn, 0);
    } else {
        _origAddEventListener.call(this, type, fn, options);
    }
};

const Pjax = (() => {
    let currentAbortController = null;
    let isNavigating = false;
    let currentPageStyles = [];
    const USERS_CACHE_TTL_MS = 2 * 60 * 1000;

    function getUsersCache(maxAgeMs = USERS_CACHE_TTL_MS) {
        const cache = window.__usersListPrefetchCache;
        if (!cache || !cache.data || !cache.fetchedAt) return null;
        if ((Date.now() - cache.fetchedAt) > maxAgeMs) return null;
        return cache.data;
    }

    function setUsersCache(data) {
        if (!data) return;
        window.__usersListPrefetchCache = {
            data,
            fetchedAt: Date.now()
        };
    }

    function isUsersRoute(url) {
        const pathname = new URL(url, window.location.origin).pathname;
        return pathname === '/users/manage-users' || pathname.startsWith('/users/manage-users');
    }

    function prefetchUsersList() {
        const cachedData = getUsersCache();
        if (cachedData) return Promise.resolve(cachedData);
        if (window.__usersListPrefetchPromise) return window.__usersListPrefetchPromise;

        window.__usersListPrefetchPromise = fetch('/users/list', {
            method: 'GET',
            headers: { 'Accept': 'application/json' },
            credentials: 'same-origin'
        }).then(async (response) => {
            const contentType = response.headers.get('content-type') || '';
            if (!contentType.includes('application/json')) {
                throw new Error('Session expired or server error. Please refresh the page.');
            }
            if (!response.ok) {
                throw new Error(`Server returned ${response.status}`);
            }
            return response.json();
        }).then((data) => {
            if (!data || !data.success) {
                throw new Error((data && data.error) || 'Failed to fetch users.');
            }
            setUsersCache(data);
            window.__usersListPrefetchPromise = null;
            return data;
        }).catch((error) => {
            window.__usersListPrefetchPromise = null;
            throw error;
        });

        return window.__usersListPrefetchPromise;
    }

    // Page-specific skeleton templates matching actual page structures
    const skeletons = {
        // Dashboard Home: greeting + 3 stat cards + 3-column card grid with blog lists
        dashboard: `
            <header class="dashboard-header skeleton-header">
                <div><div class="skeleton skeleton-text" style="height:14px;width:120px;margin-bottom:8px;"></div><div class="skeleton skeleton-title" style="width:200px;"></div></div>
            </header>
            <div class="skeleton-stat-grid">
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:48px;height:48px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:28px;width:50px;"></div></div></div>
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:48px;height:48px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:28px;width:50px;"></div></div></div>
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:48px;height:48px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:28px;width:50px;"></div></div></div>
            </div>
            <div class="skeleton-grid" style="grid-template-columns:repeat(auto-fit,minmax(280px,1fr));">
                <div class="skeleton-card"><div class="skeleton skeleton-text" style="height:18px;width:100px;margin-bottom:1.25rem;"></div><div class="skeleton-list-item"><div class="skeleton skeleton-text lg"></div><div class="skeleton skeleton-text sm"></div></div><div class="skeleton-list-item"><div class="skeleton skeleton-text md"></div><div class="skeleton skeleton-text sm"></div></div><div class="skeleton-list-item"><div class="skeleton skeleton-text lg"></div><div class="skeleton skeleton-text sm"></div></div></div>
                <div class="skeleton-card"><div class="skeleton skeleton-text" style="height:18px;width:120px;margin-bottom:1.25rem;"></div><div class="skeleton-list-item"><div class="skeleton skeleton-text md"></div><div class="skeleton skeleton-text sm"></div></div><div class="skeleton-list-item"><div class="skeleton skeleton-text lg"></div><div class="skeleton skeleton-text sm"></div></div><div class="skeleton-list-item"><div class="skeleton skeleton-text md"></div><div class="skeleton skeleton-text sm"></div></div></div>
                <div class="skeleton-card"><div class="skeleton skeleton-text" style="height:18px;width:90px;margin-bottom:1.25rem;"></div><div class="skeleton-list-item"><div class="skeleton skeleton-text lg"></div><div class="skeleton skeleton-text sm"></div></div><div class="skeleton-list-item"><div class="skeleton skeleton-text md"></div><div class="skeleton skeleton-text sm"></div></div><div class="skeleton-list-item"><div class="skeleton skeleton-text lg"></div><div class="skeleton skeleton-text sm"></div></div></div>
            </div>`,

        // All Blogs: header + filter bar (status tabs + category + search) + 5-col table
        allBlogs: `
            <header class="dashboard-header skeleton-header">
                <div><div class="skeleton skeleton-text" style="height:14px;width:100px;margin-bottom:8px;"></div><div class="skeleton skeleton-title" style="width:140px;"></div></div>
            </header>
            <div class="skeleton-filter-bar">
                <div class="skeleton" style="width:55px;height:32px;border-radius:20px;"></div>
                <div class="skeleton" style="width:65px;height:32px;border-radius:20px;"></div>
                <div class="skeleton" style="width:85px;height:32px;border-radius:20px;"></div>
                <div class="skeleton" style="width:75px;height:32px;border-radius:20px;"></div>
                <div class="skeleton" style="width:70px;height:32px;border-radius:20px;"></div>
                <div style="flex:1"></div>
                <div class="skeleton" style="width:140px;height:36px;border-radius:8px;"></div>
                <div class="skeleton" style="width:200px;height:36px;border-radius:8px;"></div>
            </div>
            <div class="skeleton-table">
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:32%;"></div><div class="skeleton skeleton-text" style="width:14%;"></div><div class="skeleton skeleton-text" style="width:12%;"></div><div class="skeleton skeleton-text" style="width:10%;"></div><div class="skeleton skeleton-text" style="width:14%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:38%;"></div><div class="skeleton skeleton-text" style="width:12%;"></div><div class="skeleton skeleton-text" style="width:14%;"></div><div class="skeleton skeleton-text" style="width:10%;"></div><div class="skeleton skeleton-text" style="width:12%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:28%;"></div><div class="skeleton skeleton-text" style="width:16%;"></div><div class="skeleton skeleton-text" style="width:10%;"></div><div class="skeleton skeleton-text" style="width:10%;"></div><div class="skeleton skeleton-text" style="width:15%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:35%;"></div><div class="skeleton skeleton-text" style="width:13%;"></div><div class="skeleton skeleton-text" style="width:12%;"></div><div class="skeleton skeleton-text" style="width:10%;"></div><div class="skeleton skeleton-text" style="width:13%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:30%;"></div><div class="skeleton skeleton-text" style="width:15%;"></div><div class="skeleton skeleton-text" style="width:11%;"></div><div class="skeleton skeleton-text" style="width:10%;"></div><div class="skeleton skeleton-text" style="width:14%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:34%;"></div><div class="skeleton skeleton-text" style="width:14%;"></div><div class="skeleton skeleton-text" style="width:13%;"></div><div class="skeleton skeleton-text" style="width:10%;"></div><div class="skeleton skeleton-text" style="width:12%;"></div></div>
            </div>`,

        // Drafts: header with "New Draft" button + 4-col table (title, category, date, actions)
        drafts: `
            <header class="dashboard-header skeleton-header">
                <div><div class="skeleton skeleton-text" style="height:14px;width:80px;margin-bottom:8px;"></div><div class="skeleton skeleton-title" style="width:130px;"></div></div>
                <div class="skeleton" style="width:110px;height:38px;border-radius:8px;"></div>
            </header>
            <div class="skeleton-table">
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:40%;"></div><div class="skeleton skeleton-text" style="width:18%;"></div><div class="skeleton skeleton-text" style="width:16%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:35%;"></div><div class="skeleton skeleton-text" style="width:15%;"></div><div class="skeleton skeleton-text" style="width:18%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:45%;"></div><div class="skeleton skeleton-text" style="width:14%;"></div><div class="skeleton skeleton-text" style="width:16%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:38%;"></div><div class="skeleton skeleton-text" style="width:16%;"></div><div class="skeleton skeleton-text" style="width:15%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:42%;"></div><div class="skeleton skeleton-text" style="width:13%;"></div><div class="skeleton skeleton-text" style="width:17%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
            </div>`,

        // Categories: header with search + table (name, count badge, status, actions)
        categories: `
            <header class="dashboard-header skeleton-header">
                <div><div class="skeleton skeleton-text" style="height:14px;width:90px;margin-bottom:8px;"></div><div class="skeleton skeleton-title" style="width:140px;"></div></div>
                <div class="skeleton" style="width:180px;height:36px;border-radius:8px;"></div>
            </header>
            <div class="skeleton-table">
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:30%;"></div><div class="skeleton" style="width:40px;height:22px;border-radius:12px;"></div><div class="skeleton skeleton-text" style="width:12%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:25%;"></div><div class="skeleton" style="width:40px;height:22px;border-radius:12px;"></div><div class="skeleton skeleton-text" style="width:12%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:35%;"></div><div class="skeleton" style="width:40px;height:22px;border-radius:12px;"></div><div class="skeleton skeleton-text" style="width:12%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:28%;"></div><div class="skeleton" style="width:40px;height:22px;border-radius:12px;"></div><div class="skeleton skeleton-text" style="width:12%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:32%;"></div><div class="skeleton" style="width:40px;height:22px;border-radius:12px;"></div><div class="skeleton skeleton-text" style="width:12%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
            </div>`,

        // Gallery: header + filter/sort toolbar + tile grid with captions.
        // The upload zone this used to draw is gone — uploading is a header
        // action and a drag-anywhere overlay now, so a 110px block at the top
        // would promise a control the real page does not have.
        gallery: `
            <header class="dashboard-header skeleton-header">
                <div><div class="skeleton skeleton-text" style="height:14px;width:100px;margin-bottom:8px;"></div><div class="skeleton skeleton-title" style="width:150px;"></div></div>
                <div class="skeleton" style="width:120px;height:40px;border-radius:999px;"></div>
            </header>
            <div class="skeleton-filter-bar">
                <div class="skeleton" style="width:56px;height:34px;border-radius:999px;"></div>
                <div class="skeleton" style="width:62px;height:34px;border-radius:999px;"></div>
                <div class="skeleton" style="width:62px;height:34px;border-radius:999px;"></div>
                <div style="flex:1"></div>
                <div class="skeleton" style="width:150px;height:38px;border-radius:999px;"></div>
                <div class="skeleton" style="width:76px;height:38px;border-radius:999px;"></div>
            </div>
            <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:1rem;">
                <div><div class="skeleton" style="width:100%;aspect-ratio:1;border-radius:16px;"></div><div class="skeleton skeleton-text sm" style="width:70%;margin-top:10px;"></div></div>
                <div><div class="skeleton" style="width:100%;aspect-ratio:1;border-radius:16px;"></div><div class="skeleton skeleton-text sm" style="width:55%;margin-top:10px;"></div></div>
                <div><div class="skeleton" style="width:100%;aspect-ratio:1;border-radius:16px;"></div><div class="skeleton skeleton-text sm" style="width:65%;margin-top:10px;"></div></div>
                <div><div class="skeleton" style="width:100%;aspect-ratio:1;border-radius:16px;"></div><div class="skeleton skeleton-text sm" style="width:60%;margin-top:10px;"></div></div>
                <div><div class="skeleton" style="width:100%;aspect-ratio:1;border-radius:16px;"></div><div class="skeleton skeleton-text sm" style="width:72%;margin-top:10px;"></div></div>
                <div><div class="skeleton" style="width:100%;aspect-ratio:1;border-radius:16px;"></div><div class="skeleton skeleton-text sm" style="width:58%;margin-top:10px;"></div></div>
                <div><div class="skeleton" style="width:100%;aspect-ratio:1;border-radius:16px;"></div><div class="skeleton skeleton-text sm" style="width:66%;margin-top:10px;"></div></div>
                <div><div class="skeleton" style="width:100%;aspect-ratio:1;border-radius:16px;"></div><div class="skeleton skeleton-text sm" style="width:62%;margin-top:10px;"></div></div>
            </div>`,

        // Newsletter: header + 3 stat cards + newsletter creation card + subscribers
        // Newsletter: header + 3 stat tiles + the section tabs + the composer's
        // split editor/preview. The old skeleton drew a tall stacked form
        // because that is what the page used to be.
        newsletter: `
            <header class="dashboard-header skeleton-header">
                <div><div class="skeleton skeleton-text" style="height:14px;width:90px;margin-bottom:8px;"></div><div class="skeleton skeleton-title" style="width:140px;"></div></div>
            </header>
            <div class="skeleton-stat-grid">
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:48px;height:48px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:28px;width:50px;"></div></div></div>
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:48px;height:48px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:28px;width:50px;"></div></div></div>
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:48px;height:48px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:28px;width:50px;"></div></div></div>
            </div>
            <div class="skeleton-filter-bar" style="margin-bottom:1.5rem;">
                <div class="skeleton" style="width:96px;height:34px;border-radius:999px;"></div>
                <div class="skeleton" style="width:124px;height:34px;border-radius:999px;"></div>
                <div class="skeleton" style="width:98px;height:34px;border-radius:999px;"></div>
            </div>
            <div class="skeleton-card">
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:1.5rem;">
                    <div>
                        <div class="skeleton skeleton-text sm" style="margin-bottom:0.5rem;"></div>
                        <div class="skeleton" style="width:100%;height:40px;border-radius:12px;margin-bottom:1.25rem;"></div>
                        <div class="skeleton skeleton-text sm" style="margin-bottom:0.5rem;"></div>
                        <div class="skeleton" style="width:100%;height:88px;border-radius:12px;margin-bottom:1.25rem;"></div>
                        <div class="skeleton" style="width:100%;height:72px;border-radius:12px;"></div>
                    </div>
                    <div class="skeleton" style="width:100%;height:320px;border-radius:16px;"></div>
                </div>
            </div>`,

        // Leads: header + 3 stats + filter tabs + table (status, name, email, subject, date, actions)
        leads: `
            <header class="dashboard-header skeleton-header">
                <div><div class="skeleton skeleton-text" style="height:14px;width:70px;margin-bottom:8px;"></div><div class="skeleton skeleton-title" style="width:100px;"></div></div>
            </header>
            <div class="skeleton-stat-grid">
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:48px;height:48px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:28px;width:50px;"></div></div></div>
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:48px;height:48px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:28px;width:50px;"></div></div></div>
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:48px;height:48px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:28px;width:50px;"></div></div></div>
            </div>
            <div class="skeleton-filter-bar">
                <div class="skeleton" style="width:55px;height:32px;border-radius:20px;"></div>
                <div class="skeleton" style="width:70px;height:32px;border-radius:20px;"></div>
                <div class="skeleton" style="width:60px;height:32px;border-radius:20px;"></div>
                <div style="flex:1"></div>
                <div class="skeleton" style="width:200px;height:36px;border-radius:8px;"></div>
            </div>
            <div class="skeleton-table">
                <div class="skeleton-table-row"><div class="skeleton skeleton-circle" style="width:10px;height:10px;flex-shrink:0;"></div><div class="skeleton skeleton-text" style="width:18%;"></div><div class="skeleton skeleton-text" style="width:22%;"></div><div class="skeleton skeleton-text" style="width:24%;"></div><div class="skeleton skeleton-text" style="width:14%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-circle" style="width:10px;height:10px;flex-shrink:0;"></div><div class="skeleton skeleton-text" style="width:15%;"></div><div class="skeleton skeleton-text" style="width:24%;"></div><div class="skeleton skeleton-text" style="width:20%;"></div><div class="skeleton skeleton-text" style="width:14%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-circle" style="width:10px;height:10px;flex-shrink:0;"></div><div class="skeleton skeleton-text" style="width:20%;"></div><div class="skeleton skeleton-text" style="width:20%;"></div><div class="skeleton skeleton-text" style="width:22%;"></div><div class="skeleton skeleton-text" style="width:14%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-circle" style="width:10px;height:10px;flex-shrink:0;"></div><div class="skeleton skeleton-text" style="width:16%;"></div><div class="skeleton skeleton-text" style="width:22%;"></div><div class="skeleton skeleton-text" style="width:26%;"></div><div class="skeleton skeleton-text" style="width:14%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-circle" style="width:10px;height:10px;flex-shrink:0;"></div><div class="skeleton skeleton-text" style="width:18%;"></div><div class="skeleton skeleton-text" style="width:21%;"></div><div class="skeleton skeleton-text" style="width:23%;"></div><div class="skeleton skeleton-text" style="width:14%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
            </div>`,

        // Activity: header + 4 stats + filter bar (type tabs + user + search) + table
        activity: `
            <header class="dashboard-header skeleton-header">
                <div><div class="skeleton skeleton-text" style="height:14px;width:90px;margin-bottom:8px;"></div><div class="skeleton skeleton-title" style="width:140px;"></div></div>
            </header>
            <div class="skeleton-stat-grid" style="grid-template-columns:repeat(auto-fit,minmax(160px,1fr));">
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:42px;height:42px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:24px;width:45px;"></div></div></div>
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:42px;height:42px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:24px;width:45px;"></div></div></div>
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:42px;height:42px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:24px;width:45px;"></div></div></div>
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:42px;height:42px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:24px;width:45px;"></div></div></div>
            </div>
            <div class="skeleton-filter-bar">
                <div class="skeleton" style="width:50px;height:32px;border-radius:20px;"></div>
                <div class="skeleton" style="width:55px;height:32px;border-radius:20px;"></div>
                <div class="skeleton" style="width:60px;height:32px;border-radius:20px;"></div>
                <div class="skeleton" style="width:70px;height:32px;border-radius:20px;"></div>
                <div class="skeleton" style="width:65px;height:32px;border-radius:20px;"></div>
                <div style="flex:1"></div>
                <div class="skeleton" style="width:120px;height:36px;border-radius:8px;"></div>
                <div class="skeleton" style="width:180px;height:36px;border-radius:8px;"></div>
            </div>
            <div class="skeleton-table">
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:16%;"></div><div class="skeleton skeleton-text" style="width:22%;"></div><div class="skeleton skeleton-text" style="width:20%;"></div><div class="skeleton skeleton-text" style="width:10%;"></div><div class="skeleton skeleton-text" style="width:14%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:14%;"></div><div class="skeleton skeleton-text" style="width:25%;"></div><div class="skeleton skeleton-text" style="width:18%;"></div><div class="skeleton skeleton-text" style="width:10%;"></div><div class="skeleton skeleton-text" style="width:14%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:18%;"></div><div class="skeleton skeleton-text" style="width:20%;"></div><div class="skeleton skeleton-text" style="width:22%;"></div><div class="skeleton skeleton-text" style="width:10%;"></div><div class="skeleton skeleton-text" style="width:14%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:15%;"></div><div class="skeleton skeleton-text" style="width:24%;"></div><div class="skeleton skeleton-text" style="width:16%;"></div><div class="skeleton skeleton-text" style="width:10%;"></div><div class="skeleton skeleton-text" style="width:14%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:17%;"></div><div class="skeleton skeleton-text" style="width:21%;"></div><div class="skeleton skeleton-text" style="width:20%;"></div><div class="skeleton skeleton-text" style="width:10%;"></div><div class="skeleton skeleton-text" style="width:14%;"></div></div>
            </div>`,

        // Analytics: header + 4 stat cards + 2-col grid (top pages + traffic sources)
        analytics: `
            <header class="dashboard-header skeleton-header">
                <div><div class="skeleton skeleton-text" style="height:14px;width:80px;margin-bottom:8px;"></div><div class="skeleton skeleton-title" style="width:130px;"></div></div>
                <div class="skeleton" style="width:110px;height:36px;border-radius:8px;"></div>
            </header>
            <div class="skeleton" style="width:100%;height:44px;border-radius:10px;margin-bottom:1.25rem;"></div>
            <div class="skeleton-stat-grid" style="grid-template-columns:repeat(auto-fit,minmax(160px,1fr));">
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:42px;height:42px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:26px;width:55px;"></div></div></div>
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:42px;height:42px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:26px;width:55px;"></div></div></div>
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:42px;height:42px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:26px;width:55px;"></div></div></div>
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:42px;height:42px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:26px;width:55px;"></div></div></div>
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:1.25rem;">
                <div class="skeleton-card"><div class="skeleton skeleton-text" style="height:18px;width:100px;margin-bottom:1rem;"></div><div class="skeleton-list-item"><div class="skeleton skeleton-text lg"></div><div class="skeleton skeleton-text" style="width:40px;"></div></div><div class="skeleton-list-item"><div class="skeleton skeleton-text md"></div><div class="skeleton skeleton-text" style="width:35px;"></div></div><div class="skeleton-list-item"><div class="skeleton skeleton-text lg"></div><div class="skeleton skeleton-text" style="width:30px;"></div></div><div class="skeleton-list-item"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="width:28px;"></div></div></div>
                <div class="skeleton-card"><div class="skeleton skeleton-text" style="height:18px;width:130px;margin-bottom:1rem;"></div><div class="skeleton-list-item"><div class="skeleton skeleton-text md"></div><div class="skeleton skeleton-text" style="width:40px;"></div></div><div class="skeleton-list-item"><div class="skeleton skeleton-text lg"></div><div class="skeleton skeleton-text" style="width:35px;"></div></div><div class="skeleton-list-item"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="width:30px;"></div></div><div class="skeleton-list-item"><div class="skeleton skeleton-text md"></div><div class="skeleton skeleton-text" style="width:28px;"></div></div></div>
            </div>`,

        // Comments: header + 3 stats + filter tabs + table (commenter, text, post, status, actions)
        comments: `
            <header class="dashboard-header skeleton-header">
                <div><div class="skeleton skeleton-text" style="height:14px;width:120px;margin-bottom:8px;"></div><div class="skeleton skeleton-title" style="width:190px;"></div></div>
            </header>
            <div class="skeleton-stat-grid">
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:48px;height:48px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:28px;width:50px;"></div></div></div>
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:48px;height:48px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:28px;width:50px;"></div></div></div>
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:48px;height:48px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:28px;width:50px;"></div></div></div>
            </div>
            <div class="skeleton-filter-bar">
                <div class="skeleton" style="width:50px;height:32px;border-radius:20px;"></div>
                <div class="skeleton" style="width:80px;height:32px;border-radius:20px;"></div>
                <div class="skeleton" style="width:75px;height:32px;border-radius:20px;"></div>
            </div>
            <div class="skeleton-table">
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:15%;"></div><div class="skeleton skeleton-text" style="width:30%;"></div><div class="skeleton skeleton-text" style="width:18%;"></div><div class="skeleton skeleton-text" style="width:10%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:13%;"></div><div class="skeleton skeleton-text" style="width:34%;"></div><div class="skeleton skeleton-text" style="width:16%;"></div><div class="skeleton skeleton-text" style="width:10%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:16%;"></div><div class="skeleton skeleton-text" style="width:28%;"></div><div class="skeleton skeleton-text" style="width:20%;"></div><div class="skeleton skeleton-text" style="width:10%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:14%;"></div><div class="skeleton skeleton-text" style="width:32%;"></div><div class="skeleton skeleton-text" style="width:17%;"></div><div class="skeleton skeleton-text" style="width:10%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
            </div>`,

        // Schedule: header + 2 stats + calendar nav + timeline
        schedule: `
            <header class="dashboard-header skeleton-header">
                <div><div class="skeleton skeleton-text" style="height:14px;width:90px;margin-bottom:8px;"></div><div class="skeleton skeleton-title" style="width:140px;"></div></div>
            </header>
            <div class="skeleton-stat-grid" style="grid-template-columns:repeat(2,1fr);">
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:48px;height:48px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:28px;width:40px;"></div></div></div>
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:48px;height:48px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:28px;width:40px;"></div></div></div>
            </div>
            <div style="display:flex;align-items:center;justify-content:center;gap:1rem;margin-bottom:1.5rem;">
                <div class="skeleton" style="width:36px;height:36px;border-radius:50%;"></div>
                <div class="skeleton skeleton-text" style="width:160px;height:22px;"></div>
                <div class="skeleton" style="width:36px;height:36px;border-radius:50%;"></div>
            </div>
            <div class="skeleton-card" style="margin-bottom:1rem;">
                <div style="display:flex;align-items:center;gap:1rem;margin-bottom:1.25rem;"><div class="skeleton" style="width:12px;height:12px;border-radius:50%;"></div><div class="skeleton skeleton-text" style="width:100px;height:16px;"></div></div>
                <div class="skeleton-list-item"><div class="skeleton skeleton-text lg"></div><div class="skeleton skeleton-text sm"></div></div>
                <div class="skeleton-list-item"><div class="skeleton skeleton-text md"></div><div class="skeleton skeleton-text sm"></div></div>
            </div>
            <div class="skeleton-card">
                <div style="display:flex;align-items:center;gap:1rem;margin-bottom:1.25rem;"><div class="skeleton" style="width:12px;height:12px;border-radius:50%;"></div><div class="skeleton skeleton-text" style="width:100px;height:16px;"></div></div>
                <div class="skeleton-list-item"><div class="skeleton skeleton-text md"></div><div class="skeleton skeleton-text sm"></div></div>
            </div>`,

        // Approval: header + table (title, category, submitted, actions)
        approval: `
            <header class="dashboard-header skeleton-header">
                <div><div class="skeleton skeleton-text" style="height:14px;width:120px;margin-bottom:8px;"></div><div class="skeleton skeleton-title" style="width:190px;"></div></div>
            </header>
            <div class="skeleton-table">
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:38%;"></div><div class="skeleton skeleton-text" style="width:16%;"></div><div class="skeleton skeleton-text" style="width:18%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:42%;"></div><div class="skeleton skeleton-text" style="width:14%;"></div><div class="skeleton skeleton-text" style="width:16%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:35%;"></div><div class="skeleton skeleton-text" style="width:18%;"></div><div class="skeleton skeleton-text" style="width:17%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:40%;"></div><div class="skeleton skeleton-text" style="width:15%;"></div><div class="skeleton skeleton-text" style="width:18%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
            </div>`,

        // SEO Tools: header + card with dropdowns and button
        seoTools: `
            <header class="dashboard-header skeleton-header">
                <div><div class="skeleton skeleton-text" style="height:14px;width:80px;margin-bottom:8px;"></div><div class="skeleton skeleton-title" style="width:130px;"></div></div>
            </header>
            <div class="skeleton-card" style="max-width:900px;">
                <div class="skeleton skeleton-text" style="height:18px;width:180px;margin-bottom:1.5rem;"></div>
                <div class="skeleton skeleton-text sm" style="margin-bottom:0.5rem;"></div>
                <div class="skeleton" style="width:100%;height:42px;border-radius:8px;margin-bottom:1.25rem;"></div>
                <div class="skeleton skeleton-text sm" style="margin-bottom:0.5rem;"></div>
                <div class="skeleton" style="width:100%;height:42px;border-radius:8px;margin-bottom:1.5rem;"></div>
                <div style="display:flex;gap:0.75rem;">
                    <div class="skeleton" style="width:150px;height:40px;border-radius:8px;"></div>
                    <div class="skeleton" style="width:130px;height:40px;border-radius:8px;"></div>
                    <div class="skeleton" style="width:170px;height:40px;border-radius:8px;"></div>
                </div>
            </div>`,

        // Formatting Tools: header + input card (dropdown + title + textarea + buttons)
        formattingTools: `
            <header class="dashboard-header skeleton-header">
                <div><div class="skeleton skeleton-text" style="height:14px;width:110px;margin-bottom:8px;"></div><div class="skeleton skeleton-title" style="width:180px;"></div></div>
            </header>
            <div class="skeleton-card" style="max-width:900px;">
                <div class="skeleton skeleton-text sm" style="margin-bottom:0.5rem;"></div>
                <div style="display:flex;gap:0.75rem;margin-bottom:1.25rem;">
                    <div class="skeleton" style="flex:1;height:42px;border-radius:8px;"></div>
                    <div class="skeleton" style="width:90px;height:42px;border-radius:8px;"></div>
                    <div class="skeleton" style="width:80px;height:42px;border-radius:8px;"></div>
                </div>
                <div class="skeleton skeleton-text sm" style="margin-bottom:0.5rem;"></div>
                <div class="skeleton" style="width:100%;height:42px;border-radius:8px;margin-bottom:1.25rem;"></div>
                <div class="skeleton skeleton-text sm" style="margin-bottom:0.5rem;"></div>
                <div class="skeleton" style="width:100%;height:200px;border-radius:8px;margin-bottom:1.5rem;"></div>
                <div style="display:flex;gap:0.75rem;">
                    <div class="skeleton" style="width:140px;height:40px;border-radius:8px;"></div>
                    <div class="skeleton" style="width:80px;height:40px;border-radius:8px;"></div>
                </div>
            </div>`,

        // Site Settings: header + tab navigation + form card
        siteSettings: `
            <header class="dashboard-header skeleton-header">
                <div><div class="skeleton skeleton-text" style="height:14px;width:90px;margin-bottom:8px;"></div><div class="skeleton skeleton-title" style="width:150px;"></div></div>
            </header>
            <div class="skeleton-filter-bar" style="margin-bottom:1.5rem;">
                <div class="skeleton" style="width:100px;height:34px;border-radius:20px;"></div>
                <div class="skeleton" style="width:110px;height:34px;border-radius:20px;"></div>
                <div class="skeleton" style="width:90px;height:34px;border-radius:20px;"></div>
                <div class="skeleton" style="width:100px;height:34px;border-radius:20px;"></div>
            </div>
            <div class="skeleton-card">
                <div class="skeleton skeleton-text sm" style="margin-bottom:0.5rem;"></div>
                <div class="skeleton" style="width:100%;height:42px;border-radius:8px;margin-bottom:1.25rem;"></div>
                <div class="skeleton skeleton-text sm" style="margin-bottom:0.5rem;"></div>
                <div class="skeleton" style="width:100%;height:42px;border-radius:8px;margin-bottom:1.25rem;"></div>
                <div class="skeleton skeleton-text sm" style="margin-bottom:0.5rem;"></div>
                <div class="skeleton" style="width:100%;height:100px;border-radius:8px;margin-bottom:1.25rem;"></div>
                <div class="skeleton" style="width:130px;height:40px;border-radius:8px;"></div>
            </div>`,

        // App Settings: header + identity card with inputs and preview
        appSettings: `
            <header class="dashboard-header skeleton-header">
                <div><div class="skeleton skeleton-text" style="height:14px;width:90px;margin-bottom:8px;"></div><div class="skeleton skeleton-title" style="width:140px;"></div></div>
            </header>
            <div class="skeleton-card">
                <div class="skeleton skeleton-text" style="height:18px;width:140px;margin-bottom:1.5rem;"></div>
                <div class="skeleton skeleton-text sm" style="margin-bottom:0.5rem;"></div>
                <div class="skeleton" style="width:100%;height:42px;border-radius:8px;margin-bottom:1.25rem;"></div>
                <div class="skeleton skeleton-text sm" style="margin-bottom:0.5rem;"></div>
                <div class="skeleton" style="width:100%;height:42px;border-radius:8px;margin-bottom:1.25rem;"></div>
                <div class="skeleton skeleton-text sm" style="margin-bottom:0.5rem;"></div>
                <div class="skeleton" style="width:100%;height:42px;border-radius:8px;margin-bottom:1.25rem;"></div>
                <div class="skeleton skeleton-text sm" style="margin-bottom:0.5rem;"></div>
                <div class="skeleton" style="width:100%;height:42px;border-radius:8px;margin-bottom:1.5rem;"></div>
                <div class="skeleton" style="width:100%;height:90px;border-radius:12px;margin-bottom:1.5rem;"></div>
                <div class="skeleton" style="width:130px;height:40px;border-radius:8px;"></div>
            </div>`,

        // Create Blog: centered layout with prompt box
        create: `
            <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:70vh;text-align:center;padding:2rem;">
                <div class="skeleton skeleton-text" style="width:200px;height:14px;margin-bottom:1rem;"></div>
                <div class="skeleton skeleton-text" style="width:340px;height:32px;margin-bottom:2.5rem;"></div>
                <div class="skeleton" style="width:100%;max-width:600px;height:120px;border-radius:16px;margin-bottom:1.5rem;"></div>
                <div style="display:flex;gap:1rem;align-items:center;">
                    <div class="skeleton" style="width:140px;height:36px;border-radius:20px;"></div>
                    <div class="skeleton skeleton-text" style="width:180px;height:14px;"></div>
                </div>
            </div>`,

        // Manage Users: header with invite button + table (user, email, role, status, actions)
        manageUsers: `
            <header class="dashboard-header skeleton-header">
                <div><div class="skeleton skeleton-text" style="height:14px;width:110px;margin-bottom:8px;"></div><div class="skeleton skeleton-title" style="width:180px;"></div></div>
                <div class="skeleton" style="width:120px;height:38px;border-radius:8px;"></div>
            </header>
            <div class="skeleton-table">
                <div class="skeleton-table-row"><div style="display:flex;align-items:center;gap:10px;width:25%;"><div class="skeleton skeleton-circle" style="width:36px;height:36px;flex-shrink:0;"></div><div class="skeleton skeleton-text" style="width:80%;"></div></div><div class="skeleton skeleton-text" style="width:25%;"></div><div class="skeleton skeleton-text" style="width:12%;"></div><div class="skeleton" style="width:60px;height:22px;border-radius:12px;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
                <div class="skeleton-table-row"><div style="display:flex;align-items:center;gap:10px;width:25%;"><div class="skeleton skeleton-circle" style="width:36px;height:36px;flex-shrink:0;"></div><div class="skeleton skeleton-text" style="width:70%;"></div></div><div class="skeleton skeleton-text" style="width:28%;"></div><div class="skeleton skeleton-text" style="width:10%;"></div><div class="skeleton" style="width:60px;height:22px;border-radius:12px;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
                <div class="skeleton-table-row"><div style="display:flex;align-items:center;gap:10px;width:25%;"><div class="skeleton skeleton-circle" style="width:36px;height:36px;flex-shrink:0;"></div><div class="skeleton skeleton-text" style="width:90%;"></div></div><div class="skeleton skeleton-text" style="width:22%;"></div><div class="skeleton skeleton-text" style="width:14%;"></div><div class="skeleton" style="width:60px;height:22px;border-radius:12px;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
                <div class="skeleton-table-row"><div style="display:flex;align-items:center;gap:10px;width:25%;"><div class="skeleton skeleton-circle" style="width:36px;height:36px;flex-shrink:0;"></div><div class="skeleton skeleton-text" style="width:75%;"></div></div><div class="skeleton skeleton-text" style="width:26%;"></div><div class="skeleton skeleton-text" style="width:11%;"></div><div class="skeleton" style="width:60px;height:22px;border-radius:12px;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
            </div>`,

        // Optimization: header + three stat tiles + tab bar + the control row.
        // A skeleton is a promise about the shape that is coming, so it moves
        // whenever the screen does — this one still drew the old six-tile
        // metrics grid and an input card, neither of which the page opens on.
        optimization: `
            <header class="dashboard-header skeleton-header">
                <div><div class="skeleton skeleton-text" style="height:14px;width:130px;margin-bottom:8px;"></div><div class="skeleton skeleton-title" style="width:180px;"></div></div>
            </header>
            <div class="skeleton-stat-grid">
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:48px;height:48px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:28px;width:50px;"></div></div></div>
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:48px;height:48px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:28px;width:50px;"></div></div></div>
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:48px;height:48px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:28px;width:50px;"></div></div></div>
            </div>
            <div class="skeleton-filter-bar" style="margin-bottom:1.5rem;">
                <div class="skeleton" style="width:92px;height:34px;border-radius:999px;"></div>
                <div class="skeleton" style="width:104px;height:34px;border-radius:999px;"></div>
                <div class="skeleton" style="width:136px;height:34px;border-radius:999px;"></div>
                <div class="skeleton" style="width:118px;height:34px;border-radius:999px;"></div>
                <div class="skeleton" style="width:148px;height:34px;border-radius:999px;"></div>
            </div>
            <div class="skeleton-card">
                <div class="skeleton skeleton-text" style="height:18px;width:170px;margin-bottom:0.75rem;"></div>
                <div class="skeleton skeleton-text" style="height:12px;width:100%;max-width:520px;margin-bottom:1.5rem;"></div>
                <div style="display:flex;gap:0.75rem;flex-wrap:wrap;align-items:flex-end;">
                    <div class="skeleton" style="flex:1;min-width:220px;height:42px;border-radius:999px;"></div>
                    <div class="skeleton" style="width:180px;height:42px;border-radius:999px;"></div>
                    <div class="skeleton" style="width:130px;height:42px;border-radius:999px;"></div>
                </div>
            </div>`
    };

    // Map routes to their specific skeleton
    const routeSkeletonMap = {
        '/dashboard': 'dashboard',
        '/drafts': 'drafts',
        '/all-blogs': 'allBlogs',
        '/categories': 'categories',
        '/gallery': 'gallery',
        '/seo-tools': 'seoTools',
        '/newsletter': 'newsletter',
        '/formatting-tools': 'formattingTools',
        '/site-settings': 'siteSettings',
        '/approval': 'approval',
        '/comments': 'comments',
        '/schedule': 'schedule',
        '/leads': 'leads',
        '/activity': 'activity',
        '/analytics': 'analytics',
        '/create': 'create',
        '/app-settings': 'appSettings',
        '/users/manage-users': 'manageUsers',
        '/optimization': 'optimization'
    };

    function getSkeletonForUrl(url) {
        const pathname = new URL(url).pathname;
        for (const [route, type] of Object.entries(routeSkeletonMap)) {
            if (pathname === route || pathname.startsWith(route)) {
                return skeletons[type] || skeletons.allBlogs;
            }
        }
        return skeletons.allBlogs;
    }

    function isDashboardLink(link) {
        if (!link || !link.href) return false;
        if (link.target === '_blank') return false;
        if (link.hasAttribute('data-bs-toggle')) return false;
        if (link.hasAttribute('download')) return false;
        if (link.classList.contains('no-pjax')) return false;
        if (link.classList.contains('user-card-logout')) return false;
        if (link.href.includes('#')) return false;
        if (link.href.startsWith('javascript:')) return false;
        if (link.href.startsWith('mailto:')) return false;

        const url = new URL(link.href);
        if (url.host !== window.location.host) return false;

        // Only intercept dashboard navigation (sidebar links)
        const dashboardPaths = [
            '/dashboard', '/create', '/drafts', '/all-blogs',
            '/categories', '/gallery', '/seo-tools', '/newsletter',
            '/formatting-tools', '/site-settings', '/approval',
            '/comments', '/schedule', '/leads', '/activity',
            '/analytics', '/app-settings', '/users/manage-users',
            '/optimization'
        ];
        return dashboardPaths.some(p => url.pathname === p || url.pathname.startsWith(p));
    }

    function showProgress() {
        if (navProgress) {
            navProgress.style.width = "0%";
            navProgress.classList.add("loading");
        }
    }

    function hideProgress() {
        if (navProgress) {
            navProgress.classList.remove("loading");
            navProgress.style.width = "100%";
            setTimeout(() => { navProgress.style.width = "0%"; }, 300);
        }
    }

    function updateActiveLink(url) {
        const currentPath = new URL(url).pathname;
        document.querySelectorAll('.sidebar-menu a, .nav-link').forEach(link => {
            const href = link.getAttribute('href');
            if (href === currentPath) {
                link.classList.add('active');
            } else {
                link.classList.remove('active');
            }
        });
    }

    function extractPageAssets(doc) {
        const styles = [];
        const inlineStyles = [];
        const scripts = [];

        // Base assets that should NOT be reloaded on navigation
        const baseScripts = ['bootstrap.bundle', 'app.js', 'activity-tracker'];
        const baseStyles = ['bootstrap', 'dashboard.css', 'fonts.googleapis'];

        // Get page-specific CSS (anything not from the base template)
        doc.querySelectorAll('link[rel="stylesheet"]').forEach(link => {
            const href = link.getAttribute('href');
            if (href && !baseStyles.some(b => href.includes(b))) {
                styles.push(href);
            }
        });

        // Get page-specific inline <style> blocks
        doc.querySelectorAll('style').forEach(style => {
            const text = style.textContent.trim();
            if (text) {
                inlineStyles.push(text);
            }
        });

        // Get page-specific external JS (anything not from the base template)
        doc.querySelectorAll('script[src]').forEach(script => {
            const src = script.getAttribute('src');
            if (src && !baseScripts.some(b => src.includes(b))) {
                scripts.push(src);
            }
        });

        // Get inline scripts (page-specific initialization)
        const bodyScripts = [];
        doc.querySelectorAll('script:not([src])').forEach(script => {
            const text = script.textContent.trim();
            // Skip the session timeout and pjax scripts (they're in app.js)
            if (text && !text.includes('resetTimers') && !text.includes('_origAddEventListener')) {
                bodyScripts.push(text);
            }
        });

        return { styles, inlineStyles, scripts, bodyScripts };
    }

    function loadStyles(newStyles, newInlineStyles) {
        // Remove old page-specific external styles
        currentPageStyles.forEach(href => {
            const existing = document.querySelector(`link[href="${href}"]`);
            if (existing && !newStyles.includes(href)) {
                existing.remove();
            }
        });

        // Remove old pjax-injected inline styles
        document.querySelectorAll('style[data-pjax-inline]').forEach(el => el.remove());

        // Inject new inline styles immediately
        if (newInlineStyles && newInlineStyles.length > 0) {
            newInlineStyles.forEach(css => {
                const style = document.createElement('style');
                style.setAttribute('data-pjax-inline', 'true');
                style.textContent = css;
                document.head.appendChild(style);
            });
        }

        // Add new page-specific styles and wait for them to load
        const loadPromises = [];
        newStyles.forEach(href => {
            if (!document.querySelector(`link[href="${href}"]`)) {
                const link = document.createElement('link');
                link.rel = 'stylesheet';
                link.href = href;
                const promise = new Promise(resolve => {
                    link.onload = resolve;
                    link.onerror = resolve;
                });
                document.head.appendChild(link);
                loadPromises.push(promise);
            }
        });

        currentPageStyles = newStyles;
        return Promise.all(loadPromises);
    }

    function executeScripts(scripts, inlineScripts) {
        let inlineHasRun = false;

        // Runs the page's inline init scripts, then fires the "page ready" events.
        // Guarded so it can never run twice and never throws out of executeScripts:
        // one bad inline block must not stop the others or wedge the page.
        function runInlineScripts() {
            if (inlineHasRun) return;
            inlineHasRun = true;

            (inlineScripts || []).forEach(code => {
                try {
                    const script = document.createElement('script');
                    script.setAttribute('data-pjax', 'true');
                    script.textContent = code;
                    document.body.appendChild(script);
                } catch (e) {
                    // A failing init block is logged but never blocks the rest.
                    console.warn('Pjax: inline script error', e);
                }
            });

            // Dispatch DOMContentLoaded-like events for scripts expecting them.
            try {
                document.dispatchEvent(new Event('pjax:complete'));
                window.dispatchEvent(new Event('load'));
            } catch (e) {
                console.warn('Pjax: post-init event error', e);
            }
        }

        if (!scripts || scripts.length === 0) {
            runInlineScripts();
            return;
        }

        // Load external scripts sequentially. Each resolves on load OR error so a
        // single missing/broken/truncated file can never stall the whole chain and
        // leave the page half-initialized.
        const loadScript = (src) => {
            return new Promise((resolve) => {
                try {
                    // Remove the previously injected copy so a fresh <script> runs.
                    const old = document.querySelector(`script[data-pjax][src="${src}"]`);
                    if (old) old.remove();

                    const script = document.createElement('script');
                    script.src = src;
                    script.setAttribute('data-pjax', 'true');
                    script.onload = () => resolve();
                    script.onerror = () => {
                        console.warn('Pjax: failed to load page script', src);
                        resolve();
                    };
                    document.body.appendChild(script);
                } catch (e) {
                    console.warn('Pjax: error injecting page script', src, e);
                    resolve();
                }
            });
        };

        // Chain script loading, then always run inline init — even if the chain
        // somehow rejects — so a page is never left without its initialization.
        let chain = Promise.resolve();
        scripts.forEach(src => {
            chain = chain.then(() => loadScript(src));
        });
        chain.then(runInlineScripts).catch((e) => {
            console.warn('Pjax: script chain error', e);
            runInlineScripts();
        });
    }

    function cleanupOldScripts() {
        document.querySelectorAll('script[data-pjax]').forEach(s => s.remove());
    }

    async function navigate(url, pushState = true) {
        if (isNavigating) {
            if (currentAbortController) {
                currentAbortController.abort();
            }
        }

        isNavigating = true;
        currentAbortController = new AbortController();
        const timeoutId = setTimeout(() => currentAbortController.abort(), 15000);
        showProgress();

        const mainContent = document.querySelector('.dashboard-main');
        if (!mainContent) {
            window.location.href = url;
            return;
        }

        try {
            // Update active sidebar link immediately for responsiveness
            updateActiveLink(url);

            // Prefetch users data while the users page HTML is loading.
            if (isUsersRoute(url)) {
                prefetchUsersList().catch(() => {});
            }

            // Dim current content to signal loading
            mainContent.style.opacity = '0.5';
            mainContent.style.pointerEvents = 'none';
            mainContent.style.transition = 'opacity 0.15s ease';

            const response = await fetch(url, {
                signal: currentAbortController.signal,
                cache: 'no-store',
                headers: { 'X-Pjax': 'true' }
            });

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }

            const html = await response.text();
            const parser = new DOMParser();
            const doc = parser.parseFromString(html, 'text/html');

            const newMain = doc.querySelector('.dashboard-main');
            if (!newMain) {
                throw new Error('No .dashboard-main found in response');
            }

            const newTitle = doc.querySelector('title');
            if (newTitle) {
                document.title = newTitle.textContent;
            }

            const assets = extractPageAssets(doc);

            // Cleanup old scripts
            cleanupOldScripts();

            // Load new styles BEFORE swapping content (prevents flash of unstyled content)
            await loadStyles(assets.styles, assets.inlineStyles);

            // Swap content with real page
            mainContent.innerHTML = newMain.innerHTML;
            mainContent.scrollTop = 0;
            window.scrollTo(0, 0);
            mainContent.style.opacity = '1';
            mainContent.style.pointerEvents = '';
            mainContent.classList.add('pjax-entering');

            // Update URL and history
            if (pushState) {
                history.pushState({ pjax: true, url: url }, '', url);
            }

            hideProgress();

            // Execute new page scripts. Wrapped defensively: executeScripts is
            // already fault-tolerant, but a synchronous throw must never skip the
            // entering-class cleanup below or wedge the navigation state.
            try {
                executeScripts(assets.scripts, assets.bodyScripts);
            } catch (e) {
                console.warn('Pjax: executeScripts error', e);
            }

            setTimeout(() => {
                mainContent.classList.remove('pjax-entering');
            }, 250);

        } catch (error) {
            if (error.name === 'AbortError') {
                mainContent.style.opacity = '1';
                mainContent.style.pointerEvents = '';
                return;
            }
            console.warn('Pjax navigation failed, falling back:', error.message);
            window.location.href = url;
        } finally {
            clearTimeout(timeoutId);
            isNavigating = false;
            currentAbortController = null;
        }
    }

    function init() {
        // Track initial page-specific styles
        const baseStyles = ['bootstrap', 'dashboard.css'];
        document.querySelectorAll('link[rel="stylesheet"]').forEach(link => {
            const href = link.getAttribute('href');
            if (href && !baseStyles.some(b => href.includes(b))) {
                currentPageStyles.push(href);
            }
        });

        // Start prefetching users list immediately on page load
        prefetchUsersList().catch(() => {});

        // Intercept sidebar link clicks
        document.addEventListener('click', (e) => {
            const link = e.target.closest('a');
            if (!link) return;
            if (e.ctrlKey || e.metaKey || e.shiftKey) return; // Allow open in new tab

            if (isDashboardLink(link)) {
                e.preventDefault();
                navigate(link.href);
            }
        });

        // Start prefetch as intent signals that users page may be opened soon.
        const warmUsersPrefetch = (link) => {
            if (!link || !isUsersRoute(link.href)) return;
            prefetchUsersList().catch(() => {});
        };

        document.addEventListener('mouseover', (e) => {
            warmUsersPrefetch(e.target.closest('a'));
        });

        document.addEventListener('focusin', (e) => {
            warmUsersPrefetch(e.target.closest('a'));
        });

        document.addEventListener('touchstart', (e) => {
            warmUsersPrefetch(e.target.closest('a'));
        }, { passive: true });

        // Low-priority warmup in case users go straight to the Users tab.
        const scheduleIdleWarmup = () => {
            const usersLink = document.querySelector('a[href*="/users/manage-users"]');
            if (!usersLink) return;
            warmUsersPrefetch(usersLink);
        };

        if ('requestIdleCallback' in window) {
            window.requestIdleCallback(scheduleIdleWarmup, { timeout: 2000 });
        } else {
            setTimeout(scheduleIdleWarmup, 1200);
        }

        // Handle browser back/forward
        window.addEventListener('popstate', (e) => {
            if (e.state && e.state.pjax) {
                navigate(e.state.url, false);
            } else if (document.querySelector('.dashboard-main')) {
                navigate(window.location.href, false);
            }
        });

        // Store initial state
        history.replaceState({ pjax: true, url: window.location.href }, '');
    }

    return { init, navigate, getSkeletonForUrl, skeletons };
})();

// --------------------------------------------------------------------------
// Global UI safety net
//
// A page script that throws during initialization must never leave the app in a
// stuck state (main content dimmed/unclickable, or the nav progress bar spinning
// forever). If any uncaught error or rejected promise slips through, restore the
// content to an interactive state. This is a backstop, not a substitute for the
// per-script guards above — it just guarantees the UI always stays usable.
// --------------------------------------------------------------------------
function __restoreInteractiveUI() {
    try {
        const main = document.querySelector('.dashboard-main');
        if (main) {
            if (main.style.opacity && main.style.opacity !== '1') main.style.opacity = '1';
            if (main.style.pointerEvents === 'none') main.style.pointerEvents = '';
        }
        const progress = document.getElementById('nav-progress');
        if (progress && progress.classList.contains('loading')) {
            progress.classList.remove('loading');
            progress.style.width = '0%';
        }
    } catch (e) {
        /* never let the safety net itself throw */
    }
}

window.addEventListener('error', __restoreInteractiveUI);
window.addEventListener('unhandledrejection', __restoreInteractiveUI);

// --------------------------------------------------------------------------
// Global Skeleton Utility (for page scripts to use during AJAX loading)
// --------------------------------------------------------------------------

window.Skeleton = {
    // Show skeleton inside a container while data is loading
    show(container, type = 'table') {
        if (typeof container === 'string') {
            container = document.querySelector(container);
        }
        if (!container) return;
        container.setAttribute('data-skeleton-original', container.innerHTML);
        const templates = {
            table: `
                <div class="skeleton-table">
                    <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:35%;"></div><div class="skeleton skeleton-text" style="width:15%;"></div><div class="skeleton skeleton-text" style="width:12%;"></div><div class="skeleton skeleton-text" style="width:15%;"></div></div>
                    <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:40%;"></div><div class="skeleton skeleton-text" style="width:12%;"></div><div class="skeleton skeleton-text" style="width:14%;"></div><div class="skeleton skeleton-text" style="width:13%;"></div></div>
                    <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:30%;"></div><div class="skeleton skeleton-text" style="width:18%;"></div><div class="skeleton skeleton-text" style="width:10%;"></div><div class="skeleton skeleton-text" style="width:16%;"></div></div>
                    <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:38%;"></div><div class="skeleton skeleton-text" style="width:14%;"></div><div class="skeleton skeleton-text" style="width:12%;"></div><div class="skeleton skeleton-text" style="width:14%;"></div></div>
                    <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:32%;"></div><div class="skeleton skeleton-text" style="width:16%;"></div><div class="skeleton skeleton-text" style="width:11%;"></div><div class="skeleton skeleton-text" style="width:15%;"></div></div>
                </div>`,
            list: `
                <div class="skeleton-list-item"><div class="skeleton skeleton-circle" style="width:40px;height:40px;"></div><div style="flex:1"><div class="skeleton skeleton-text lg"></div><div class="skeleton skeleton-text sm"></div></div></div>
                <div class="skeleton-list-item"><div class="skeleton skeleton-circle" style="width:40px;height:40px;"></div><div style="flex:1"><div class="skeleton skeleton-text md"></div><div class="skeleton skeleton-text sm"></div></div></div>
                <div class="skeleton-list-item"><div class="skeleton skeleton-circle" style="width:40px;height:40px;"></div><div style="flex:1"><div class="skeleton skeleton-text lg"></div><div class="skeleton skeleton-text sm"></div></div></div>
                <div class="skeleton-list-item"><div class="skeleton skeleton-circle" style="width:40px;height:40px;"></div><div style="flex:1"><div class="skeleton skeleton-text md"></div><div class="skeleton skeleton-text sm"></div></div></div>`,
            cards: `
                <div class="skeleton-grid">
                    <div class="skeleton-card"><div class="skeleton skeleton-text md" style="height:18px;margin-bottom:1rem;"></div><div class="skeleton skeleton-text xl"></div><div class="skeleton skeleton-text lg"></div><div class="skeleton skeleton-text md"></div></div>
                    <div class="skeleton-card"><div class="skeleton skeleton-text md" style="height:18px;margin-bottom:1rem;"></div><div class="skeleton skeleton-text xl"></div><div class="skeleton skeleton-text lg"></div><div class="skeleton skeleton-text md"></div></div>
                    <div class="skeleton-card"><div class="skeleton skeleton-text md" style="height:18px;margin-bottom:1rem;"></div><div class="skeleton skeleton-text xl"></div><div class="skeleton skeleton-text lg"></div><div class="skeleton skeleton-text md"></div></div>
                </div>`,
            stats: `
                <div class="skeleton-stat-grid">
                    <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:48px;height:48px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:28px;width:60px;"></div></div></div>
                    <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:48px;height:48px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:28px;width:60px;"></div></div></div>
                    <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:48px;height:48px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:28px;width:60px;"></div></div></div>
                </div>`,
            gallery: `
                <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:1rem;">
                    <div class="skeleton" style="width:100%;height:160px;border-radius:12px;"></div>
                    <div class="skeleton" style="width:100%;height:160px;border-radius:12px;"></div>
                    <div class="skeleton" style="width:100%;height:160px;border-radius:12px;"></div>
                    <div class="skeleton" style="width:100%;height:160px;border-radius:12px;"></div>
                    <div class="skeleton" style="width:100%;height:160px;border-radius:12px;"></div>
                    <div class="skeleton" style="width:100%;height:160px;border-radius:12px;"></div>
                </div>`,
            rows: (count = 5) => {
                let html = '';
                for (let i = 0; i < count; i++) {
                    const w = 25 + Math.random() * 30;
                    html += `<div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:${w}%;"></div><div class="skeleton skeleton-text" style="width:${w * 0.4}%;"></div><div class="skeleton skeleton-text" style="width:${w * 0.3}%;"></div></div>`;
                }
                return `<div class="skeleton-table">${html}</div>`;
            }
        };

        const html = typeof templates[type] === 'function' ? templates[type]() : templates[type];
        if (html) container.innerHTML = html;
    },

    // Hide skeleton and restore original content (or replace with new content)
    hide(container, newContent) {
        if (typeof container === 'string') {
            container = document.querySelector(container);
        }
        if (!container) return;
        if (newContent !== undefined) {
            container.innerHTML = newContent;
        } else {
            const original = container.getAttribute('data-skeleton-original');
            if (original) container.innerHTML = original;
        }
        container.removeAttribute('data-skeleton-original');
    }
};

// --------------------------------------------------------------------------
// Page Load & Non-Dashboard Navigation Handling
// --------------------------------------------------------------------------

// Hide loader on page load
window.addEventListener("load", () => setTimeout(hideLoader, 200));

// Safety fallback: Force hide after 2.5 seconds if load event fails
setTimeout(hideLoader, 2500);

// Show loader only for non-dashboard (non-pjax) navigation
document.addEventListener("click", (e) => {
    const link = e.target.closest("a");
    if (link && link.href &&
        !link.href.includes("#") &&
        !link.href.startsWith("javascript:") &&
        !link.href.startsWith("mailto:") &&
        link.target !== "_blank" &&
        !e.ctrlKey && !e.metaKey &&
        !link.hasAttribute('data-bs-toggle') &&
        !link.hasAttribute('download') &&
        !link.classList.contains('logout') &&
        !link.classList.contains('no-loader')) {

        const currentHost = window.location.host;
        const linkHost = new URL(link.href).host;

        // Only show full-page loader for non-dashboard external/internal links
        // Dashboard links are handled by Pjax
        if (currentHost === linkHost) {
            const dashboardPaths = [
                '/dashboard', '/create', '/drafts', '/all-blogs',
                '/categories', '/gallery', '/seo-tools', '/newsletter',
                '/formatting-tools', '/site-settings', '/approval',
                '/comments', '/schedule', '/leads', '/activity',
                '/analytics', '/app-settings', '/users/manage-users',
                '/optimization'
            ];
            const url = new URL(link.href);
            const isPjaxLink = dashboardPaths.some(p => url.pathname === p || url.pathname.startsWith(p));
            if (!isPjaxLink) {
                showLoader();
            }
        }
    }
});

// Show loader on form submit (except AJAX forms)
document.addEventListener("submit", (e) => {
    if (!e.target.classList.contains('no-loader') &&
        !e.target.classList.contains('ajax-form')) {
        showLoader();
    }
});

// Hide loader on back/forward navigation
window.addEventListener("pageshow", (event) => {
    if (event.persisted) hideLoader();
});

// --------------------------------------------------------------------------
// Sidebar Active Link Handler (initial page load)
// --------------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", () => {
    const currentPath = window.location.pathname;
    document.querySelectorAll('.sidebar-menu a, .nav-link').forEach(link => {
        const href = link.getAttribute('href');
        if (href === currentPath) {
            link.classList.add('active');
        } else {
            link.classList.remove('active');
        }
    });

    // Initialize Pjax navigation
    Pjax.init();
});

// --------------------------------------------------------------------------
// Session Inactivity Timeout
// --------------------------------------------------------------------------

(function () {
    // Both numbers come from the server (see base.html), so the countdown here
    // and the timeout there are the same value. This was hardcoded to 15
    // minutes while the server allowed 480, so the browser signed people out
    // at a moment the server did not recognise and neither could be changed
    // without the other silently disagreeing.
    function metaSeconds(name, fallback) {
        const el = document.querySelector(`meta[name="${name}"]`);
        const n = el ? parseInt(el.getAttribute('content'), 10) : NaN;
        return (Number.isFinite(n) && n > 0) ? n : fallback;
    }

    const TIMEOUT_MS = metaSeconds('session-timeout', 600) * 1000;
    const WARN_MS = Math.min(metaSeconds('session-warning', 60) * 1000,
                             Math.floor(TIMEOUT_MS / 2));

    // Heartbeat at most this often. A third of the window means any activity
    // keeps the session alive with ~3 requests per window, while a genuinely
    // idle tab sends none and is allowed to expire — which is the entire point
    // of an inactivity timeout, and why this is driven by interaction rather
    // than by a bare interval.
    const HEARTBEAT_MS = Math.max(30000, Math.floor(TIMEOUT_MS / 3));

    let timeoutTimer = null, warningTimer = null;
    let lastBeat = 0;
    let stopped = false;

    function scheduleFrom(remainingMs) {
        clearTimeout(timeoutTimer);
        clearTimeout(warningTimer);
        if (stopped) return;

        warningTimer = setTimeout(warn, Math.max(0, remainingMs - WARN_MS));
        timeoutTimer = setTimeout(() => {
            window.location.href = '/login?expired=1';
        }, Math.max(0, remainingMs));
    }

    function warn() {
        // Nothing in the editor autosaves, so being signed out mid-post loses
        // the draft. The warning therefore carries an action rather than just
        // announcing what is about to happen.
        const seconds = Math.round(WARN_MS / 1000);
        if (window.showToast) {
            showToast({
                type: 'warning',
                title: 'Session expiring',
                message: `You will be signed out in ${seconds} seconds. `
                       + `Click anywhere to stay signed in.`,
                duration: Math.max(WARN_MS - 2000, 5000)
            });
        }
        // A click anywhere counts as activity and triggers a heartbeat, so the
        // instruction above is literally true; force the next interaction to
        // beat rather than be throttled out.
        lastBeat = 0;
    }

    async function heartbeat() {
        lastBeat = Date.now();
        try {
            const res = await fetch('/api/session/heartbeat', {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' }
            });
            if (res.status === 401) {
                // The global fetch wrapper handles the redirect for a
                // session_expired body; stop our own timers either way so we
                // do not race it.
                stopped = true;
                return;
            }
            const data = await res.json();
            if (data && data.expires_in) {
                // Resync to what the server actually granted, rather than
                // trusting a local assumption about the window.
                scheduleFrom(data.expires_in * 1000);
            }
        } catch (e) {
            // Offline or a transient failure. Keep the existing countdown: it
            // is the conservative choice, and the next interaction retries.
        }
    }

    function onActivity() {
        if (stopped) return;
        const now = Date.now();
        if (now - lastBeat >= HEARTBEAT_MS) {
            heartbeat();
        } else {
            // Between heartbeats, keep the local countdown honest: the server
            // expiry is still lastBeat + TIMEOUT, not now + TIMEOUT.
            scheduleFrom(Math.max(0, lastBeat + TIMEOUT_MS - now));
        }
    }

    const ACTIVITY_EVENTS = ['click', 'keydown', 'scroll', 'mousemove', 'touchstart'];

    function bind(target) {
        if (!target || target.__sessionActivityBound) return;
        try {
            ACTIVITY_EVENTS.forEach(evt =>
                target.addEventListener(evt, onActivity, { passive: true })
            );
            target.__sessionActivityBound = true;
        } catch (e) { /* a document we are not allowed to touch */ }
    }

    bind(document);

    // TinyMCE renders the editor into an iframe, and events inside an iframe do
    // not bubble to the parent document. Without this, someone writing a post
    // is invisible to every listener above and to the server both -- so a
    // ten-minute window would expire them mid-sentence. This is the case that
    // makes a short timeout safe rather than hostile.
    function bindFrames() {
        document.querySelectorAll('iframe').forEach(frame => {
            let doc = null;
            try {
                doc = frame.contentDocument;      // throws for cross-origin
            } catch (e) { return; }
            if (doc) bind(doc);
        });
    }

    function bindEditors() {
        bindFrames();
        // TinyMCE's own hook catches editors created after this runs, which is
        // every one of them: the editor is initialised when a dialog opens.
        if (window.tinymce && typeof window.tinymce.on === 'function'
            && !window.tinymce.__sessionActivityHooked) {
            try {
                window.tinymce.on('AddEditor', e => {
                    if (!e.editor || typeof e.editor.on !== 'function') return;
                    e.editor.on('init', () => bind(e.editor.getDoc && e.editor.getDoc()));
                    e.editor.on('keydown click', onActivity);
                });
                window.tinymce.__sessionActivityHooked = true;
            } catch (err) { /* older or partial TinyMCE build */ }
        }
    }

    bindEditors();
    // PJAX replaces .dashboard-main, so a new page brings new iframes and, on
    // the pages that use it, a newly loaded TinyMCE.
    document.addEventListener('pjax:complete', bindEditors);
    // A dialog can create an editor well after navigation finishes; a slow
    // sweep is cheaper than trying to hook every dialog-open path.
    setInterval(bindEditors, 10000);

    // Start the countdown from a full window: the page load itself was a
    // request, so the server has just stamped the session.
    lastBeat = Date.now();
    scheduleFrom(TIMEOUT_MS);

    // ----------------------------------------------------------------------
    // fetch() wrapper: CSRF header on writes, plus session-expiry handling
    //
    // The server runs Flask-WTF CSRFProtect over every POST/PUT/PATCH/DELETE
    // and publishes the token in a readable `csrf_token` cookie specifically
    // so this layer can echo it back in `X-CSRFToken`. Nothing was reading it,
    // so every write in the dashboard came back 400 "session security token
    // expired". Injecting it here rather than at ~45 individual call sites is
    // what keeps a newly added fetch() correct by default.
    // ----------------------------------------------------------------------

    // Methods the server protects (WTF_CSRF_METHODS).
    const CSRF_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

    function readCsrfToken() {
        const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]*)/);
        return match ? decodeURIComponent(match[1]) : null;
    }

    // Only ever attach the token to our own origin. A relative URL is
    // same-origin by definition; an absolute one is resolved and compared, so
    // a third-party endpoint never receives the token.
    function isSameOrigin(url) {
        try {
            return new URL(String(url), window.location.href).origin === window.location.origin;
        } catch (e) {
            return false;
        }
    }

    function withCsrf(input, init) {
        const method = String((init && init.method) || 'GET').toUpperCase();
        if (!CSRF_METHODS.has(method) || !isSameOrigin(input)) return init;

        const token = readCsrfToken();
        if (!token) return init;

        // Headers may arrive as a Headers instance, an array of pairs, or a
        // plain object. Normalising through Headers handles all three, and
        // preserves a token a caller set explicitly.
        const headers = new Headers((init && init.headers) || {});
        if (!headers.has('X-CSRFToken')) headers.set('X-CSRFToken', token);

        return Object.assign({}, init, { headers: headers });
    }

    const _fetch = window.fetch;
    window.fetch = async function(input, init) {
        const res = await _fetch.call(this, input, withCsrf(input, init));
        if (res.status === 401) {
            try {
                const data = await res.clone().json();
                if (data.error === 'session_expired') {
                    window.location.href = data.redirect || '/login?expired=1';
                }
            } catch(e) {}
        }
        return res;
    };
})();
