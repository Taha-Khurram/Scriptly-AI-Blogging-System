/**
 * All Blogs Listing - all_blogs.js
 * Handles blog listing, filtering, pagination, and date range modal
 */

var currentStatus = 'all';
var currentCategory = 'all';
var currentSearch = '';
var currentDateFrom = '';
var currentDateTo = '';
var currentPage = 1;
var perPage = 10;
var searchTimeout = null;
var initialLoadDone = false;

(function initAllBlogs() {
    applyUrlParams();
    setupFilterTabs();
    setupControls();
    setupCalendar();
    // Reflect anything the URL handed us (the dashboard links in with ?status=
    // and the header search with ?search=) before the first paint settles.
    updateFilterChrome();
    if (window.location.search) {
        loadBlogs();
    } else {
        initialLoadDone = true;
        // Server-rendered first page: convert its stamps the same way
        // renderBlogRow does, so paint one and paint two agree.
        document.querySelectorAll('#blogsList time[data-relative]').forEach(function (el) {
            const stamp = el.getAttribute('datetime');
            const text = relativeDate(stamp);
            if (!text) return;
            el.title = formatDate(stamp);
            el.textContent = text;
        });
    }
    // syncThemeControls only runs on DOMContentLoaded, which PJAX never fires.
    if (typeof window.syncThemeControls === 'function') window.syncThemeControls();
})();

function applyUrlParams() {
    var params = new URLSearchParams(window.location.search);
    var status = params.get('status');
    if (status) {
        currentStatus = status;
        document.querySelectorAll('.blogs-filter-tabs .seg-tab').forEach(function(tab) {
            tab.classList.remove('is-active');
            if (tab.dataset.filter === status) {
                tab.classList.add('is-active');
            }
        });
    }

    // ?search= is how the dashboard's header search hands a query off to this
    // screen — mirror it into the field so the reader can see and edit what is
    // being filtered on, rather than facing a narrowed list with an empty box.
    var search = (params.get('search') || '').trim();
    if (search) {
        currentSearch = search;
        var searchField = document.getElementById('searchInput');
        if (searchField) searchField.value = search;
    }
}

// ==================== FILTER TABS ====================

function setupFilterTabs() {
    document.querySelectorAll('.blogs-filter-tabs .seg-tab').forEach(tab => {
        tab.addEventListener('click', function () {
            document.querySelectorAll('.blogs-filter-tabs .seg-tab').forEach(t => t.classList.remove('is-active'));
            this.classList.add('is-active');
            currentStatus = this.dataset.filter;
            currentPage = 1;
            updateFilterChrome();
            loadBlogs();
        });
    });
}

// ==================== CONTROLS ====================

// ==================== FILTER CHROME ====================
//
// Keeps the pills showing what is actually applied, and only offers "Clear all"
// when there is something to clear. Status is deliberately excluded from the
// "is anything applied" test: "All" is its resting state, not a filter.

function updateFilterChrome() {
    const catPill = document.querySelector('[data-pill="category"]');
    const catValue = document.getElementById('categoryFilterValue');

    if (catPill && catValue) {
        const on = currentCategory && currentCategory !== 'all';
        catPill.classList.toggle('is-active', !!on);
        catValue.hidden = !on;
        catValue.textContent = on ? currentCategory : '';
    }

    const datePill = document.getElementById('dateFilterBtn');
    if (datePill) datePill.classList.toggle('is-active', !!(currentDateFrom || currentDateTo));

    const clearBtn = document.getElementById('clearFiltersBtn');
    if (clearBtn) {
        const anyApplied = (currentStatus && currentStatus !== 'all')
            || (currentCategory && currentCategory !== 'all')
            || !!currentSearch
            || !!currentDateFrom
            || !!currentDateTo;
        clearBtn.hidden = !anyApplied;
    }
}

