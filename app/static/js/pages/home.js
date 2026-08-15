/* ==========================================================================
   Dashboard (home.html)

   PJAX re-injects this file on every navigation back to the dashboard, so the
   module has to be safe to run more than once in one document lifetime. Every
   listener it puts on `document` or `window` is bound through a shared
   AbortController that the *next* run aborts first — element-level listeners
   need no cleanup because PJAX throws the elements away with the canvas.
   ========================================================================== */

(function dashboard() {
    'use strict';

    // Drop anything the previous visit to this page left on document/window.
    if (window.__dashboardAbort) {
        try { window.__dashboardAbort.abort(); } catch (e) { }
    }
    const controller = new AbortController();
    window.__dashboardAbort = controller;
    const signal = controller.signal;

    const root = document.querySelector('.dashboard-main');
    if (!root) return;

    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    // ----------------------------------------------------------------------
    // Hero figure — count up to the rendered value
    //
    // The template prints the final number, so a failure here (or no JS at
    // all) leaves the truth on screen; the animation only replaces it for the
    // ~900ms it is running.
    // ----------------------------------------------------------------------

    function countUp(el) {
        const target = parseInt(el.dataset.countup, 10);
        if (!isFinite(target) || target <= 0) return;

        // Nothing to animate to, or nobody watching: leave the rendered value
        // alone. requestAnimationFrame does not run in a hidden tab, so
        // starting here would blank the figure to 0 and hold it there until
        // the tab came forward.
        if (reduceMotion || document.hidden) return;

        const duration = 900;
        let start = null;
        let done = false;

        function finish() {
            if (done) return;
            done = true;
            el.textContent = String(target);
        }

        function step(now) {
            if (done) return;
            if (start === null) start = now;
            const t = Math.min((now - start) / duration, 1);
            if (t >= 1) return finish();
            el.textContent = String(Math.round(target * (1 - Math.pow(1 - t, 3))));
            requestAnimationFrame(step);
        }

        el.textContent = '0';
        requestAnimationFrame(step);

        // Belt and braces: if the frame loop is throttled to a crawl (or stops
        // entirely mid-count), the figure still lands on the true number rather
        // than freezing partway.
        setTimeout(finish, duration + 400);
    }

    root.querySelectorAll('[data-countup]').forEach(countUp);

    // ----------------------------------------------------------------------
    // Pipeline bar — draw in from zero
    //
    // The segments carry their real width in CSS. `.is-preparing` zeroes them
    // with the transition off; removing it a frame later is what animates.
    // ----------------------------------------------------------------------

    const stack = root.querySelector('[data-viz-stack]');
    if (stack && stack.classList.contains('is-preparing')) {
        if (reduceMotion) {
            stack.classList.remove('is-preparing');
        } else {
            requestAnimationFrame(() => {
                requestAnimationFrame(() => stack.classList.remove('is-preparing'));
            });
        }
    }

    // ----------------------------------------------------------------------
    // Pipeline bar — hover / focus layer
    //
    // Every value in this tooltip is also printed in the legend below the bar,
    // so the tooltip enhances and never gates.
    // ----------------------------------------------------------------------

    const tooltip = root.querySelector('[data-viz-tooltip]');
    const pipelineCard = root.querySelector('.pipeline-card');

    function showTip(seg) {
        if (!tooltip || !pipelineCard) return;

        tooltip.innerHTML =
            '<b>' + seg.dataset.label + '</b> · ' + seg.dataset.count +
            ' <span class="tip-share">(' + seg.dataset.share + '%)</span>';
        tooltip.hidden = false;

        const card = pipelineCard.getBoundingClientRect();
        const bar = seg.getBoundingClientRect();
        const half = tooltip.offsetWidth / 2;

        // Clamp to the card so a segment at either end cannot push the tooltip
        // outside the card and get clipped.
        const raw = bar.left - card.left + bar.width / 2;
        const x = Math.min(Math.max(raw, half + 8), card.width - half - 8);

        tooltip.style.left = x + 'px';
        tooltip.style.top = (bar.top - card.top - 10) + 'px';
    }

    function hideTip() {
        if (tooltip) tooltip.hidden = true;
    }

    root.querySelectorAll('.viz-seg').forEach((seg) => {
        seg.addEventListener('mouseenter', () => showTip(seg));
        seg.addEventListener('focus', () => showTip(seg));
        seg.addEventListener('mouseleave', hideTip);
        seg.addEventListener('blur', hideTip);
    });

    // ----------------------------------------------------------------------
    // Hero spotlight
    // ----------------------------------------------------------------------

    const hero = root.querySelector('[data-spotlight]');
    if (hero && !reduceMotion) {
        hero.addEventListener('pointermove', (e) => {
            const r = hero.getBoundingClientRect();
            hero.style.setProperty('--mx', ((e.clientX - r.left) / r.width * 100) + '%');
            hero.style.setProperty('--my', ((e.clientY - r.top) / r.height * 100) + '%');
        });
        hero.addEventListener('pointerleave', () => {
            hero.style.removeProperty('--mx');
            hero.style.removeProperty('--my');
        });
    }

    // ----------------------------------------------------------------------
    // Relative timestamps
    // ----------------------------------------------------------------------

    function relative(iso) {
        const then = new Date(iso);
        if (isNaN(then.getTime())) return null;

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

    root.querySelectorAll('time[data-relative]').forEach((el) => {
        const text = relative(el.getAttribute('datetime'));
        if (text) {
            el.title = el.textContent.trim();
            el.textContent = text;
        }
    });

    // ----------------------------------------------------------------------
    // Recent-work tabs
    // ----------------------------------------------------------------------

    const tabs = Array.from(root.querySelectorAll('.seg-tab'));
    const panels = Array.from(root.querySelectorAll('.work-panel'));

    function selectTab(name, focus) {
        tabs.forEach((tab) => {
            const on = tab.dataset.tab === name;
            tab.classList.toggle('is-active', on);
            tab.setAttribute('aria-selected', on ? 'true' : 'false');
            tab.tabIndex = on ? 0 : -1;
            if (on && focus) tab.focus();
        });
        panels.forEach((panel) => {
            panel.hidden = panel.dataset.panel !== name;
        });
    }

    tabs.forEach((tab, i) => {
        tab.addEventListener('click', () => selectTab(tab.dataset.tab, false));
        tab.addEventListener('keydown', (e) => {
            let next = null;
            if (e.key === 'ArrowRight') next = tabs[(i + 1) % tabs.length];
            else if (e.key === 'ArrowLeft') next = tabs[(i - 1 + tabs.length) % tabs.length];
            else if (e.key === 'Home') next = tabs[0];
            else if (e.key === 'End') next = tabs[tabs.length - 1];
            if (!next) return;
            e.preventDefault();
            selectTab(next.dataset.tab, true);
        });
    });

    // ----------------------------------------------------------------------
    // Search
    //
    // The header component owns the field and the keyboard shortcuts; it just
    // announces what was typed. Filtering happens across every panel so the
    // result is already correct when the reader switches tabs, and Enter hands
    // the query off to All Blogs, which can search the whole collection rather
    // than the five rows on this screen.
    //
    // The same query also answers back with `page-search-results`, which is
    // what fills the header's dropdown: the rows the reader wants are usually
    // in the panel they are not looking at, and the dropdown flattens all
    // three into one ranked list without making them hunt through the tabs.
    // ----------------------------------------------------------------------

    function filter(query) {
        const q = query.trim().toLowerCase();

        panels.forEach((panel) => {
            const rows = Array.from(panel.querySelectorAll('.data-row'));
            if (!rows.length) return;

            let shown = 0;
            rows.forEach((row) => {
                const hit = !q || (row.dataset.search || '').indexOf(q) !== -1;
                row.hidden = !hit;
                if (hit) shown++;
            });

            const none = panel.querySelector('[data-noresults]');
            if (none) none.hidden = shown !== 0;

            const more = panel.querySelector('.work-more');
            if (more) more.hidden = shown === 0;
        });
    }

    function text(row, selector) {
        const el = row.querySelector(selector);
        return el ? el.textContent.replace(/\s+/g, ' ').trim() : '';
    }

    // Every panel holds its own copy of a row, so the same post can be matched
    // three times over; data-search is the row's identity here because it is
    // built from exactly the fields the dropdown shows.
    function collect(q) {
        const seen = new Set();
        const hits = [];

        root.querySelectorAll('.data-row').forEach((row) => {
            const haystack = row.dataset.search || '';
            if (haystack.indexOf(q) === -1 || seen.has(haystack)) return;
            seen.add(haystack);

            const title = text(row, '.row-title');
            const pill = row.querySelector('.status-pill');
            const status = pill
                ? (pill.className.match(/status-([a-z_]+)/) || [, ''])[1]
                : '';

            hits.push({
                title: title,
                meta: text(row, '.row-meta'),
                href: row.getAttribute('href'),
                mark: text(row, '.row-mark'),
                status: status,
                statusLabel: pill ? pill.textContent.trim() : '',
                // A title that opens with the query is what the reader most
                // likely meant; a category-only match is the weakest hit.
                rank: title.toLowerCase().indexOf(q) === 0 ? 0
                    : title.toLowerCase().indexOf(q) !== -1 ? 1 : 2
            });
        });

        return hits.sort((a, b) => a.rank - b.rank);
    }

    function announce(query) {
        const q = query.trim().toLowerCase();
        const card = root.querySelector('[data-search-url]');

        document.dispatchEvent(new CustomEvent('page-search-results', {
            detail: {
                query: query,
                items: q ? collect(q) : [],
                empty: 'Nothing in recent work matches that.',
                // Recent work is only the last few posts, so every query keeps
                // a way out to the collection that holds the rest.
                footer: card ? {
                    label: 'Search all blogs for “' + query.trim() + '”',
                    href: card.dataset.searchUrl + '?search=' + encodeURIComponent(query.trim())
                } : null
            }
        }));
    }

    document.addEventListener('page-search', (e) => {
        const value = (e.detail && e.detail.value) || '';

        if (e.detail && e.detail.submit) {
            const q = value.trim();
            const card = root.querySelector('[data-search-url]');
            if (q && card) {
                window.location.href = card.dataset.searchUrl + '?search=' + encodeURIComponent(q);
                return;
            }
        }

        filter(value);
        announce(value);
    }, { signal });

    // Keep the header's theme button in sync — syncThemeControls only runs on
    // DOMContentLoaded, which a PJAX navigation never fires.
    if (typeof window.syncThemeControls === 'function') window.syncThemeControls();
})();
