/**
 * All Blogs Listing - all_blogs.js
 * Handles blog listing, filtering, pagination, and date range modal
 */

let currentStatus = 'all';
let currentCategory = 'all';
let currentSearch = '';
let currentDateFrom = '';
let currentDateTo = '';
let currentPage = 1;
const perPage = 10;
let searchTimeout = null;
let initialLoadDone = false;

(function initAllBlogs() {
    applyUrlParams();
    setupFilterTabs();
    setupControls();
    if (window.location.search) {
        loadBlogs();
    } else {
        initialLoadDone = true;
    }
})();

function applyUrlParams() {
    var params = new URLSearchParams(window.location.search);
    var status = params.get('status');
    if (status) {
        currentStatus = status;
        document.querySelectorAll('.blogs-filter-tabs .filter-tab').forEach(function(tab) {
            tab.classList.remove('active');
            if (tab.dataset.filter === status) {
                tab.classList.add('active');
            }
        });
    }
}

// ==================== FILTER TABS ====================

function setupFilterTabs() {
    document.querySelectorAll('.blogs-filter-tabs .filter-tab').forEach(tab => {
        tab.addEventListener('click', function () {
            document.querySelectorAll('.blogs-filter-tabs .filter-tab').forEach(t => t.classList.remove('active'));
            this.classList.add('active');
            currentStatus = this.dataset.filter;
            currentPage = 1;
            loadBlogs();
        });
    });
}

// ==================== CONTROLS ====================

function setupControls() {
    const categorySelect = document.getElementById('categoryFilter');
    categorySelect.addEventListener('change', function () {
        currentCategory = this.value;
        currentPage = 1;
        loadBlogs();
    });

    const searchInput = document.getElementById('searchInput');
    searchInput.addEventListener('input', function () {
        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(() => {
            currentSearch = this.value.trim();
            currentPage = 1;
            loadBlogs();
        }, 300);
    });
}

// ==================== LOAD BLOGS ====================