function clearAllFilters() {
    currentStatus = 'all';
    currentCategory = 'all';
    currentSearch = '';
    currentDateFrom = '';
    currentDateTo = '';
    currentPage = 1;

    document.querySelectorAll('.blogs-filter-tabs .seg-tab').forEach(function (tab) {
        tab.classList.toggle('is-active', tab.dataset.filter === 'all');
    });

    const cat = document.getElementById('categoryFilter');
    if (cat) cat.value = 'all';

    const search = document.getElementById('searchInput');
    if (search) {
        search.value = '';
        // The header component owns the field's own chrome (the clear button).
        const box = search.closest('[data-page-search]');
        if (box) box.classList.remove('has-value');
    }

    const dateBtn = document.getElementById('dateFilterBtn');
    const dateLabel = document.getElementById('dateFilterLabel');
    if (dateBtn) dateBtn.classList.remove('is-active');
    if (dateLabel) dateLabel.textContent = 'Date range';

    updateFilterChrome();
    loadBlogs();
}

function setupControls() {
    const categorySelect = document.getElementById('categoryFilter');
    categorySelect.addEventListener('change', function () {
        currentCategory = this.value;
        currentPage = 1;
        updateFilterChrome();
        loadBlogs();
    });

    const searchInput = document.getElementById('searchInput');
    searchInput.addEventListener('input', function () {
        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(() => {
            currentSearch = this.value.trim();
            currentPage = 1;
            updateFilterChrome();
            loadBlogs();
        }, 300);
    });
}

// ==================== LOAD BLOGS ====================

async function loadBlogs() {
    const listEl = document.getElementById('blogsList');
    listEl.innerHTML = `
        <div class="blogs-state">
            <div class="spinner-border spinner-border-sm text-primary opacity-50"></div>
            <p class="mb-0">Loading blogs…</p>
        </div>`;

    try {
        const params = new URLSearchParams({
            page: currentPage,
            per_page: perPage
        });
        if (currentStatus !== 'all') params.set('status', currentStatus);
        if (currentCategory !== 'all') params.set('category', currentCategory);
        if (currentSearch) params.set('search', currentSearch);
        if (currentDateFrom) params.set('date_from', currentDateFrom);
        if (currentDateTo) params.set('date_to', currentDateTo);

        const res = await fetch('/api/all-blogs?' + params.toString());
        const data = await res.json();

        if (data.success && data.blogs && data.blogs.length > 0) {
            listEl.innerHTML = data.blogs.map(renderBlogRow).join('');
            renderPagination(data.total, data.page, data.per_page);
            updateResultSummary(data.total, data.page, data.per_page);
        } else {
            updateResultSummary(0, 1, perPage);
            listEl.innerHTML = `
                <div class="list-empty">
                    <span class="list-empty-icon"><i class="material-symbols-outlined icon-inline" aria-hidden="true">library_books</i></span>
                    <p>No blogs found. Try a different status, category or date range.</p>
                </div>`;
            document.getElementById('blogsPagination').innerHTML = '';
        }
    } catch (err) {
        console.error('Error loading blogs:', err);
        listEl.innerHTML = `
            <div class="blogs-state is-error">
                <i class="material-symbols-outlined icon-inline" aria-hidden="true">error</i>
                <p class="mb-0">Failed to load blogs.</p>
            </div>`;
    }
}

// ==================== RENDER ROW ====================

// Mirrors the row the template renders for the first page, so a filtered or
// paginated list is indistinguishable from a fresh load. Titles are no longer
// truncated in JS — the CSS ellipsis does it, which keeps the full text
// selectable and correct at every column width.
function renderBlogRow(blog) {
    const id = blog.id;
    const title = escapeHtml(blog.title || 'Untitled');
    const authorName = escapeHtml(blog.author_name || blog.user_name || 'Unknown');
    // The blog's initial, not the author's — on a single-author site every row
    // would otherwise carry the same letter. Asterisks are stripped so a title
    // that arrives with markdown emphasis still yields a letter.
    const mark = (blog.title || 'Untitled').replace(/\*/g, '').trim();
    const initial = (mark.charAt(0) || '?').toUpperCase();
    const category = escapeHtml(blog.category || 'Uncategorized');
    const status = blog.status || 'DRAFT';
    const stamp = blog.updated_at || blog.created_at;
    const updatedAt = relativeDate(stamp);
    const exactAt = escapeHtml(formatDate(stamp));

    return `
    <div class="data-row blog-row" id="row-${id}">
        <span class="row-mark" aria-hidden="true">${escapeHtml(initial)}</span>

        <button type="button" class="row-open" onclick="openEditModal('${id}')" title="Edit &ldquo;${title}&rdquo;">
            <span class="row-title blog-title-cell">${title}</span>
            <span class="row-meta">
                <span>${authorName}</span>
                <span class="row-sep" aria-hidden="true">·</span>
                <span>${category}</span>
            </span>
        </button>

        ${getStatusBadge(status)}
        <span class="row-time blog-date-cell" title="${exactAt}">${updatedAt}</span>

        <div class="row-trail">
            <div class="dropdown">
                <button class="btn-dropdown-trigger" type="button" data-bs-toggle="dropdown" aria-expanded="false"
                    aria-label="More actions for ${title}">
                    <i class="material-symbols-outlined icon-inline" aria-hidden="true">more_vert</i>
                </button>
                <ul class="dropdown-menu dropdown-menu-end">
                    <li>
                        <button class="dropdown-item" onclick="openEditModal('${id}')">
                            <i class="material-symbols-outlined icon-inline" style="color: var(--primary-color);" aria-hidden="true">edit_square</i> Edit Blog
                        </button>
                    </li>
                    <li><hr class="dropdown-divider"></li>
                    <li>
                        <button class="dropdown-item text-danger" onclick="deleteBlog('${id}')">
                            <i class="material-symbols-outlined icon-inline" aria-hidden="true">cancel</i> Delete
                        </button>
                    </li>
                </ul>
            </div>
        </div>
    </div>`;
}

