/**
 * Gallery — the media library.
 *
 * Everything is delegated off the page root and re-read at event time. This
 * file is re-injected by PJAX on every visit to /gallery, so nothing may hold
 * a reference across navigations and every listener registered on `document`
 * goes through an AbortController the next run of this file aborts.
 */

(function galleryPage() {
    'use strict';

    // Abort the previous visit's listeners before registering this visit's.
    if (window.__galleryAbort) {
        try { window.__galleryAbort.abort(); } catch (e) { /* already gone */ }
    }
    const controller = new AbortController();
    const signal = controller.signal;
    window.__galleryAbort = controller;

    const ALLOWED = ['image/png', 'image/jpeg', 'image/gif', 'image/webp'];
    const MAX_BYTES = 5 * 1024 * 1024;
    const PER_PAGE = 24;
    const UPLOAD_CONCURRENCY = 3;
    const VIEW_KEY = 'scriptly-gallery-view';

    const params = new URLSearchParams(window.location.search);

    const state = {
        search: (params.get('search') || '').trim(),
        type: params.get('type') || 'all',
        sort: params.get('sort') || 'newest',
        page: Math.max(1, parseInt(params.get('page'), 10) || 1),
        view: localStorage.getItem(VIEW_KEY) === 'list' ? 'list' : 'grid',
        items: [],
        selected: new Set(),
        anchor: null,
        totalPages: 1,
        // The image the preview/delete flow is currently acting on.
        activeId: null,
        pendingDelete: []
    };

    let fetchAbort = null;
    let searchTimer = null;

    // ----------------------------------------------------------------------
    // Small helpers
    // ----------------------------------------------------------------------

    const $ = (sel, root) => (root || document).querySelector(sel);
    const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

    // NOT the `div.textContent -> div.innerHTML` trick. That escapes &, < and >
    // but leaves quotes untouched, which is safe for text and unsafe here:
    // every value below is interpolated into an *attribute* (data-name, alt,
    // title, aria-label). A file named `" onerror="alert(1)` closes the
    // attribute and injects a handler — and filenames are attacker-supplied,
    // stored verbatim at upload.
    //
    // These five replacements are exactly what Jinja's autoescape does, which
    // is also what keeps renderTile() byte-identical to the server's markup.
    function escapeHtml(str) {
        return String(str == null ? '' : str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&#34;')
            .replace(/'/g, '&#39;');
    }

    // Mirrors the `readable` expression in gallery.html exactly. If one changes
    // the other must, or the same file restates its own size differently
    // between the server's paint and the first client re-render.
    function formatBytes(bytes) {
        const n = Number(bytes) || 0;
        if (n >= 1048576) return (n / 1048576).toFixed(1) + ' MB';
        return Math.round(n / 1024) + ' KB';
    }

    function extOf(name) {
        const s = String(name || '');
        return s.indexOf('.') !== -1 ? s.split('.').pop().toUpperCase() : 'IMG';
    }

    function formatDate(iso) {
        if (!iso) return '—';
        const d = new Date(iso);
        if (isNaN(d.getTime())) return '—';
        return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
    }

    // A stored URL is site-relative (/static/uploads/…). Copying that raw is
    // what the old page did, and it pastes as a broken image everywhere except
    // the same origin.
    function absoluteUrl(url) {
        try { return new URL(url, window.location.origin).href; }
        catch (e) { return url; }
    }

    function toast(type, title, message) {
        if (typeof window.showToast === 'function') {
            window.showToast({ type: type, title: title, message: message, duration: type === 'error' ? 5000 : 2600 });
        }
    }

    async function copyText(text, note) {
        try {
            await navigator.clipboard.writeText(text);
            toast('success', 'Copied', note);
            return true;
        } catch (e) {
            toast('error', 'Copy failed', 'Your browser blocked clipboard access.');
            return false;
        }
    }

    function bsModal(id) {
        const el = document.getElementById(id);
        if (!el || typeof bootstrap === 'undefined') return null;
        return bootstrap.Modal.getOrCreateInstance(el);
    }

    // ----------------------------------------------------------------------
    // Hydration
    //
    // The first page is rendered by Jinja, so the tiles already on screen are
    // the source of truth for state.items rather than something to re-render.
    // renderTile() below must produce the same markup — see the note in
    // gallery.html.
    // ----------------------------------------------------------------------

    function hydrate() {
        state.items = $$('[data-media-item]').map(tileToItem);

        const buttons = $$('#galleryPager .pager-btn');
        const pages = buttons.map((b) => parseInt(b.dataset.page, 10) || 1);
        state.totalPages = pages.length ? Math.max.apply(null, pages) : 1;

        // The server clamps an out-of-range ?page= before it renders, so the
        // active button is the truth about which page is on screen — trusting
        // the query string instead would leave ?page=99 asking for page 100.
        const active = $('#galleryPager .pager-btn.is-active');
        state.page = active ? (parseInt(active.dataset.page, 10) || 1) : 1;

        // Mirror the query into the header field. Arriving with ?search= and an
        // empty box means facing a narrowed library with nothing to explain it.
        const box = $('#gallerySearch');
        if (box && state.search) box.value = state.search;
    }

    function tileToItem(el) {
        return {
            id: el.dataset.id,
            url: el.dataset.url,
            filename: el.dataset.name,
            size: Number(el.dataset.size) || 0,
            created_at: el.dataset.created || ''
        };
    }

    // ----------------------------------------------------------------------
    // Rendering
    // ----------------------------------------------------------------------

    function renderTile(img) {
        const name = escapeHtml(img.filename || 'Untitled');
        const url = escapeHtml(img.url);
        const sub = formatBytes(img.size) + ' · ' + escapeHtml(extOf(img.filename));
        return '' +
            '<figure class="media-tile" data-media-item data-id="' + escapeHtml(img.id) + '" data-url="' + url + '"' +
            ' data-name="' + name + '" data-size="' + (Number(img.size) || 0) + '"' +
            ' data-created="' + escapeHtml(img.created_at || '') + '">' +
            '<div class="media-frame">' +
            '<div class="media-thumb"><img src="' + url + '" alt="' + name + '" loading="lazy" decoding="async"></div>' +
            '<button type="button" class="media-open" data-media-open aria-label="Preview ' + name + '"></button>' +
            '<span class="media-check">' +
            '<input type="checkbox" class="media-checkbox" data-media-select aria-label="Select ' + name + '">' +
            '<span class="media-check-box" aria-hidden="true"></span>' +
            '</span>' +
            '<button type="button" class="media-quick" data-media-copy title="Copy image URL"' +
            ' aria-label="Copy URL for ' + name + '"><i class="bi bi-link-45deg" aria-hidden="true"></i></button>' +
            '</div>' +
            '<figcaption class="media-caption">' +
            '<span class="media-name" title="' + name + '">' + name + '</span>' +
            '<span class="media-sub" data-media-sub>' + sub + '</span>' +
            '</figcaption></figure>';
    }

    // List view reuses the shared .data-row, so an image reads like every other
    // record in the product — with a thumbnail of itself where a record would
    // carry its monogram.
    function renderRow(img) {
        const name = escapeHtml(img.filename || 'Untitled');
        const url = escapeHtml(img.url);
        return '' +
            '<div class="data-row media-row" data-media-item data-id="' + escapeHtml(img.id) + '" data-url="' + url + '"' +
            ' data-name="' + name + '" data-size="' + (Number(img.size) || 0) + '"' +
            ' data-created="' + escapeHtml(img.created_at || '') + '">' +
            '<span class="media-row-lead">' +
            '<span class="media-check">' +
            '<input type="checkbox" class="media-checkbox" data-media-select aria-label="Select ' + name + '">' +
            '<span class="media-check-box" aria-hidden="true"></span>' +
            '</span>' +
            '<img class="media-row-thumb" src="' + url + '" alt="" loading="lazy" decoding="async">' +
            '</span>' +
            '<button type="button" class="row-open" data-media-open aria-label="Preview ' + name + '">' +
            '<span class="row-title">' + name + '</span>' +
            '<span class="row-meta"><span>' + escapeHtml(formatDate(img.created_at)) + '</span></span>' +
            '</button>' +
            '<span class="media-row-type">' + escapeHtml(extOf(img.filename)) + '</span>' +
            '<span class="media-row-size">' + formatBytes(img.size) + '</span>' +
            '<div class="row-trail">' +
            '<button type="button" class="row-action" data-media-copy title="Copy image URL"' +
            ' aria-label="Copy URL for ' + name + '"><i class="bi bi-link-45deg" aria-hidden="true"></i></button>' +
            '<button type="button" class="row-action" data-media-delete title="Delete image"' +
            ' aria-label="Delete ' + name + '"><i class="bi bi-trash3" aria-hidden="true"></i></button>' +
            '</div></div>';
    }

    function renderBody() {
        const body = $('[data-gallery-body]');
        if (!body) return;

        if (!state.items.length) {
            body.innerHTML = isFiltered()
                ? '<div class="list-empty">' +
                '<span class="list-empty-icon"><i class="bi bi-search"></i></span>' +
                '<p>No images match those filters.</p>' +
                '<button type="button" class="app-btn is-ghost" data-clear-filters>Clear filters</button>' +
                '</div>'
                : '<div class="list-empty gallery-empty">' +
                '<span class="list-empty-icon"><i class="bi bi-images"></i></span>' +
                '<p>No images yet. Upload artwork here once and reuse it as a feature image on any post.</p>' +
                '<button type="button" class="app-btn is-primary" data-upload-trigger>' +
                '<i class="bi bi-cloud-arrow-up" aria-hidden="true"></i> Upload images</button>' +
                '<span class="gallery-empty-hint">or drop files anywhere on this page — PNG, JPG, GIF, WebP up to 5MB</span>' +
                '</div>';
            syncSelectionChrome();
            return;
        }

        if (state.view === 'list') {
            body.innerHTML = '<div class="media-list data-rows" id="galleryGrid" aria-label="Images">' +
                state.items.map(renderRow).join('') + '</div>';
        } else {
            body.innerHTML = '<div class="media-grid" id="galleryGrid" aria-label="Images">' +
                state.items.map(renderTile).join('') + '</div>';
        }
        syncSelectionChrome();
    }

    function renderPager() {
        const pager = $('#galleryPager');
        if (!pager) return;
        if (state.totalPages <= 1) { pager.innerHTML = ''; return; }

        // A window around the current page rather than every page number: the
        // old build printed one button per page, so a 40-page library rendered
        // 40 buttons across the card.
        const page = state.page;
        const last = state.totalPages;
        const wanted = new Set([1, last, page - 1, page, page + 1]);
        if (page <= 3) { wanted.add(2); wanted.add(3); }
        if (page >= last - 2) { wanted.add(last - 1); wanted.add(last - 2); }

        const pages = Array.from(wanted).filter((p) => p >= 1 && p <= last).sort((a, b) => a - b);

        let html = '<button type="button" class="pager-btn' + (page <= 1 ? ' is-disabled' : '') +
            '" data-page="' + (page - 1) + '" aria-label="Previous page">' +
            '<i class="bi bi-chevron-left" aria-hidden="true"></i></button>';

        let prev = 0;
        pages.forEach((p) => {
            if (p - prev > 1) html += '<span class="pager-dots">…</span>';
            html += '<button type="button" class="pager-btn' + (p === page ? ' is-active' : '') +
                '" data-page="' + p + '">' + p + '</button>';
            prev = p;
        });

        html += '<button type="button" class="pager-btn' + (page >= last ? ' is-disabled' : '') +
            '" data-page="' + (page + 1) + '" aria-label="Next page">' +
            '<i class="bi bi-chevron-right" aria-hidden="true"></i></button>';

        pager.innerHTML = html;
    }

    function renderMeta(data) {
        const count = $('[data-gallery-count]');
        if (count) count.textContent = data.total || 0;

        const note = $('[data-gallery-note]');
        if (note) {
            const bits = [];
            if ((data.total_pages || 0) > 1) bits.push('Page ' + data.page + ' of ' + data.total_pages);
            if (data.total) bits.push(((data.matched_size || 0) / 1048576).toFixed(1) + ' MB');
            note.textContent = bits.join(' · ');
        }

        // Facet counts move with the search, so the tabs always describe the
        // library as currently searched rather than as it was on load.
        const counts = data.type_counts || {};
        const all = Object.keys(counts).reduce((sum, k) => sum + counts[k], 0);
        $$('.gallery-type-tabs .seg-tab').forEach((tab) => {
            const key = tab.dataset.type;
            const badge = $('.seg-count', tab);
            const n = key === 'all' ? all : (counts[key] || 0);
            if (badge) badge.textContent = n;
            // Keep a type visible while it is the active filter even at 0, so
            // the control the reader is looking at cannot vanish under them.
            tab.hidden = key !== 'all' && n === 0 && state.type !== key;
        });
    }

    function isFiltered() {
        return !!state.search || state.type !== 'all';
    }

    function syncFilterChrome() {
        $$('.gallery-type-tabs .seg-tab').forEach((tab) => {
            tab.classList.toggle('is-active', tab.dataset.type === state.type);
        });

        const sortSelect = $('#gallerySort');
        if (sortSelect && sortSelect.value !== state.sort) sortSelect.value = state.sort;
        const sortValue = $('#gallerySortValue');
        if (sortValue && sortSelect) {
            const opt = sortSelect.options[sortSelect.selectedIndex];
            if (opt) sortValue.textContent = opt.textContent;
        }

        $$('[data-clear-filters]').forEach((btn) => { btn.hidden = !isFiltered(); });

        $$('.view-toggle-btn').forEach((btn) => {
            const on = btn.dataset.view === state.view;
            btn.classList.toggle('is-active', on);
            btn.setAttribute('aria-pressed', on ? 'true' : 'false');
        });
    }

    // The view state lives in the URL so a reload, a shared link or a back
    // button all land on the same shelf. replaceState (never push) keeps the
    // history stack PJAX owns exactly as it was, and the pjax flag is carried
    // through so its popstate handler still recognises the entry.
    function syncUrl() {
        const next = new URLSearchParams();
        if (state.search) next.set('search', state.search);
        if (state.type !== 'all') next.set('type', state.type);
        if (state.sort !== 'newest') next.set('sort', state.sort);
        if (state.page > 1) next.set('page', String(state.page));

        const qs = next.toString();
        const url = window.location.pathname + (qs ? '?' + qs : '');
        if (url === window.location.pathname + window.location.search) return;
        try {
            history.replaceState({ pjax: true, url: window.location.origin + url }, '', url);
        } catch (e) { /* history is not essential to the page working */ }
    }

    // ----------------------------------------------------------------------
    // Fetching
    // ----------------------------------------------------------------------

    async function load(opts) {
        const options = opts || {};
        const body = $('[data-gallery-body]');

        if (fetchAbort) fetchAbort.abort();
        fetchAbort = new AbortController();

        // Dim rather than blank: replacing the grid with a spinner throws away
        // tiles that are usually about to be re-rendered identically, and the
        // resulting height collapse jumps the page under the pointer.
        if (body) body.classList.add('is-loading');

        const query = new URLSearchParams({
            page: String(state.page),
            per_page: String(PER_PAGE),
            sort: state.sort,
            type: state.type
        });
        if (state.search) query.set('search', state.search);

        try {
            const res = await fetch('/api/gallery/images?' + query.toString(), {
                signal: fetchAbort.signal,
                credentials: 'same-origin',
                headers: { 'Accept': 'application/json' }
            });
            const data = await res.json();
            if (!data.success) throw new Error(data.error || 'Failed to load');

            state.items = data.images || [];
            state.page = data.page || 1;
            state.totalPages = data.total_pages || 1;

            if (!options.keepSelection) clearSelection(true);
            else pruneSelection();

            renderBody();
            renderPager();
            renderMeta(data);
            syncFilterChrome();
            syncUrl();
        } catch (err) {
            if (err.name === 'AbortError') return;
            console.error('Gallery load failed:', err);
            toast('error', 'Could not load images', 'Check your connection and try again.');
        } finally {
            if (body) body.classList.remove('is-loading');
        }
    }

    function resetToFirstPage() {
        state.page = 1;
        load();
    }

    // ----------------------------------------------------------------------
    // Selection
    //
    // Photos-style: with nothing selected a click opens the preview; once
    // anything is selected a click toggles, so there is no mode to enter or
    // leave. Modifier keys always work — ctrl/cmd toggles one, shift takes the
    // range from the last anchor.
    // ----------------------------------------------------------------------

    function tileEls() {
        return $$('#galleryGrid [data-media-item]');
    }

    function setSelected(id, on) {
        if (on) state.selected.add(id); else state.selected.delete(id);
    }

    function toggle(id) {
        setSelected(id, !state.selected.has(id));
        state.anchor = id;
        syncSelectionChrome();
    }

    function selectRange(toId) {
        const ids = state.items.map((i) => i.id);
        const from = ids.indexOf(state.anchor);
        const to = ids.indexOf(toId);
        if (from === -1 || to === -1) { toggle(toId); return; }
        const [lo, hi] = from < to ? [from, to] : [to, from];
        for (let i = lo; i <= hi; i++) state.selected.add(ids[i]);
        syncSelectionChrome();
    }

    function clearSelection(quiet) {
        state.selected.clear();
        state.anchor = null;
        if (!quiet) syncSelectionChrome();
    }

    // Drop ids that are no longer on the page — otherwise a bulk delete could
    // act on something the reader can no longer see.
    function pruneSelection() {
        const present = new Set(state.items.map((i) => i.id));
        Array.from(state.selected).forEach((id) => { if (!present.has(id)) state.selected.delete(id); });
    }

    function syncSelectionChrome() {
        const n = state.selected.size;

        tileEls().forEach((el) => {
            const on = state.selected.has(el.dataset.id);
            el.classList.toggle('is-selected', on);
            const box = $('[data-media-select]', el);
            if (box) box.checked = on;
        });

        const filters = $('[data-toolbar-filters]');
        const bar = $('[data-selection-bar]');
        if (filters) filters.hidden = n > 0;
        if (bar) bar.hidden = n === 0;

        const label = $('[data-selection-count]');
        if (label) label.textContent = n + ' selected';

        const all = $('[data-selection-all]');
        if (all) {
            const everything = state.items.length > 0 && n === state.items.length;
            all.textContent = everything ? 'Clear page' : 'Select page';
        }
    }

    function selectedItems() {
        return state.items.filter((i) => state.selected.has(i.id));
    }

    // ----------------------------------------------------------------------
    // Preview
    // ----------------------------------------------------------------------

    function itemById(id) {
        return state.items.filter((i) => i.id === id)[0] || null;
    }

    function openPreview(id) {
        const img = itemById(id);
        if (!img) return;
        state.activeId = id;
        paintPreview(img);
        const modal = bsModal('previewModal');
        if (modal) modal.show();
    }

    function paintPreview(img) {
        const index = state.items.indexOf(img);
        const name = img.filename || 'Untitled';

        const title = $('[data-preview-name]');
        if (title) { title.textContent = name; title.title = name; }

        const el = $('[data-preview-image]');
        const dims = $('[data-preview-dimensions]');
        if (el) {
            if (dims) dims.textContent = '—';
            el.alt = name;
            // Natural size is read off the loaded element. There is no server
            // to ask — the upload pipeline stores no dimensions, and adding an
            // image library to learn two integers is not worth it when the
            // browser has already decoded the file.
            el.onload = function () {
                if (dims) dims.textContent = el.naturalWidth + ' × ' + el.naturalHeight;
            };
            el.src = img.url;
        }

        const size = $('[data-preview-size]');
        if (size) size.textContent = formatBytes(img.size);

        const type = $('[data-preview-type]');
        if (type) type.textContent = extOf(img.filename);

        const created = $('[data-preview-created]');
        if (created) created.textContent = formatDate(img.created_at);

        const open = $('[data-preview-open]');
        if (open) open.href = absoluteUrl(img.url);

        const position = $('[data-preview-position]');
        if (position) position.textContent = (index + 1) + ' of ' + state.items.length;

        const prev = $('[data-preview-prev]');
        const next = $('[data-preview-next]');
        if (prev) prev.hidden = index <= 0;
        if (next) next.hidden = index >= state.items.length - 1;
    }

    function stepPreview(delta) {
        const index = state.items.map((i) => i.id).indexOf(state.activeId);
        const target = state.items[index + delta];
        if (!target) return;
        state.activeId = target.id;
        paintPreview(target);
    }

    function copyAs(format) {
        const img = itemById(state.activeId);
        if (!img) return;
        const url = absoluteUrl(img.url);
        const alt = img.filename || 'image';
        if (format === 'markdown') return copyText('![' + alt + '](' + url + ')', 'Markdown copied to your clipboard.');
        if (format === 'html') return copyText('<img src="' + url + '" alt="' + alt + '">', 'HTML copied to your clipboard.');
        return copyText(url, 'Image URL copied to your clipboard.');
    }

    // ----------------------------------------------------------------------
    // Delete
    // ----------------------------------------------------------------------

    function askDelete(ids) {
        const list = ids.filter(Boolean);
        if (!list.length) return;
        state.pendingDelete = list;

        const single = list.length === 1 ? itemById(list[0]) : null;

        const title = $('#deleteImageTitle');
        if (title) title.textContent = list.length === 1 ? 'Delete image' : 'Delete ' + list.length + ' images';

        const copy = $('[data-delete-copy]');
        if (copy) {
            copy.textContent = list.length === 1
                ? 'This image will be removed from your library and deleted from the server. Posts already using it will lose the image.'
                : 'These ' + list.length + ' images will be removed from your library and deleted from the server. Posts already using them will lose the image.';
        }

        const preview = $('[data-delete-preview]');
        if (preview) {
            preview.hidden = !single;
            if (single) {
                const thumb = $('[data-delete-thumb]', preview);
                const label = $('[data-delete-name]', preview);
                if (thumb) thumb.src = single.url;
                if (label) label.textContent = single.filename || 'Untitled';
            }
        }

        const confirmBtn = $('[data-delete-confirm]');
        if (confirmBtn) {
            confirmBtn.disabled = false;
            confirmBtn.textContent = 'Delete';
        }

        // Deleting from inside the preview means two dialogs want the screen.
        // Showing the second while the first is still animating out leaves
        // Bootstrap's backdrop stranded, so the confirm waits for the preview
        // to actually finish closing.
        const previewEl = document.getElementById('previewModal');
        const openDelete = () => { const m = bsModal('deleteImageModal'); if (m) m.show(); };

        if (previewEl && previewEl.classList.contains('show')) {
            previewEl.addEventListener('hidden.bs.modal', openDelete, { once: true });
            const previewModal = bsModal('previewModal');
            if (previewModal) previewModal.hide();
            return;
        }

        openDelete();
    }

    async function runDelete() {
        const ids = state.pendingDelete.slice();
        if (!ids.length) return;

        const confirmBtn = $('[data-delete-confirm]');
        if (confirmBtn) {
            confirmBtn.disabled = true;
            confirmBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Deleting…';
        }

        try {
            let deleted = [];
            if (ids.length === 1) {
                const res = await fetch('/api/gallery/images/' + encodeURIComponent(ids[0]), {
                    method: 'DELETE',
                    credentials: 'same-origin'
                });
                const data = await res.json();
                if (!data.success) throw new Error(data.error || 'Delete failed');
                deleted = ids;
            } else {
                const res = await fetch('/api/gallery/images/bulk-delete', {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ ids: ids })
                });
                const data = await res.json();
                if (!data.success) throw new Error(data.error || 'Delete failed');
                deleted = data.deleted || [];
                if ((data.failed || []).length) {
                    toast('warning', 'Partly deleted', data.failed.length + ' image(s) could not be removed.');
                }
            }

            const modal = bsModal('deleteImageModal');
            if (modal) modal.hide();

            deleted.forEach((id) => state.selected.delete(id));
            state.pendingDelete = [];
            toast('success', deleted.length === 1 ? 'Image deleted' : 'Images deleted',
                deleted.length + ' image' + (deleted.length === 1 ? '' : 's') + ' removed from your library.');

            // Reload rather than splice: the page is now one short, and the
            // server decides what backfills it — including dropping to the
            // previous page when the last item on the last page has gone.
            await load({ keepSelection: true });
        } catch (err) {
            console.error('Delete failed:', err);
            toast('error', 'Delete failed', err.message || 'Please try again.');
            if (confirmBtn) {
                confirmBtn.disabled = false;
                confirmBtn.textContent = 'Delete';
            }
        }
    }

    // ----------------------------------------------------------------------
    // Upload
    //
    // Real per-file progress via XHR, three at a time. Files are validated in
    // the browser first — a 12MB photo used to upload in full before the
    // server refused it.
    // ----------------------------------------------------------------------

    let traySeq = 0;

    function trayEl() { return $('[data-upload-tray]'); }

    function showTray() {
        const tray = trayEl();
        if (!tray) return;
        tray.hidden = false;
        tray.classList.remove('is-collapsed');
        const toggle = $('[data-tray-toggle]', tray);
        if (toggle) toggle.setAttribute('aria-expanded', 'true');
    }

    function addTrayItem(file) {
        const list = $('[data-tray-list]');
        if (!list) return null;

        const id = 'up-' + (++traySeq);
        const li = document.createElement('li');
        li.className = 'upload-item';
        li.id = id;

        // An object URL, so the row shows the actual picture while it uploads
        // rather than a generic file glyph. Revoked once the row is finished.
        const objectUrl = URL.createObjectURL(file);
        li.dataset.objectUrl = objectUrl;

        li.innerHTML = '' +
            '<img class="upload-item-thumb" src="' + objectUrl + '" alt="">' +
            '<div class="upload-item-main">' +
            '<span class="upload-item-name">' + escapeHtml(file.name) + '</span>' +
            '<span class="upload-item-note">' + formatBytes(file.size) + '</span>' +
            '<div class="upload-item-bar"><div class="upload-item-fill"></div></div>' +
            '</div>' +
            '<span class="upload-item-state"><i class="bi bi-arrow-up-circle"></i></span>';

        list.appendChild(li);
        return li;
    }

    function setTrayProgress(li, percent) {
        const fill = $('.upload-item-fill', li);
        if (fill) fill.style.width = Math.max(0, Math.min(100, percent)) + '%';
    }

    function finishTrayItem(li, ok, note) {
        if (!li) return;
        li.classList.add(ok ? 'is-done' : 'is-error');
        const state_el = $('.upload-item-state', li);
        if (state_el) {
            state_el.innerHTML = ok
                ? '<i class="bi bi-check-circle-fill"></i>'
                : '<i class="bi bi-exclamation-circle-fill"></i>';
        }
        const noteEl = $('.upload-item-note', li);
        if (noteEl && note) noteEl.textContent = note;

        if (li.dataset.objectUrl) {
            URL.revokeObjectURL(li.dataset.objectUrl);
            delete li.dataset.objectUrl;
        }
    }

    function setTrayTitle(text) {
        const title = $('[data-tray-title]');
        if (title) title.textContent = text;
    }

    function validate(file) {
        if (ALLOWED.indexOf(file.type) === -1) return 'Unsupported type — use PNG, JPG, GIF or WebP';
        if (file.size > MAX_BYTES) return 'Too large (' + formatBytes(file.size) + ') — the limit is 5MB';
        if (file.size === 0) return 'File is empty';
        return null;
    }

    function uploadOne(file, li) {
        return new Promise((resolve) => {
            const form = new FormData();
            form.append('file', file);

            const xhr = new XMLHttpRequest();
            xhr.open('POST', '/api/gallery/upload', true);
            xhr.withCredentials = true;

            xhr.upload.onprogress = function (e) {
                if (e.lengthComputable) setTrayProgress(li, (e.loaded / e.total) * 100);
            };

            xhr.onload = function () {
                let data = null;
                try { data = JSON.parse(xhr.responseText); } catch (e) { /* handled below */ }
                if (xhr.status === 200 && data && data.success) {
                    finishTrayItem(li, true, 'Uploaded · ' + formatBytes(file.size));
                    resolve({ ok: true, image: data.image });
                } else {
                    const message = (data && data.error) || 'Upload failed (' + xhr.status + ')';
                    finishTrayItem(li, false, message);
                    resolve({ ok: false, error: message });
                }
            };

            xhr.onerror = function () {
                finishTrayItem(li, false, 'Network error');
                resolve({ ok: false, error: 'Network error' });
            };

            xhr.ontimeout = function () {
                finishTrayItem(li, false, 'Timed out');
                resolve({ ok: false, error: 'Timed out' });
            };

            xhr.timeout = 60000;
            xhr.send(form);
        });
    }

    async function uploadFiles(fileList) {
        const files = Array.from(fileList || []);
        if (!files.length) return;

        showTray();

        // Rejections are queued as finished rows rather than as a toast each:
        // dropping ten files where three are wrong should say which three.
        const queue = [];
        files.forEach((file) => {
            const li = addTrayItem(file);
            const problem = validate(file);
            if (problem) finishTrayItem(li, false, problem);
            else queue.push({ file: file, li: li });
        });

        if (!queue.length) {
            setTrayTitle('Nothing to upload');
            return;
        }

        let done = 0;
        let uploaded = 0;
        const fresh = [];
        setTrayTitle('Uploading 0 of ' + queue.length);

        // A small worker pool. Strictly sequential uploads left the connection
        // idle between files; unbounded parallelism starves the page's own
        // requests on a slow link.
        let cursor = 0;
        async function worker() {
            while (cursor < queue.length) {
                const job = queue[cursor++];
                const result = await uploadOne(job.file, job.li);
                done++;
                if (result.ok && result.image) { uploaded++; fresh.push(result.image); }
                setTrayTitle('Uploading ' + done + ' of ' + queue.length);
            }
        }

        await Promise.all(
            Array.from({ length: Math.min(UPLOAD_CONCURRENCY, queue.length) }, worker)
        );

        const failed = queue.length - uploaded;
        setTrayTitle(
            failed === 0
                ? uploaded + ' image' + (uploaded === 1 ? '' : 's') + ' uploaded'
                : uploaded + ' uploaded · ' + failed + ' failed'
        );

        if (uploaded) {
            toast('success', 'Upload complete',
                uploaded + ' image' + (uploaded === 1 ? '' : 's') + ' added to your library.');

            // Land on the shelf the new files are actually on: they sort to the
            // top under "Newest first", but under any other order or an active
            // filter, re-asking the server is the only honest answer.
            if (state.sort === 'newest' && state.page === 1 && !isFiltered()) {
                state.items = fresh.concat(state.items).slice(0, PER_PAGE);
                renderBody();
            }
            await load({ keepSelection: true });
        }

        if (failed) {
            toast('error', 'Some uploads failed', failed + ' file' + (failed === 1 ? '' : 's') + ' could not be uploaded.');
        }
    }

    // ----------------------------------------------------------------------
    // Wiring
    // ----------------------------------------------------------------------

    const root = $('.dashboard-main') || document;

    root.addEventListener('click', (e) => {
        const target = e.target;

        // --- Upload entry points ---
        if (target.closest('[data-upload-trigger]')) {
            e.preventDefault();
            const input = $('#galleryFileInput');
            if (input) input.click();
            return;
        }

        // --- Type facet ---
        const tab = target.closest('.gallery-type-tabs .seg-tab');
        if (tab) {
            if (tab.dataset.type === state.type) return;
            state.type = tab.dataset.type;
            syncFilterChrome();
            resetToFirstPage();
            return;
        }

        // --- View toggle ---
        const view = target.closest('.view-toggle-btn');
        if (view) {
            if (view.dataset.view === state.view) return;
            state.view = view.dataset.view;
            localStorage.setItem(VIEW_KEY, state.view);
            syncFilterChrome();
            renderBody();
            return;
        }

        // --- Clear filters ---
        if (target.closest('[data-clear-filters]')) {
            state.search = '';
            state.type = 'all';
            const box = $('#gallerySearch');
            if (box) box.value = '';
            syncFilterChrome();
            resetToFirstPage();
            return;
        }

        // --- Pager ---
        const pageBtn = target.closest('#galleryPager .pager-btn');
        if (pageBtn && !pageBtn.classList.contains('is-disabled')) {
            const next = parseInt(pageBtn.dataset.page, 10);
            if (next && next !== state.page) {
                state.page = next;
                load();
                const card = $('.gallery-card');
                if (card) card.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
            return;
        }

        // --- Selection bar ---
        if (target.closest('[data-selection-clear]')) { clearSelection(); return; }

        if (target.closest('[data-selection-all]')) {
            if (state.selected.size === state.items.length) clearSelection();
            else { state.items.forEach((i) => state.selected.add(i.id)); syncSelectionChrome(); }
            return;
        }

        if (target.closest('[data-selection-copy]')) {
            const urls = selectedItems().map((i) => absoluteUrl(i.url)).join('\n');
            copyText(urls, state.selected.size + ' URL(s) copied, one per line.');
            return;
        }

        if (target.closest('[data-selection-delete]')) {
            askDelete(Array.from(state.selected));
            return;
        }

        // --- Tile: copy / delete / select / open ---
        const tile = target.closest('[data-media-item]');

        if (target.closest('[data-media-copy]')) {
            e.preventDefault();
            e.stopPropagation();
            if (tile) copyText(absoluteUrl(tile.dataset.url), 'Image URL copied to your clipboard.');
            return;
        }

        if (target.closest('[data-media-delete]')) {
            e.preventDefault();
            if (tile) askDelete([tile.dataset.id]);
            return;
        }

        // The checkbox drives itself; this only mirrors the result into state.
        if (target.closest('[data-media-select]')) {
            e.stopPropagation();
            if (!tile) return;
            if (e.shiftKey && state.anchor) selectRange(tile.dataset.id);
            else toggle(tile.dataset.id);
            return;
        }

        const opener = target.closest('[data-media-open]');
        if (opener && tile) {
            e.preventDefault();
            const id = tile.dataset.id;
            if (e.shiftKey && state.anchor) { selectRange(id); return; }
            if (e.metaKey || e.ctrlKey || state.selected.size > 0) { toggle(id); return; }
            openPreview(id);
            return;
        }

        // --- Preview modal ---
        if (target.closest('[data-preview-prev]')) { stepPreview(-1); return; }
        if (target.closest('[data-preview-next]')) { stepPreview(1); return; }

        const copyBtn = target.closest('[data-preview-copy]');
        if (copyBtn) { copyAs(copyBtn.dataset.previewCopy); return; }

        if (target.closest('[data-preview-delete]')) {
            if (state.activeId) askDelete([state.activeId]);
            return;
        }

        // --- Delete modal ---
        if (target.closest('[data-delete-confirm]')) { runDelete(); return; }

        // --- Upload tray chrome ---
        if (target.closest('[data-tray-toggle]')) {
            const tray = trayEl();
            if (!tray) return;
            const collapsed = tray.classList.toggle('is-collapsed');
            const btn = target.closest('[data-tray-toggle]');
            btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
            btn.setAttribute('aria-label', collapsed ? 'Expand uploads' : 'Collapse uploads');
            return;
        }

        if (target.closest('[data-tray-close]')) {
            const tray = trayEl();
            if (!tray) return;
            tray.hidden = true;
            const list = $('[data-tray-list]', tray);
            if (list) {
                // Any object URL still held by a row in flight is released here
                // rather than leaked when the markup goes.
                $$('.upload-item', list).forEach((li) => {
                    if (li.dataset.objectUrl) URL.revokeObjectURL(li.dataset.objectUrl);
                });
                list.innerHTML = '';
            }
        }
    }, { signal: signal });

    // Sort is a real <select> behind the select-pill, so this is a plain
    // change listener exactly as it was before the pill existed.
    root.addEventListener('change', (e) => {
        if (e.target.id === 'gallerySort') {
            state.sort = e.target.value;
            syncFilterChrome();
            resetToFirstPage();
            return;
        }
        if (e.target.id === 'galleryFileInput') {
            uploadFiles(e.target.files);
            e.target.value = '';
        }
    }, { signal: signal });

    // --- Header search ---
    document.addEventListener('page-search', (e) => {
        const value = ((e.detail && e.detail.value) || '').trim();
        if (value === state.search) return;
        clearTimeout(searchTimer);
        searchTimer = setTimeout(() => {
            state.search = value;
            resetToFirstPage();
        }, 250);
    }, { signal: signal });

    // --- Keyboard ---
    document.addEventListener('keydown', (e) => {
        const tag = (e.target.tagName || '').toLowerCase();
        const typing = tag === 'input' || tag === 'textarea' || e.target.isContentEditable;

        const previewOpen = !!document.querySelector('#previewModal.show');
        if (previewOpen) {
            if (e.key === 'ArrowLeft') { e.preventDefault(); stepPreview(-1); }
            if (e.key === 'ArrowRight') { e.preventDefault(); stepPreview(1); }
            return;
        }

        if (typing) return;
        if (document.querySelector('.modal.show')) return;

        // Space toggles the focused tile; the browser would otherwise scroll.
        if (e.key === ' ') {
            const tile = e.target.closest && e.target.closest('[data-media-item]');
            if (tile) { e.preventDefault(); toggle(tile.dataset.id); }
            return;
        }

        if ((e.key === 'a' || e.key === 'A') && (e.metaKey || e.ctrlKey)) {
            if (!state.items.length) return;
            e.preventDefault();
            state.items.forEach((i) => state.selected.add(i.id));
            syncSelectionChrome();
            return;
        }

        if (e.key === 'Escape' && state.selected.size) {
            clearSelection();
            return;
        }

        if ((e.key === 'Delete' || e.key === 'Backspace') && state.selected.size) {
            e.preventDefault();
            askDelete(Array.from(state.selected));
        }
    }, { signal: signal });

    // --- Drag anywhere ---
    //
    // dragenter/dragleave fire for every child element the pointer crosses, so
    // a naive show/hide flickers constantly. Counting enters against leaves is
    // the standard fix; the counter is also reset on drop and on window blur so
    // a drag that ends outside the window cannot strand the overlay on screen.
    let dragDepth = 0;

    function hasFiles(e) {
        const dt = e.dataTransfer;
        if (!dt) return false;
        if (dt.types) return Array.prototype.indexOf.call(dt.types, 'Files') !== -1;
        return false;
    }

    function setOverlay(on) {
        const overlay = $('[data-drop-overlay]');
        if (overlay) overlay.hidden = !on;
    }

    window.addEventListener('dragenter', (e) => {
        if (!hasFiles(e)) return;
        e.preventDefault();
        dragDepth++;
        setOverlay(true);
    }, { signal: signal });

    window.addEventListener('dragover', (e) => {
        if (!hasFiles(e)) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = 'copy';
    }, { signal: signal });

    window.addEventListener('dragleave', (e) => {
        if (!hasFiles(e)) return;
        dragDepth = Math.max(0, dragDepth - 1);
        if (dragDepth === 0) setOverlay(false);
    }, { signal: signal });

    window.addEventListener('drop', (e) => {
        if (!hasFiles(e)) return;
        e.preventDefault();
        dragDepth = 0;
        setOverlay(false);
        uploadFiles(e.dataTransfer.files);
    }, { signal: signal });

    window.addEventListener('blur', () => { dragDepth = 0; setOverlay(false); }, { signal: signal });

    // ----------------------------------------------------------------------
    // Boot
    // ----------------------------------------------------------------------

    hydrate();
    syncFilterChrome();

    // The server rendered the grid, not the list. If the reader's stored
    // preference is the list, swap it in before the first frame settles.
    if (state.view === 'list' && state.items.length) renderBody();

    // syncThemeControls only runs on DOMContentLoaded, which PJAX never fires.
    if (typeof window.syncThemeControls === 'function') window.syncThemeControls();
})();