async function loadBlogs() {
    const listEl = document.getElementById('blogsList');
    listEl.innerHTML = `
        <div class="text-center py-5">
            <div class="spinner-border spinner-border-sm text-primary opacity-50"></div>
            <p class="text-secondary fw-medium mt-2 mb-0">Loading blogs...</p>
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
        } else {
            listEl.innerHTML = `
                <div class="blogs-empty">
                    <i class="bi bi-journals"></i>
                    <p>No blogs found.</p>
                </div>`;
            document.getElementById('blogsPagination').innerHTML = '';
        }
    } catch (err) {
        console.error('Error loading blogs:', err);
        listEl.innerHTML = `
            <div class="text-center py-4 text-danger">
                <i class="bi bi-exclamation-circle"></i> Failed to load blogs.
            </div>`;
    }
}

// ==================== RENDER ROW ====================

function renderBlogRow(blog) {
    const title = escapeHtml(blog.title || 'Untitled');
    const authorName = escapeHtml(blog.author_name || blog.user_name || 'Unknown');
    const initial = authorName.charAt(0).toUpperCase();
    const category = escapeHtml(blog.category || 'Uncategorized');
    const status = blog.status || 'DRAFT';
    const updatedAt = formatDate(blog.updated_at || blog.created_at);

    return `
    <div class="blog-row">
        <div class="col-blog-title">
            <span class="blog-title-cell" title="${title}">${truncate(title, 45)}</span>
        </div>
        <div class="col-blog-author">
            <div class="blog-author-cell">
                <div class="blog-author-avatar">${initial}</div>
                <span class="blog-author-name">${truncate(authorName, 20)}</span>
            </div>
        </div>
        <div class="col-blog-category">
            <span class="blog-category-badge">${category}</span>
        </div>
        <div class="col-blog-status">
            ${getStatusBadge(status)}
        </div>
        <div class="col-blog-date">
            <span class="blog-date-cell">${updatedAt}</span>
        </div>
    </div>`;
}

// ==================== STATUS BADGE ====================

function getStatusBadge(status) {
    const statusMap = {
        'DRAFT': { label: 'Draft', cls: 'status-badge-draft' },
        'UNDER_REVIEW': { label: 'Under Review', cls: 'status-badge-under_review' },
        'PUBLISHED': { label: 'Published', cls: 'status-badge-published' },
        'REJECTED': { label: 'Rejected', cls: 'status-badge-rejected' }
    };
    const info = statusMap[status] || statusMap['DRAFT'];
    return `<span class="blog-status-badge ${info.cls}"><i class="bi bi-circle-fill"></i> ${info.label}</span>`;
}

// ==================== PAGINATION ====================

function renderPagination(total, page, perPage) {
    const container = document.getElementById('blogsPagination');
    const totalPages = Math.ceil(total / perPage);
    if (totalPages <= 1) { container.innerHTML = ''; return; }

    let html = '';
    html += `<button class="page-btn ${page <= 1 ? 'disabled' : ''}" onclick="goToPage(${page - 1})" ${page <= 1 ? 'disabled' : ''}>
        <i class="bi bi-chevron-left"></i>
    </button>`;

    for (let i = 1; i <= totalPages; i++) {
        if (i === 1 || i === totalPages || (i >= page - 1 && i <= page + 1)) {
            html += `<button class="page-btn ${i === page ? 'active' : ''}" onclick="goToPage(${i})">${i}</button>`;
        } else if (i === page - 2 || i === page + 2) {
            html += `<span class="page-dots">...</span>`;
        }
    }

    html += `<button class="page-btn ${page >= totalPages ? 'disabled' : ''}" onclick="goToPage(${page + 1})" ${page >= totalPages ? 'disabled' : ''}>
        <i class="bi bi-chevron-right"></i>
    </button>`;

    container.innerHTML = html;
}

function goToPage(page) {
    currentPage = page;
    loadBlogs();
    document.querySelector('.blogs-container').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ==================== DATE RANGE MODAL ====================

function openDateModal() {
    document.getElementById('dateFrom').value = currentDateFrom;
    document.getElementById('dateTo').value = currentDateTo;
    const modal = new bootstrap.Modal(document.getElementById('dateRangeModal'));
    modal.show();
}

function setDatePreset(preset) {
    const today = new Date();
    const toStr = today.toISOString().split('T')[0];
    let fromStr = '';

    switch (preset) {
        case 'today':
            fromStr = toStr;
            break;
        case 'week':
            const week = new Date(today);
            week.setDate(week.getDate() - 7);
            fromStr = week.toISOString().split('T')[0];
            break;
        case 'month':
            const month = new Date(today);
            month.setDate(month.getDate() - 30);
            fromStr = month.toISOString().split('T')[0];
            break;
        case 'all':
            fromStr = '';
            document.getElementById('dateFrom').value = '';
            document.getElementById('dateTo').value = '';
            return;
    }

    document.getElementById('dateFrom').value = fromStr;
    document.getElementById('dateTo').value = toStr;
}

function applyDateFilter() {
    currentDateFrom = document.getElementById('dateFrom').value;
    currentDateTo = document.getElementById('dateTo').value;
    currentPage = 1;

    const btn = document.getElementById('dateFilterBtn');
    const label = document.getElementById('dateFilterLabel');

    if (currentDateFrom || currentDateTo) {
        btn.classList.add('active');
        const from = currentDateFrom ? formatShortDate(currentDateFrom) : '...';
        const to = currentDateTo ? formatShortDate(currentDateTo) : '...';
        label.textContent = `${from} - ${to}`;
    } else {
        btn.classList.remove('active');
        label.textContent = 'Date Range';
    }

    bootstrap.Modal.getInstance(document.getElementById('dateRangeModal')).hide();
    loadBlogs();
}

function clearDateFilter() {
    document.getElementById('dateFrom').value = '';
    document.getElementById('dateTo').value = '';
    currentDateFrom = '';
    currentDateTo = '';
    currentPage = 1;

    document.getElementById('dateFilterBtn').classList.remove('active');
    document.getElementById('dateFilterLabel').textContent = 'Date Range';

    bootstrap.Modal.getInstance(document.getElementById('dateRangeModal')).hide();
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