// ==================== STATUS BADGE ====================

function getStatusBadge(status) {
    const statusMap = {
        'DRAFT': { label: 'Draft', cls: 'status-draft' },
        'UNDER_REVIEW': { label: 'Under review', cls: 'status-under_review' },
        'PUBLISHED': { label: 'Published', cls: 'status-published' },
        'REJECTED': { label: 'Rejected', cls: 'status-rejected' }
    };
    const info = statusMap[status] || statusMap['DRAFT'];
    return `<span class="status-pill ${info.cls}">${info.label}</span>`;
}

// The card head carries the same "count + page" pair the drafts list does, so
// the two screens read as one listing with different contents.
function updateResultSummary(total, page, perPage) {
    const count = document.getElementById('blogsCount');
    if (count) count.textContent = total;

    const note = document.getElementById('blogsPageNote');
    if (note) {
        const totalPages = Math.ceil(total / perPage);
        note.textContent = totalPages > 1 ? `Page ${page} of ${totalPages}` : '';
    }
}

// ==================== PAGINATION ====================

function renderPagination(total, page, perPage) {
    const container = document.getElementById('blogsPagination');
    const totalPages = Math.ceil(total / perPage);
    if (totalPages <= 1) { container.innerHTML = ''; return; }

    let html = '';
    html += `<button class="pager-btn ${page <= 1 ? 'is-disabled' : ''}" onclick="goToPage(${page - 1})" ${page <= 1 ? 'disabled' : ''} aria-label="Previous page">
        <i class="material-symbols-outlined icon-inline" aria-hidden="true">chevron_left</i>
    </button>`;

    for (let i = 1; i <= totalPages; i++) {
        if (i === 1 || i === totalPages || (i >= page - 1 && i <= page + 1)) {
            html += `<button class="pager-btn ${i === page ? 'is-active' : ''}" onclick="goToPage(${i})" ${i === page ? 'aria-current="page"' : ''}>${i}</button>`;
        } else if (i === page - 2 || i === page + 2) {
            html += `<span class="pager-dots">…</span>`;
        }
    }

    html += `<button class="pager-btn ${page >= totalPages ? 'is-disabled' : ''}" onclick="goToPage(${page + 1})" ${page >= totalPages ? 'disabled' : ''} aria-label="Next page">
        <i class="material-symbols-outlined icon-inline" aria-hidden="true">chevron_right</i>
    </button>`;

    container.innerHTML = html;
}

function goToPage(page) {
    currentPage = page;
    loadBlogs();
    document.querySelector('.blogs-container').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ==================== DATE RANGE MODAL ====================

// Each preset is a pure function of "today", so the same definitions drive both
// setting a range and recognising one that is already set.
const DATE_PRESETS = {
    today: 0,
    week: 7,
    month: 30,
    quarter: 90
};

// Local, not toISOString(). toISOString() converts to UTC first, so anywhere
// behind UTC "today" comes back as yesterday for most of the day.
function isoLocal(date) {
    const m = String(date.getMonth() + 1).padStart(2, '0');
    const d = String(date.getDate()).padStart(2, '0');
    return date.getFullYear() + '-' + m + '-' + d;
}

function isoDaysAgo(days) {
    const d = new Date();
    d.setDate(d.getDate() - days);
    return isoLocal(d);
}

function todayIso() {
    return isoLocal(new Date());
}

function parseIso(iso) {
    if (!iso) return null;
    const [y, m, d] = iso.split('-').map(Number);
    if (!y || !m || !d) return null;
    return new Date(y, m - 1, d);
}

// ==================== CALENDAR ====================
//
// A range picker over the two hidden inputs. Selection is two clicks: the
// first sets the start and arms the range, the second closes it — and picking
// a second date before the first swaps them rather than rejecting the click.

var calCursor = new Date();   // month on screen
var calPicking = false;       // start chosen, waiting for the end
var calPreview = '';          // hovered day while picking

function calFrom() { return document.getElementById('dateFrom').value; }
function calTo() { return document.getElementById('dateTo').value; }

function setRange(from, to) {
    document.getElementById('dateFrom').value = from || '';
    document.getElementById('dateTo').value = to || '';
    paintCalendar();
    syncDateChrome();
}

// Building and painting are separate on purpose. Hovering while picking
// repaints the band on every mousemove, and rebuilding the grid's innerHTML
// that often would destroy the very button the pointer is over — losing
// :hover, losing focus, and flickering. buildCalendar() runs on a month
// change; paintCalendar() only rewrites class names on cells that already
// exist.
function buildCalendar() {
    const grid = document.querySelector('[data-cal-grid]');
    if (!grid) return;

    const title = document.querySelector('[data-cal-title]');
    if (title) {
        title.textContent = calCursor.toLocaleDateString('en-US', { month: 'long', year: 'numeric' });
    }

    // Start on the Sunday on or before the 1st, then lay out six full weeks so
    // the grid never changes height as the month changes.
    const first = new Date(calCursor.getFullYear(), calCursor.getMonth(), 1);
    const start = new Date(first);
    start.setDate(1 - first.getDay());

    let html = '';
    for (let i = 0; i < 42; i++) {
        const day = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i);
        html += '<div class="cal-cell" data-cell="' + isoLocal(day) + '">' +
            '<button type="button" class="cal-day" data-iso="' + isoLocal(day) + '" ' +
            'aria-label="' + day.toLocaleDateString('en-US', { dateStyle: 'full' }) + '">' +
            day.getDate() + '</button></div>';
    }
    grid.innerHTML = html;
    paintCalendar();
}

function paintCalendar() {
    const grid = document.querySelector('[data-cal-grid]');
    if (!grid) return;

    const cal = document.querySelector('[data-calendar]');
    if (cal) cal.classList.toggle('is-picking', calPicking);

    const from = calFrom();
    const open = calPicking && !calTo();
    const to = calTo() || (open ? calPreview : '');
    const lo = from && to ? (from <= to ? from : to) : from;
    const hi = from && to ? (from <= to ? to : from) : '';
    const today = todayIso();
    const month = calCursor.getMonth();

    grid.querySelectorAll('.cal-cell').forEach(function (cell) {
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

function onDayClick(iso) {
    if (!calPicking && !(calFrom() && !calTo())) {
        // Fresh selection: this is the start, and the range is open.
        calPicking = true;
        calPreview = iso;
        setRange(iso, '');
        return;
    }

    const start = calFrom();
    calPicking = false;
    calPreview = '';
    setRange(iso < start ? iso : start, iso < start ? start : iso);
}

function setupCalendar() {
    const cal = document.querySelector('[data-calendar]');
    if (!cal) return;

    // Bound to the calendar element, which PJAX replaces with the page — so no
    // document-level listener survives to fire against a dead DOM.
    cal.addEventListener('click', function (e) {
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
        if (day) onDayClick(day.dataset.iso);
    });

    cal.addEventListener('mouseover', function (e) {
        const day = e.target.closest('.cal-day');
        if (!day || !calPicking) return;
        calPreview = day.dataset.iso;
        paintCalendar();
    });

    cal.addEventListener('keydown', function (e) {
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

        const target = cal.querySelector('.cal-day[data-iso="' + isoLocal(next) + '"]');
        if (target) target.focus();
    });
}

function openDateModal() {
    calPicking = false;
    calPreview = '';
    // Open on the month the range ends in, so an existing selection is on
    // screen instead of "today" with the choice scrolled out of view.
    calCursor = parseIso(currentDateTo) || parseIso(currentDateFrom) || new Date();
    buildCalendar();
    setRange(currentDateFrom, currentDateTo);

    const modal = new bootstrap.Modal(document.getElementById('dateRangeModal'));
    modal.show();
}

function setDatePreset(preset) {
    calPicking = false;
    calPreview = '';

    if (preset === 'all') {
        setRange('', '');
        return;
    }

    const to = todayIso();
    const from = preset === 'year'
        ? new Date().getFullYear() + '-01-01'
        : isoDaysAgo(DATE_PRESETS[preset] || 0);

    calCursor = parseIso(to);
    buildCalendar();
    setRange(from, to);
}

// Which preset, if any, the current field values correspond to. Lets the modal
// show the active range on reopen instead of six identical buttons.
function matchingPreset(from, to) {
    if (!from && !to) return 'all';
    if (to !== todayIso()) return null;

    for (const key in DATE_PRESETS) {
        if (from === isoDaysAgo(DATE_PRESETS[key])) return key;
    }
    if (from === new Date().getFullYear() + '-01-01') return 'year';
    return null;
}

function describeRange(from, to) {
    const pretty = (d) => new Date(d + 'T00:00:00').toLocaleDateString('en-US',
        { month: 'short', day: 'numeric', year: 'numeric' });

    if (!from && !to) return 'Showing all time';
    if (from && !to) return 'Showing from ' + pretty(from);
    if (!from && to) return 'Showing up to ' + pretty(to);
    if (from === to) return 'Showing ' + pretty(from);
    return 'Showing ' + pretty(from) + ' – ' + pretty(to);
}

// Keeps the preset highlight, the summary line and the Apply button in step
// with the current selection.
function syncDateChrome() {
    const from = calFrom();
    const to = calTo();

    const active = matchingPreset(from, to);
    document.querySelectorAll('.date-preset').forEach(function (btn) {
        btn.classList.toggle('is-active', btn.dataset.preset === active);
    });

    const summary = document.getElementById('dateSummary');
    const half = !!(from && !to);
    if (summary) {
        summary.textContent = half ? 'Pick the end of the range' : describeRange(from, to);
        summary.classList.toggle('is-hint', half);
    }

    // A half-open range would silently become "everything since", so Apply
    // waits until the second date is in.
    const apply = document.getElementById('dateApplyBtn');
    if (apply) apply.disabled = half;
}

function applyDateFilter() {
    currentDateFrom = document.getElementById('dateFrom').value;
    currentDateTo = document.getElementById('dateTo').value;
    currentPage = 1;

    const btn = document.getElementById('dateFilterBtn');
    const label = document.getElementById('dateFilterLabel');

    if (currentDateFrom || currentDateTo) {
        btn.classList.add('is-active');
        const from = currentDateFrom ? formatShortDate(currentDateFrom) : 'Any';
        const to = currentDateTo ? formatShortDate(currentDateTo) : 'Any';
        label.textContent = `${from} – ${to}`;
    } else {
        btn.classList.remove('is-active');
        label.textContent = 'Date range';
    }

    bootstrap.Modal.getInstance(document.getElementById('dateRangeModal')).hide();
    updateFilterChrome();
    loadBlogs();
}

function clearDateFilter() {
    calPicking = false;
    calPreview = '';
    setRange('', '');
    currentDateFrom = '';
    currentDateTo = '';
    currentPage = 1;

    document.getElementById('dateFilterBtn').classList.remove('is-active');
    document.getElementById('dateFilterLabel').textContent = 'Date range';

    bootstrap.Modal.getInstance(document.getElementById('dateRangeModal')).hide();
    updateFilterChrome();
    loadBlogs();
}

// ==================== HELPERS ====================

function formatDate(timestamp) {
    if (!timestamp) return '';
    try {
        const date = new Date(timestamp);
        return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
    } catch {
        return '';
    }
}

// Relative "when", matching the drafts list. The absolute date stays on the
// element's title attribute, so the exact value is never lost — including at
// widths where the column itself is hidden.
function relativeDate(timestamp) {
    if (!timestamp) return '';
    const then = new Date(timestamp);
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
    return formatDate(timestamp);
}

function formatShortDate(dateStr) {
    try {
        const d = new Date(dateStr + 'T00:00:00');
        return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    } catch {
        return dateStr;
    }
}

function truncate(str, max) {
    if (!str) return '';
    return str.length > max ? str.substring(0, max) + '...' : str;
}

function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// ==================== EDIT / DELETE ACTIONS ====================
// Reuses the shared blog editing flow (same backend as drafts): loads the blog
// into a TinyMCE modal, saves via /api/update_blog, deletes via /api/delete_blog.
// Works for any blog regardless of status, including PUBLISHED ones.

var currentEditingId = null;

function generateSlug(title) {
    if (!title) return '';
    return title.toLowerCase()
        .replace(/[^\w\s-]/g, '')
        .replace(/[\s_]+/g, '-')
        .replace(/-+/g, '-')
        .trim()
        .replace(/^-|-$/g, '')
        .substring(0, 100);
}

function initEditor(initialContent) {
    if (window.tinymce && tinymce.get('editor-canvas')) {
        tinymce.remove('#editor-canvas');
    }
    tinymce.init({
        selector: '#editor-canvas',
        plugins: 'anchor autolink charmap codesample emoticons image link lists media searchreplace table visualblocks wordcount',
        toolbar: 'undo redo | blocks fontfamily fontsize | bold italic underline strikethrough | link image media table | align lineheight | numlist bullist indent outdent | emoticons charmap | removeformat',
        height: 500,
        menubar: false,
        statusbar: false,
        setup: function (editor) {
            editor.on('init', function () {
                editor.setContent(initialContent || '');
            });
        }
    });
}

async function openEditModal(id) {
    currentEditingId = id;
    if (window.closeAllDropdowns) closeAllDropdowns();
    showActionLoader('Loading blog...');
    try {
        const res = await fetch(`/api/get_blog/${id}`);
        if (!res.ok) throw new Error(`Server error (${res.status})`);
        const data = await res.json();
        if (data.success) {
            hideActionLoader();
            document.getElementById('modal-title').value = data.blog.title || '';
            document.getElementById('modal-slug').value = data.blog.slug || '';
            document.getElementById('modal-seo-title').value = data.blog.seo_title || '';
            document.getElementById('modal-seo-description').value = data.blog.seo_description || '';
            updateSeoCounters();
            setCoverImagePreview(data.blog.cover_image || '');

            const modalElement = document.getElementById('editModal');
            const editModal = new bootstrap.Modal(modalElement);
            editModal.show();

            let content = '';
            const blogContent = data.blog.content;
            if (typeof blogContent === 'object' && blogContent !== null) {
                content = blogContent.html || blogContent.body || '';
            } else {
                content = blogContent || '';
            }
            initEditor(content);

            // Rebind slug listeners (clone to drop stale handlers)
            const titleInput = document.getElementById('modal-title');
            const regenerateBtn = document.getElementById('regenerate-slug');
            const newTitleInput = titleInput.cloneNode(true);
            titleInput.parentNode.replaceChild(newTitleInput, titleInput);
            const newRegenerateBtn = regenerateBtn.cloneNode(true);
            regenerateBtn.parentNode.replaceChild(newRegenerateBtn, regenerateBtn);

            newTitleInput.addEventListener('blur', function () {
                const slugField = document.getElementById('modal-slug');
                if (!slugField.value) {
                    slugField.value = generateSlug(this.value);
                }
            });
            newRegenerateBtn.addEventListener('click', function () {
                document.getElementById('modal-slug').value = generateSlug(document.getElementById('modal-title').value);
            });

            setupSeoToggle();
        } else {
            hideActionLoader();
            showToast({ type: 'error', title: 'Error', message: data.message || 'Failed to load blog.', duration: 5000 });
        }
    } catch (err) {
        hideActionLoader();
        console.error('openEditModal error:', err);
        showToast({ type: 'error', title: 'Connection Error', message: err.message || 'Could not connect to server.', duration: 5000 });
    }
}

function setupSeoToggle() {
    const toggleBtn = document.getElementById('seo-toggle-btn');
    const seoFields = document.getElementById('seo-fields');
    const seoTitle = document.getElementById('modal-seo-title');
    const seoDesc = document.getElementById('modal-seo-description');

    const newToggleBtn = toggleBtn.cloneNode(true);
    toggleBtn.parentNode.replaceChild(newToggleBtn, toggleBtn);
    newToggleBtn.addEventListener('click', function () {
        const isVisible = seoFields.style.display !== 'none';
        seoFields.style.display = isVisible ? 'none' : 'block';
        this.classList.toggle('active', !isVisible);
    });

    seoTitle.addEventListener('input', updateSeoCounters);
    seoDesc.addEventListener('input', updateSeoCounters);
}

function updateSeoCounters() {
    document.getElementById('seo-title-count').textContent = document.getElementById('modal-seo-title').value.length;
    document.getElementById('seo-desc-count').textContent = document.getElementById('modal-seo-description').value.length;
}

function copyBlogContent() {
    const editor = window.tinymce && tinymce.get('editor-canvas');
    if (!editor) return;
    const content = editor.getContent({ format: 'text' });
    navigator.clipboard.writeText(content).then(function () {
        showToast({ type: 'success', title: 'Copied', message: 'Content copied to clipboard.', duration: 2000 });
    }).catch(function () {
        showToast({ type: 'error', title: 'Error', message: 'Failed to copy content.', duration: 3000 });
    });
}

async function saveModalChanges() {
    const updatedTitle = document.getElementById('modal-title').value;
    const editor = window.tinymce && tinymce.get('editor-canvas');
    if (!editor) return;
    const updatedContent = editor.getContent();

    let slug = document.getElementById('modal-slug').value.trim();
    if (!slug) slug = generateSlug(updatedTitle);

    const seoTitle = document.getElementById('modal-seo-title').value.trim();
    const seoDescription = document.getElementById('modal-seo-description').value.trim();

    const coverImageImg = document.getElementById('coverImageImg');
    const coverImage = coverImageImg ? coverImageImg.src : '';
    const coverImageValue = document.getElementById('coverImagePreview').style.display !== 'none' ? coverImage : '';

    const saveBtn = document.getElementById('save-changes-btn');
    const originalContent = saveBtn.innerHTML;
    saveBtn.disabled = true;
    saveBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status"></span> Saving...';

    try {
        const res = await fetch(`/api/update_blog/${currentEditingId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                title: updatedTitle,
                content: updatedContent,
                slug: slug,
                seo_title: seoTitle,
                seo_description: seoDescription,
                cover_image: coverImageValue
            })
        });
        const data = await res.json();
        if (data.success) {
            // Update the row title in place without a full reload
            const titleCell = document.querySelector(`#row-${currentEditingId} .blog-title-cell`);
            if (titleCell) {
                titleCell.textContent = updatedTitle;
                const opener = titleCell.closest('.row-open');
                if (opener) opener.setAttribute('title', `Edit “${updatedTitle}”`);
            }
            bootstrap.Modal.getInstance(document.getElementById('editModal')).hide();
            saveBtn.disabled = false;
            saveBtn.innerHTML = originalContent;
            showToast({ type: 'success', title: 'Changes Saved', message: 'Your blog has been updated successfully.', duration: 4000 });
        } else {
            saveBtn.disabled = false;
            saveBtn.innerHTML = originalContent;
            showToast({ type: 'error', title: 'Save Failed', message: data.error || 'Could not save changes.', duration: 5000 });
        }
    } catch (err) {
        saveBtn.disabled = false;
        saveBtn.innerHTML = originalContent;
        showToast({ type: 'error', title: 'Error', message: 'Failed to save changes.', duration: 5000 });
    }
}

async function deleteBlog(id) {
    if (window.closeAllDropdowns) closeAllDropdowns();
    showActionLoader('Deleting blog...');

    const row = document.getElementById(`row-${id}`);
    const dropdownBtn = row ? row.querySelector('.btn-dropdown-trigger') : null;
    if (dropdownBtn) {
        dropdownBtn.disabled = true;
        dropdownBtn.innerHTML = '<div class="spinner-border spinner-border-sm" role="status"></div>';
    }

    try {
        const res = await fetch(`/api/delete_blog/${id}`, { method: 'DELETE' });
        const data = await res.json();
        if (data.success) {
            hideActionLoader();
            showToast({ type: 'warning', title: 'Blog Deleted', message: 'The blog has been permanently removed.', duration: 4000 });
            if (row) {
                row.style.transition = 'all 0.3s ease';
                row.style.opacity = '0';
                row.style.transform = 'translateX(20px)';
                setTimeout(() => {
                    row.remove();
                    if (!document.querySelector('#blogsList .blog-row')) {
                        loadBlogs();
                    }
                }, 300);
            }
        } else {
            hideActionLoader();
            if (dropdownBtn) {
                dropdownBtn.disabled = false;
                dropdownBtn.innerHTML = '<i class="material-symbols-outlined icon-inline" aria-hidden="true">more_vert</i>';
            }
            showToast({ type: 'error', title: 'Delete Failed', message: data.error || 'Could not delete blog.', duration: 5000 });
        }
    } catch (e) {
        hideActionLoader();
        if (dropdownBtn) {
            dropdownBtn.disabled = false;
            dropdownBtn.innerHTML = '<i class="material-symbols-outlined icon-inline" aria-hidden="true">more_vert</i>';
        }
        showToast({ type: 'error', title: 'Error', message: 'Failed to delete blog.', duration: 5000 });
    }
}

// ==================== COVER IMAGE PICKER ====================

function setCoverImagePreview(url) {
    var preview = document.getElementById('coverImagePreview');
    var img = document.getElementById('coverImageImg');
    var placeholder = document.getElementById('coverImagePlaceholder');
    if (url) {
        img.src = url;
        preview.style.display = 'block';
        if (placeholder) placeholder.style.display = 'none';
    } else {
        img.src = '';
        preview.style.display = 'none';
        if (placeholder) placeholder.style.display = '';
    }
}

function removeCoverImage() {
    setCoverImagePreview('');
}

function openImagePicker() {
    var overlay = document.getElementById('imagePickerOverlay');
    var grid = document.getElementById('imagePickerGrid');
    overlay.classList.add('active');
    grid.innerHTML = '<div class="text-center py-4" style="grid-column:1/-1;"><div class="spinner-border spinner-border-sm text-primary opacity-50"></div><p class="text-secondary mt-2 mb-0" style="font-size:0.82rem;">Loading gallery...</p></div>';

    var xhr = new XMLHttpRequest();
    xhr.open('GET', '/api/gallery/images?per_page=50', true);
    xhr.timeout = 10000;
    xhr.onload = function () {
        try {
            if (xhr.status === 200) {
                var data = JSON.parse(xhr.responseText);
                if (data.success && data.images && data.images.length > 0) {
                    var html = '';
                    for (var i = 0; i < data.images.length; i++) {
                        var img = data.images[i];
                        html += '<div class="image-picker-item" onclick="selectCoverImage(\'' + img.url + '\')">';
                        html += '<img src="' + img.url + '" alt="' + (img.filename || '') + '" loading="lazy">';
                        html += '</div>';
                    }
                    grid.innerHTML = html;
                } else {
                    grid.innerHTML = '<div class="image-picker-empty" style="grid-column:1/-1;"><i class="material-symbols-outlined icon-inline" style="font-size:1.5rem;display:block;margin-bottom:0.5rem;" aria-hidden="true">photo_library</i>No images in gallery. Upload images on the Gallery page first.</div>';
                }
            } else {
                grid.innerHTML = '<div class="image-picker-empty" style="grid-column:1/-1;">Error loading images.</div>';
            }
        } catch (e) {
            grid.innerHTML = '<div class="image-picker-empty" style="grid-column:1/-1;">Error loading images.</div>';
        }
    };
    xhr.onerror = function () {
        grid.innerHTML = '<div class="image-picker-empty" style="grid-column:1/-1;">Error loading images.</div>';
    };
    xhr.ontimeout = function () {
        grid.innerHTML = '<div class="image-picker-empty" style="grid-column:1/-1;">Error loading images.</div>';
    };
    xhr.send();
}

function closeImagePicker(event) {
    if (event && event.target !== event.currentTarget) return;
    document.getElementById('imagePickerOverlay').classList.remove('active');
}

function selectCoverImage(url) {
    setCoverImagePreview(url);
    closeImagePicker();
}
